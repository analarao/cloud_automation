#!/usr/bin/env python3
"""
Alert Pipeline - Automatic Alert-to-Remediation Pipeline

This module implements the end-to-end pipeline:
1. Receive alerts from AlertManager webhook
2. Aggregate context (Prometheus, Istio, K8s, Logs)
3. Build rich context as protobuf message
4. Send to LLM orchestrator for autonomous remediation

The protobuf message IS the prompt - it contains all information
the LLM needs to diagnose and fix the issue.

Usage:
    # Run as webhook server
    python alert_pipeline.py
    
    # Test with specific alert
    python alert_pipeline.py --test BookinfoReviewsDown
"""

import os
import sys
import json
import time
import logging
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


from flask import Flask, request, jsonify
from prometheus_api_client import PrometheusConnect
import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator import CBOrchestrator, Alert

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("alert_pipeline")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class PipelineConfig:
    """Configuration for the Alert Pipeline."""
    # Prometheus
    prometheus_url: str = os.getenv(
        "PROMETHEUS_URL", 
        "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090"
    )
    
    # vLLM Orchestrator
    vllm_url: str = os.getenv("CB_MODEL_OPENAI_API_URL", "http://localhost:8000/v1")
    
    # Webhook server
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "9095"))
    webhook_host: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    
    # Query settings
    metrics_lookback_minutes: int = int(os.getenv("METRICS_LOOKBACK_MINUTES", "30"))
    logs_lookback_seconds: int = int(os.getenv("LOGS_LOOKBACK_SECONDS", "60"))
    
    # Processing
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "10"))
    target_namespace: str = os.getenv("TARGET_NAMESPACE", "target-services")


config = PipelineConfig()


# =============================================================================
# Prometheus Query Module with Istio Metrics
# =============================================================================

class IstioPrometheusQuerier:
    """
    Query Prometheus for Istio service mesh metrics.
    Uses PromQL to extract upstream/downstream dependencies.
    """
    
    def __init__(self, url: str):
        self.url = url
        self.client: Optional[PrometheusConnect] = None
        self._connect()
    
    def _connect(self):
        """Initialize Prometheus connection."""
        try:
            self.client = PrometheusConnect(url=self.url, disable_ssl=True)
            logger.info(f"✓ Connected to Prometheus at {self.url}")
        except Exception as e:
            logger.error(f"✗ Failed to connect to Prometheus: {e}")
            self.client = None
    
    def query(self, promql: str) -> List[Dict]:
        """Execute a PromQL query."""
        if not self.client:
            self._connect()
        if not self.client:
            return []
        
        try:
            result = self.client.custom_query(query=promql)
            return result
        except Exception as e:
            logger.error(f"PromQL query failed: {promql[:100]}... Error: {e}")
            return []
    
    # =========================================================================
    # Istio Service Mesh Queries
    # =========================================================================
    
    def get_upstream_dependencies(self, namespace: str, service: str, 
                                   lookback: str = "5m") -> List[Dict]:
        """
        Get services that THIS service calls (upstream dependencies).
        Uses Istio's istio_requests_total metric.
        
        Example: If 'reviews' calls 'ratings', then 'ratings' is upstream of 'reviews'
        """
        # Query for outbound requests FROM this service
        query = f'''
            sum by (destination_service_name, destination_service_namespace) (
                rate(istio_requests_total{{
                    source_workload_namespace="{namespace}",
                    source_app=~"{service}.*",
                    reporter="source"
                }}[{lookback}])
            ) > 0
        '''
        
        results = self.query(query)
        dependencies = []
        
        for result in results:
            metric = result.get("metric", {})
            value = float(result.get("value", [0, 0])[1])
            
            dest_service = metric.get("destination_service_name", "")
            dest_namespace = metric.get("destination_service_namespace", "")
            
            if dest_service and dest_service != "unknown":
                dependencies.append({
                    "service_name": dest_service,
                    "namespace": dest_namespace,
                    "requests_per_second": round(value, 4),
                    "direction": "upstream",
                    "description": f"This service calls {dest_service}"
                })
        
        return dependencies
    
    def get_downstream_dependencies(self, namespace: str, service: str,
                                     lookback: str = "5m") -> List[Dict]:
        """
        Get services that CALL this service (downstream dependencies).
        
        Example: If 'productpage' calls 'reviews', then 'productpage' is downstream of 'reviews'
        """
        # Query for inbound requests TO this service
        query = f'''
            sum by (source_workload, source_workload_namespace, source_app) (
                rate(istio_requests_total{{
                    destination_service_namespace="{namespace}",
                    destination_app=~"{service}.*",
                    reporter="destination"
                }}[{lookback}])
            ) > 0
        '''
        
        results = self.query(query)
        dependencies = []
        
        for result in results:
            metric = result.get("metric", {})
            value = float(result.get("value", [0, 0])[1])
            
            source_workload = metric.get("source_workload", "")
            source_app = metric.get("source_app", "")
            source_namespace = metric.get("source_workload_namespace", "")
            
            source_name = source_app or source_workload
            if source_name and source_name != "unknown":
                dependencies.append({
                    "service_name": source_name,
                    "namespace": source_namespace,
                    "requests_per_second": round(value, 4),
                    "direction": "downstream",
                    "description": f"{source_name} calls this service"
                })
        
        return dependencies
    
    def get_service_error_rate(self, namespace: str, service: str,
                                lookback: str = "5m") -> Dict:
        """Get error rate (5xx) for a service."""
        # Total requests
        total_query = f'''
            sum(rate(istio_requests_total{{
                destination_service_namespace="{namespace}",
                destination_app=~"{service}.*"
            }}[{lookback}]))
        '''
        
        # 5xx errors
        error_query = f'''
            sum(rate(istio_requests_total{{
                destination_service_namespace="{namespace}",
                destination_app=~"{service}.*",
                response_code=~"5.*"
            }}[{lookback}]))
        '''
        
        total_results = self.query(total_query)
        error_results = self.query(error_query)
        
        total = float(total_results[0]["value"][1]) if total_results else 0
        errors = float(error_results[0]["value"][1]) if error_results else 0
        
        error_rate = (errors / total * 100) if total > 0 else 0
        
        return {
            "total_rps": round(total, 4),
            "error_rps": round(errors, 4),
            "error_rate_percent": round(error_rate, 2)
        }
    
    def get_service_latency(self, namespace: str, service: str,
                            lookback: str = "5m") -> Dict:
        """Get latency percentiles for a service."""
        percentiles = {}
        
        for p in ["0.5", "0.9", "0.99"]:
            query = f'''
                histogram_quantile({p}, sum(rate(istio_request_duration_milliseconds_bucket{{
                    destination_service_namespace="{namespace}",
                    destination_app=~"{service}.*"
                }}[{lookback}])) by (le))
            '''
            results = self.query(query)
            if results:
                value = float(results[0]["value"][1])
                percentiles[f"p{int(float(p)*100)}"] = round(value, 2)
        
        return percentiles
    
    def get_pod_restarts(self, namespace: str, pod_pattern: str) -> List[Dict]:
        """Get pod restart counts."""
        query = f'''
            kube_pod_container_status_restarts_total{{
                namespace="{namespace}",
                pod=~"{pod_pattern}.*"
            }}
        '''
        results = self.query(query)
        
        restarts = []
        for result in results:
            metric = result.get("metric", {})
            value = int(float(result.get("value", [0, 0])[1]))
            restarts.append({
                "pod": metric.get("pod", ""),
                "container": metric.get("container", ""),
                "restart_count": value
            })
        
        return restarts
    
    def get_pod_status(self, namespace: str, pod_pattern: str) -> List[Dict]:
        """Get current pod status/phase."""
        query = f'''
            kube_pod_status_phase{{
                namespace="{namespace}",
                pod=~"{pod_pattern}.*"
            }} == 1
        '''
        results = self.query(query)
        
        pods = []
        for result in results:
            metric = result.get("metric", {})
            pods.append({
                "pod": metric.get("pod", ""),
                "phase": metric.get("phase", "Unknown")
            })
        
        return pods
    
    def get_container_cpu(self, namespace: str, pod_pattern: str,
                          lookback: str = "5m") -> List[Dict]:
        """Get container CPU usage."""
        query = f'''
            sum by (pod, container) (
                rate(container_cpu_usage_seconds_total{{
                    namespace="{namespace}",
                    pod=~"{pod_pattern}.*",
                    container!=""
                }}[{lookback}])
            )
        '''
        results = self.query(query)
        
        cpu_usage = []
        for result in results:
            metric = result.get("metric", {})
            value = float(result.get("value", [0, 0])[1])
            cpu_usage.append({
                "pod": metric.get("pod", ""),
                "container": metric.get("container", ""),
                "cpu_cores": round(value, 4)
            })
        
        return cpu_usage
    
    def get_container_memory(self, namespace: str, pod_pattern: str) -> List[Dict]:
        """Get container memory usage."""
        query = f'''
            sum by (pod, container) (
                container_memory_working_set_bytes{{
                    namespace="{namespace}",
                    pod=~"{pod_pattern}.*",
                    container!=""
                }}
            )
        '''
        results = self.query(query)
        
        memory_usage = []
        for result in results:
            metric = result.get("metric", {})
            value = float(result.get("value", [0, 0])[1])
            memory_usage.append({
                "pod": metric.get("pod", ""),
                "container": metric.get("container", ""),
                "memory_bytes": int(value),
                "memory_mb": round(value / 1024 / 1024, 2)
            })
        
        return memory_usage
    
    def get_service_up_status(self, namespace: str, service: str) -> Dict:
        """Check if service endpoints are up."""
        query = f'''
            sum(up{{
                namespace="{namespace}",
                pod=~"{service}.*"
            }})
        '''
        results = self.query(query)
        
        if results:
            up_count = int(float(results[0]["value"][1]))
            return {
                "service": service,
                "namespace": namespace,
                "up_instances": up_count,
                "is_up": up_count > 0
            }
        
        return {
            "service": service,
            "namespace": namespace,
            "up_instances": 0,
            "is_up": False
        }
    
    def get_deployment_replicas(self, namespace: str, deployment: str) -> Dict:
        """Get deployment replica status. Handles versioned deployments (e.g., reviews-v1, reviews-v2)."""
        # Use regex to match versioned deployments (e.g., reviews, reviews-v1, reviews-v2)
        deployment_pattern = f"{deployment}(-v[0-9]+)?"
        
        queries = {
            "desired": f'sum(kube_deployment_spec_replicas{{namespace="{namespace}", deployment=~"{deployment_pattern}"}})',
            "available": f'sum(kube_deployment_status_replicas_available{{namespace="{namespace}", deployment=~"{deployment_pattern}"}})',
            "ready": f'sum(kube_deployment_status_replicas_ready{{namespace="{namespace}", deployment=~"{deployment_pattern}"}})',
            "unavailable": f'sum(kube_deployment_status_replicas_unavailable{{namespace="{namespace}", deployment=~"{deployment_pattern}"}})',
        }
        
        result = {"deployment": deployment, "namespace": namespace, "versions": []}
        
        # Get aggregated totals
        for key, query in queries.items():
            data = self.query(query)
            if data:
                result[key] = int(float(data[0]["value"][1]))
            else:
                result[key] = 0
        
        # Also get individual deployment versions for detail
        version_query = f'kube_deployment_spec_replicas{{namespace="{namespace}", deployment=~"{deployment_pattern}"}}'
        version_data = self.query(version_query)
        for item in version_data:
            metric = item.get("metric", {})
            value = int(float(item.get("value", [0, 0])[1]))
            result["versions"].append({
                "name": metric.get("deployment", ""),
                "replicas": value
            })
        
        return result


# =============================================================================
# Kubernetes Log Fetcher (using kubectl)
# =============================================================================

class KubernetesLogFetcher:
    """Fetch logs from Kubernetes pods using kubectl or API."""
    
    def __init__(self, namespace: str = "target-services"):
        self.namespace = namespace
    
    def get_pod_logs(self, pod_name: str, container: str = None,
                     tail_lines: int = 100, previous: bool = False) -> str:
        """
        Get logs from a pod.
        Falls back to kubectl if K8s API is not available.
        """
        import subprocess
        
        cmd = ["kubectl", "logs", pod_name, "-n", self.namespace]
        
        if container:
            cmd.extend(["-c", container])
        if tail_lines:
            cmd.extend(["--tail", str(tail_lines)])
        if previous:
            cmd.append("--previous")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"kubectl logs failed: {result.stderr}")
                return f"Error getting logs: {result.stderr}"
                
        except subprocess.TimeoutExpired:
            return "Error: Log fetch timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def get_logs_for_service(self, service_pattern: str, 
                             tail_lines: int = 50) -> Dict[str, str]:
        """Get logs from all pods matching a service pattern."""
        import subprocess
        
        # First, list pods matching the pattern
        cmd = [
            "kubectl", "get", "pods", "-n", self.namespace,
            "-l", f"app={service_pattern}",
            "-o", "jsonpath={.items[*].metadata.name}"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            pod_names = result.stdout.strip().split()
            
            logs = {}
            for pod in pod_names[:3]:  # Limit to 3 pods
                logs[pod] = self.get_pod_logs(pod, tail_lines=tail_lines)
            
            return logs
            
        except Exception as e:
            logger.error(f"Failed to get pods: {e}")
            return {}


# =============================================================================
# Alert Context Builder
# =============================================================================

@dataclass
class AlertContext:
    """
    Complete context for an alert - this becomes the LLM prompt.
    All information the LLM needs to diagnose and fix the issue.
    """
    # Alert Identity
    alert_name: str
    alert_fingerprint: str
    severity: str
    status: str  # firing, resolved
    
    # Alert Details
    summary: str
    description: str
    promql_expression: str
    
    # Target Resource
    namespace: str
    service_name: str
    pod_pattern: str
    
    # Current State
    service_status: Dict = field(default_factory=dict)
    deployment_status: Dict = field(default_factory=dict)
    pod_statuses: List[Dict] = field(default_factory=list)
    pod_restarts: List[Dict] = field(default_factory=list)
    
    # Dependencies (from Istio)
    upstream_dependencies: List[Dict] = field(default_factory=list)
    downstream_dependencies: List[Dict] = field(default_factory=list)
    
    # Metrics
    error_rate: Dict = field(default_factory=dict)
    latency: Dict = field(default_factory=dict)
    cpu_usage: List[Dict] = field(default_factory=list)
    memory_usage: List[Dict] = field(default_factory=list)
    
    # Logs
    recent_logs: Dict[str, str] = field(default_factory=dict)
    
    # Timestamps
    started_at: str = ""
    received_at: str = ""
    
    # Labels and Annotations from Alert
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    
    def to_prompt(self) -> str:
        """
        Convert the context to a structured prompt for the LLM.
        This IS the prompt - comprehensive and actionable.
        """
        prompt = f"""
================================================================================
ALERT ANALYSIS REQUEST
================================================================================

ALERT DETAILS:
  Name: {self.alert_name}
  Severity: {self.severity}
  Status: {self.status}
  Started: {self.started_at}
  Fingerprint: {self.alert_fingerprint}

SUMMARY: {self.summary}

DESCRIPTION: {self.description}

PROMQL EXPRESSION: {self.promql_expression}

--------------------------------------------------------------------------------
TARGET RESOURCE
--------------------------------------------------------------------------------
  Namespace: {self.namespace}
  Service: {self.service_name}
  Pod Pattern: {self.pod_pattern}

SERVICE STATUS:
{json.dumps(self.service_status, indent=2)}

DEPLOYMENT STATUS:
{json.dumps(self.deployment_status, indent=2)}

POD STATUSES:
{json.dumps(self.pod_statuses, indent=2)}

POD RESTARTS:
{json.dumps(self.pod_restarts, indent=2)}

--------------------------------------------------------------------------------
SERVICE MESH DEPENDENCIES (from Istio)
--------------------------------------------------------------------------------

UPSTREAM DEPENDENCIES (services this service calls):
{json.dumps(self.upstream_dependencies, indent=2) if self.upstream_dependencies else "  None detected"}

DOWNSTREAM DEPENDENCIES (services that call this service):
{json.dumps(self.downstream_dependencies, indent=2) if self.downstream_dependencies else "  None detected"}

--------------------------------------------------------------------------------
METRICS
--------------------------------------------------------------------------------

ERROR RATE:
{json.dumps(self.error_rate, indent=2)}

LATENCY (ms):
{json.dumps(self.latency, indent=2)}

CPU USAGE:
{json.dumps(self.cpu_usage, indent=2)}

MEMORY USAGE:
{json.dumps(self.memory_usage, indent=2)}

--------------------------------------------------------------------------------
RECENT LOGS (last 60 seconds)
--------------------------------------------------------------------------------
"""
        
        for pod, logs in self.recent_logs.items():
            prompt += f"\n=== {pod} ===\n"
            # Limit log lines
            log_lines = logs.split('\n')[-30:]  # Last 30 lines
            prompt += '\n'.join(log_lines)
            prompt += "\n"
        
        prompt += """
--------------------------------------------------------------------------------
ALERT LABELS
--------------------------------------------------------------------------------
"""
        for k, v in self.labels.items():
            prompt += f"  {k}: {v}\n"
        
        prompt += """
================================================================================
INSTRUCTIONS
================================================================================

Based on the above alert context, please:
1. DIAGNOSE the root cause of the alert
2. INVESTIGATE using kubectl commands to gather more info if needed
3. REMEDIATE by taking appropriate action (scale, restart, patch, etc.)
4. VERIFY the fix worked

Use the available Kubernetes tools to investigate and fix this issue.
When done, end your response with either:
  - "REMEDIATION COMPLETE: <summary of what was done>"
  - "REMEDIATION FAILED: <reason and what was tried>"
================================================================================
"""
        return prompt
    
    def to_alert(self) -> Alert:
        """Convert to Alert object for the orchestrator."""
        return Alert(
            alert_name=self.alert_name,
            severity=self.severity,
            namespace=self.namespace,
            pod_name=self.pod_pattern,
            deployment_name=self.service_name,
            message=self.to_prompt(),  # Full context as message
            labels=self.labels,
            annotations=self.annotations,
        )


class AlertContextAggregator:
    """
    Aggregates all context for an alert from multiple sources:
    - Prometheus (metrics, Istio mesh data)
    - Kubernetes (pod status, events)
    - Logs (kubectl logs)
    """
    
    def __init__(self, prometheus_url: str, namespace: str = "target-services"):
        self.prometheus = IstioPrometheusQuerier(prometheus_url)
        self.log_fetcher = KubernetesLogFetcher(namespace)
        self.namespace = namespace
        self.executor = ThreadPoolExecutor(max_workers=10)
    
    def aggregate_from_alertmanager(self, alert_payload: Dict) -> AlertContext:
        """
        Build complete context from an AlertManager webhook payload.
        
        Args:
            alert_payload: Single alert from AlertManager webhook
            
        Returns:
            AlertContext with all aggregated information
        """
        labels = alert_payload.get("labels", {})
        annotations = alert_payload.get("annotations", {})
        
        # Extract identifiers
        alert_name = labels.get("alertname", "UnknownAlert")
        namespace = labels.get("namespace", self.namespace)
        severity = labels.get("severity", "warning")
        
        # Try to infer service name from various labels
        service_name = (
            labels.get("service") or 
            labels.get("app") or 
            labels.get("deployment") or
            self._infer_service_from_alert(alert_name)
        )
        
        # Pod pattern for queries
        pod_pattern = labels.get("pod", f"{service_name}")
        
        logger.info(f"Aggregating context for alert: {alert_name}")
        logger.info(f"  Namespace: {namespace}, Service: {service_name}")
        
        # Create context object
        context = AlertContext(
            alert_name=alert_name,
            alert_fingerprint=alert_payload.get("fingerprint", ""),
            severity=severity,
            status=alert_payload.get("status", "firing"),
            summary=annotations.get("summary", ""),
            description=annotations.get("description", ""),
            promql_expression=labels.get("promql", alert_payload.get("generatorURL", "")),
            namespace=namespace,
            service_name=service_name,
            pod_pattern=pod_pattern,
            started_at=alert_payload.get("startsAt", ""),
            received_at=datetime.now().isoformat(),
            labels=labels,
            annotations=annotations,
        )
        
        # Parallel data gathering
        futures = {
            "upstream": self.executor.submit(
                self.prometheus.get_upstream_dependencies, namespace, service_name
            ),
            "downstream": self.executor.submit(
                self.prometheus.get_downstream_dependencies, namespace, service_name
            ),
            "error_rate": self.executor.submit(
                self.prometheus.get_service_error_rate, namespace, service_name
            ),
            "latency": self.executor.submit(
                self.prometheus.get_service_latency, namespace, service_name
            ),
            "service_status": self.executor.submit(
                self.prometheus.get_service_up_status, namespace, service_name
            ),
            "pod_status": self.executor.submit(
                self.prometheus.get_pod_status, namespace, pod_pattern
            ),
            "pod_restarts": self.executor.submit(
                self.prometheus.get_pod_restarts, namespace, pod_pattern
            ),
            "cpu": self.executor.submit(
                self.prometheus.get_container_cpu, namespace, pod_pattern
            ),
            "memory": self.executor.submit(
                self.prometheus.get_container_memory, namespace, pod_pattern
            ),
            "logs": self.executor.submit(
                self.log_fetcher.get_logs_for_service, service_name
            ),
        }
        
        # Also try to get deployment status
        deployment_name = service_name.replace("-v1", "").replace("-v2", "").replace("-v3", "")
        futures["deployment"] = self.executor.submit(
            self.prometheus.get_deployment_replicas, namespace, deployment_name
        )
        
        # Wait for all futures
        for key, future in futures.items():
            try:
                result = future.result(timeout=30)
                
                if key == "upstream":
                    context.upstream_dependencies = result
                elif key == "downstream":
                    context.downstream_dependencies = result
                elif key == "error_rate":
                    context.error_rate = result
                elif key == "latency":
                    context.latency = result
                elif key == "service_status":
                    context.service_status = result
                elif key == "deployment":
                    context.deployment_status = result
                elif key == "pod_status":
                    context.pod_statuses = result
                elif key == "pod_restarts":
                    context.pod_restarts = result
                elif key == "cpu":
                    context.cpu_usage = result
                elif key == "memory":
                    context.memory_usage = result
                elif key == "logs":
                    context.recent_logs = result
                    
            except Exception as e:
                logger.warning(f"Failed to get {key}: {e}")
        
        logger.info(f"Context aggregation complete for {alert_name}")
        return context
    
    def _infer_service_from_alert(self, alert_name: str) -> str:
        """Try to infer service name from alert name."""
        # Map known alerts to services
        alert_service_map = {
            "BookinfoReviewsDown": "reviews",
            "BookinfoProductpageDown": "productpage",
            "BookinfoRatingsDown": "ratings",
            "BookinfoDetailsDown": "details",
        }
        return alert_service_map.get(alert_name, "unknown")


# =============================================================================
# Alert Pipeline - Connects AlertManager to LLM Orchestrator
# =============================================================================

class AlertPipeline:
    """
    Main pipeline that:
    1. Receives alerts from AlertManager
    2. Aggregates context
    3. Sends to LLM orchestrator for remediation
    """
    
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.aggregator = AlertContextAggregator(
            prometheus_url=config.prometheus_url,
            namespace=config.target_namespace
        )
        self.orchestrator = CBOrchestrator(
            vllm_base_url=config.vllm_url,
            target_namespace=config.target_namespace,
            max_iterations=config.max_iterations
        )
        
        logger.info("Alert Pipeline initialized")
        logger.info(f"  Prometheus: {config.prometheus_url}")
        logger.info(f"  vLLM: {config.vllm_url}")
        logger.info(f"  Namespace: {config.target_namespace}")
    
    def process_alert(self, alert_payload: Dict) -> Dict:
        """
        Process a single alert through the full pipeline.
        
        Args:
            alert_payload: Alert from AlertManager
            
        Returns:
            Processing result with remediation status
        """
        alert_name = alert_payload.get("labels", {}).get("alertname", "unknown")
        logger.info(f"=" * 60)
        logger.info(f"Processing alert: {alert_name}")
        logger.info(f"=" * 60)
        
        start_time = time.time()
        
        try:
            # Step 1: Aggregate context
            logger.info("Step 1: Aggregating context...")
            context = self.aggregator.aggregate_from_alertmanager(alert_payload)
            
            # Step 2: Convert to Alert and process
            logger.info("Step 2: Sending to LLM orchestrator...")
            alert = context.to_alert()
            result = self.orchestrator.process_alert(alert)
            
            elapsed = time.time() - start_time
            
            # Step 3: Return result
            return {
                "alert_name": alert_name,
                "status": "remediated" if result.success else "failed",
                "success": result.success,
                "iterations": result.iterations,
                "actions_taken": len(result.actions_taken),
                "final_response": result.final_response[:1000],
                "elapsed_seconds": round(elapsed, 2),
                "context_summary": {
                    "upstream_deps": len(context.upstream_dependencies),
                    "downstream_deps": len(context.downstream_dependencies),
                    "pods_found": len(context.pod_statuses),
                    "logs_collected": len(context.recent_logs),
                }
            }
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "alert_name": alert_name,
                "status": "error",
                "success": False,
                "error": str(e),
            }


# =============================================================================
# Flask Webhook Server
# =============================================================================

app = Flask(__name__)
pipeline: Optional[AlertPipeline] = None


def init_pipeline():
    """Initialize the alert pipeline."""
    global pipeline
    pipeline = AlertPipeline(config)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "prometheus_url": config.prometheus_url,
        "vllm_url": config.vllm_url,
        "namespace": config.target_namespace,
    })


@app.route("/webhook", methods=["POST"])
def alertmanager_webhook():
    """
    AlertManager webhook receiver.
    
    Receives alerts and processes them through the pipeline.
    """
    try:
        payload = request.json
        alerts = payload.get("alerts", [])
        
        logger.info(f"Received {len(alerts)} alert(s) from AlertManager")
        
        results = []
        for alert in alerts:
            # Only process firing alerts
            if alert.get("status") != "firing":
                logger.info(f"Skipping non-firing alert: {alert.get('labels', {}).get('alertname')}")
                continue
            
            result = pipeline.process_alert(alert)
            results.append(result)
        
        return jsonify({
            "status": "processed",
            "alerts_processed": len(results),
            "results": results
        })
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/test", methods=["POST"])
def test_endpoint():
    """Test endpoint to manually submit an alert."""
    try:
        alert = request.json
        result = pipeline.process_alert(alert)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/simulate/<alert_name>", methods=["POST"])
def simulate_alert(alert_name: str):
    """
    Simulate a specific alert for testing.
    
    Usage: POST /simulate/BookinfoReviewsDown
    """
    # Predefined test alerts
    test_alerts = {
        "BookinfoReviewsDown": {
            "status": "firing",
            "labels": {
                "alertname": "BookinfoReviewsDown",
                "severity": "critical",
                "namespace": "target-services",
                "service": "reviews",
                "app": "reviews",
            },
            "annotations": {
                "summary": "Bookinfo 'reviews' service is down",
                "description": "No instances of the 'reviews' service in the 'target-services' namespace are reachable."
            },
            "startsAt": datetime.now().isoformat(),
            "fingerprint": hashlib.md5(f"{alert_name}-{time.time()}".encode()).hexdigest(),
        },
        "HighCPUUsage": {
            "status": "firing",
            "labels": {
                "alertname": "HighCPUUsage",
                "severity": "warning",
                "namespace": "target-services",
                "service": "productpage",
                "app": "productpage",
            },
            "annotations": {
                "summary": "High CPU usage detected",
                "description": "CPU usage has exceeded 90% for more than 5 minutes"
            },
            "startsAt": datetime.now().isoformat(),
            "fingerprint": hashlib.md5(f"{alert_name}-{time.time()}".encode()).hexdigest(),
        }
    }
    
    if alert_name not in test_alerts:
        return jsonify({
            "error": f"Unknown alert: {alert_name}",
            "available": list(test_alerts.keys())
        }), 404
    
    result = pipeline.process_alert(test_alerts[alert_name])
    return jsonify(result)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Alert Pipeline - AlertManager to LLM Remediation")
    parser.add_argument(
        "--test",
        type=str,
        help="Run a test with specified alert name (e.g., BookinfoReviewsDown)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.webhook_port,
        help="Webhook server port"
    )
    parser.add_argument(
        "--prometheus-url",
        type=str,
        default=config.prometheus_url,
        help="Prometheus URL"
    )
    parser.add_argument(
        "--vllm-url",
        type=str,
        default=config.vllm_url,
        help="vLLM URL"
    )
    parser.add_argument(
        "--namespace",
        type=str,
        default=config.target_namespace,
        help="Target Kubernetes namespace"
    )
    
    args = parser.parse_args()
    
    # Update config from args
    config.webhook_port = args.port
    config.prometheus_url = args.prometheus_url
    config.vllm_url = args.vllm_url
    config.target_namespace = args.namespace
    
    # Initialize pipeline
    init_pipeline()
    
    if args.test:
        # Run test mode
        logger.info(f"Running test for alert: {args.test}")
        
        # Create test alert
        test_alert = {
            "status": "firing",
            "labels": {
                "alertname": args.test,
                "severity": "critical",
                "namespace": config.target_namespace,
            },
            "annotations": {
                "summary": f"Test alert: {args.test}",
                "description": f"This is a test of the {args.test} alert"
            },
            "startsAt": datetime.now().isoformat(),
            "fingerprint": hashlib.md5(f"{args.test}-{time.time()}".encode()).hexdigest(),
        }
        
        result = pipeline.process_alert(test_alert)
        print(json.dumps(result, indent=2))
        
    else:
        # Run webhook server
        logger.info("=" * 60)
        logger.info("Alert Pipeline - Starting Webhook Server")
        logger.info("=" * 60)
        logger.info(f"Prometheus: {config.prometheus_url}")
        logger.info(f"vLLM: {config.vllm_url}")
        logger.info(f"Namespace: {config.target_namespace}")
        logger.info(f"Webhook Port: {config.webhook_port}")
        logger.info("=" * 60)
        
        app.run(host=config.webhook_host, port=config.webhook_port)


if __name__ == "__main__":
    main()
