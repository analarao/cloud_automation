#!/usr/bin/env python3
"""
Alert Context Aggregator for CB (Container-Brain) Model

This service acts as:
1. AlertManager webhook receiver - receives alerts
2. Context aggregator - queries Prometheus, Kiali, K8s API for context
3. gRPC client - forwards enriched context to CB Model LLM

Flow:
AlertManager -> Webhook -> Aggregator -> [Prometheus, Kiali, K8s] -> gRPC -> CB Model LLM

Author: Cloud Automation Team
"""

import os
import sys
import json
import time
import logging
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

import grpc
from flask import Flask, request, jsonify
from prometheus_api_client import PrometheusConnect

# Import the generated protobuf modules
try:
    import cb_model_v2_pb2 as pb2
    import cb_model_v2_pb2_grpc as pb2_grpc
except ImportError:
    # Fall back to original proto if v2 not compiled yet
    import cb_model_pb2 as pb2
    import cb_model_pb2_grpc as pb2_grpc

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("alert_aggregator")

# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Config:
    """Configuration for the Alert Context Aggregator."""
    # Service endpoints
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090")
    alertmanager_url: str = os.getenv("ALERTMANAGER_URL", "http://prometheus-kube-prometheus-alertmanager.monitoring.svc.cluster.local:9093")
    kiali_url: str = os.getenv("KIALI_URL", "http://kiali.istio-system.svc.cluster.local:20001")
    loki_url: str = os.getenv("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100")
    cb_model_host: str = os.getenv("CB_MODEL_HOST", "cb-model-service.monitoring.svc.cluster.local")
    cb_model_port: int = int(os.getenv("CB_MODEL_PORT", "50051"))
    
    # Kubernetes API (use in-cluster config or explicit URL)
    k8s_api_url: str = os.getenv("K8S_API_URL", "https://kubernetes.default.svc")
    k8s_token_path: str = os.getenv("K8S_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token")
    k8s_ca_path: str = os.getenv("K8S_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    
    # Webhook server
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "9095"))
    webhook_host: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    
    # Query settings
    metrics_lookback_minutes: int = int(os.getenv("METRICS_LOOKBACK_MINUTES", "30"))
    logs_lookback_minutes: int = int(os.getenv("LOGS_LOOKBACK_MINUTES", "15"))
    max_log_lines: int = int(os.getenv("MAX_LOG_LINES", "100"))
    
    # Timeouts
    grpc_timeout_seconds: int = int(os.getenv("GRPC_TIMEOUT_SECONDS", "120"))
    query_timeout_seconds: int = int(os.getenv("QUERY_TIMEOUT_SECONDS", "30"))


config = Config()

# =============================================================================
# Prometheus Query Module
# =============================================================================

class PrometheusQuerier:
    """Query Prometheus for metrics and metadata."""
    
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
    
    def query_range(self, promql: str, start_time: datetime, end_time: datetime, step: str = "1m") -> List[Dict]:
        """Execute a range query."""
        if not self.client:
            self._connect()
        if not self.client:
            return []
        
        try:
            result = self.client.custom_query_range(
                query=promql,
                start_time=start_time,
                end_time=end_time,
                step=step
            )
            return result
        except Exception as e:
            logger.error(f"PromQL range query failed: {e}")
            return []
    
    def get_container_info(self, namespace: str, pod: str, container: str = None) -> Dict:
        """Get container metadata from cAdvisor metrics."""
        queries = {
            "image": f'container_last_seen{{namespace="{namespace}", pod="{pod}"}}',
            "cpu_limit": f'container_spec_cpu_quota{{namespace="{namespace}", pod="{pod}"}}',
            "memory_limit": f'container_spec_memory_limit_bytes{{namespace="{namespace}", pod="{pod}"}}',
            "restarts": f'kube_pod_container_status_restarts_total{{namespace="{namespace}", pod="{pod}"}}',
        }
        
        result = {}
        for key, query in queries.items():
            data = self.query(query)
            if data:
                result[key] = data[0] if len(data) == 1 else data
        
        return result
    
    def get_cpu_usage(self, namespace: str, pod: str, lookback_minutes: int = 30) -> Dict:
        """Get CPU usage time series."""
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=lookback_minutes)
        
        query = f'rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod="{pod}", container!=""}}[5m])'
        data = self.query_range(query, start_time, end_time)
        
        if not data:
            return {}
        
        # Parse the first series
        series = data[0] if data else {}
        values = series.get("values", [])
        
        if not values:
            return {}
        
        float_values = [float(v[1]) for v in values]
        return {
            "name": "cpu_usage",
            "unit": "cores",
            "labels": series.get("metric", {}),
            "values": [{"timestamp": int(v[0]), "value": float(v[1])} for v in values],
            "current": float_values[-1] if float_values else 0,
            "avg": sum(float_values) / len(float_values) if float_values else 0,
            "max": max(float_values) if float_values else 0,
            "min": min(float_values) if float_values else 0,
        }
    
    def get_memory_usage(self, namespace: str, pod: str, lookback_minutes: int = 30) -> Dict:
        """Get memory usage time series."""
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=lookback_minutes)
        
        query = f'container_memory_working_set_bytes{{namespace="{namespace}", pod="{pod}", container!=""}}'
        data = self.query_range(query, start_time, end_time)
        
        if not data:
            return {}
        
        series = data[0] if data else {}
        values = series.get("values", [])
        
        if not values:
            return {}
        
        float_values = [float(v[1]) for v in values]
        return {
            "name": "memory_usage",
            "unit": "bytes",
            "labels": series.get("metric", {}),
            "values": [{"timestamp": int(v[0]), "value": float(v[1])} for v in values],
            "current": float_values[-1] if float_values else 0,
            "avg": sum(float_values) / len(float_values) if float_values else 0,
            "max": max(float_values) if float_values else 0,
            "min": min(float_values) if float_values else 0,
        }
    
    def get_network_metrics(self, namespace: str, pod: str, lookback_minutes: int = 30) -> Dict:
        """Get network RX/TX metrics."""
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=lookback_minutes)
        
        result = {}
        for direction, metric in [("rx", "container_network_receive_bytes_total"), 
                                   ("tx", "container_network_transmit_bytes_total")]:
            query = f'rate({metric}{{namespace="{namespace}", pod="{pod}"}}[5m])'
            data = self.query_range(query, start_time, end_time)
            
            if data:
                series = data[0]
                values = series.get("values", [])
                float_values = [float(v[1]) for v in values]
                result[direction] = {
                    "name": f"network_{direction}_bytes",
                    "unit": "bytes/sec",
                    "current": float_values[-1] if float_values else 0,
                    "avg": sum(float_values) / len(float_values) if float_values else 0,
                }
        
        return result
    
    def get_pod_labels(self, namespace: str, pod: str) -> Dict[str, str]:
        """Get pod labels from kube-state-metrics."""
        query = f'kube_pod_labels{{namespace="{namespace}", pod="{pod}"}}'
        data = self.query(query)
        
        if not data:
            return {}
        
        # Extract labels from metric labels
        metric = data[0].get("metric", {})
        labels = {k.replace("label_", ""): v for k, v in metric.items() 
                  if k.startswith("label_")}
        return labels
    
    def get_service_for_pod(self, namespace: str, pod_labels: Dict[str, str]) -> List[Dict]:
        """Find services that select this pod."""
        services = []
        
        # Query for services with matching selectors
        for label_key, label_value in pod_labels.items():
            query = f'kube_service_spec_selector{{namespace="{namespace}", label_{label_key}="{label_value}"}}'
            data = self.query(query)
            
            for item in data:
                metric = item.get("metric", {})
                svc_name = metric.get("service", "")
                if svc_name and svc_name not in [s.get("name") for s in services]:
                    services.append({
                        "name": svc_name,
                        "namespace": namespace,
                    })
        
        return services


# =============================================================================
# Kiali/Istio Query Module
# =============================================================================

class KialiQuerier:
    """Query Kiali API for service mesh topology and dependencies."""
    
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.session = None
        self._init_session()
    
    def _init_session(self):
        """Initialize HTTP session with auth if needed."""
        import requests
        self.session = requests.Session()
        # Add authentication if configured
        kiali_user = os.getenv("KIALI_USERNAME", "")
        kiali_pass = os.getenv("KIALI_PASSWORD", "")
        if kiali_user and kiali_pass:
            self.session.auth = (kiali_user, kiali_pass)
    
    def get_service_graph(self, namespace: str, service: str) -> Dict:
        """Get service dependency graph from Kiali."""
        try:
            # Kiali graph API endpoint
            url = f"{self.url}/kiali/api/namespaces/{namespace}/services/{service}/graph"
            params = {
                "duration": "300s",  # Last 5 minutes
                "graphType": "service",
                "includeIdleEdges": "false",
            }
            
            response = self.session.get(url, params=params, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get Kiali service graph: {e}")
            return {}
    
    def get_workload_graph(self, namespace: str) -> Dict:
        """Get workload dependency graph for a namespace."""
        try:
            url = f"{self.url}/kiali/api/namespaces/{namespace}/graph"
            params = {
                "duration": "300s",
                "graphType": "workload",
            }
            
            response = self.session.get(url, params=params, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get Kiali workload graph: {e}")
            return {}
    
    def parse_dependencies(self, graph_data: Dict, service_name: str) -> Dict:
        """Parse upstream and downstream dependencies from graph data."""
        upstream = []
        downstream = []
        
        if not graph_data:
            return {"upstream": upstream, "downstream": downstream}
        
        elements = graph_data.get("elements", {})
        nodes = {n.get("data", {}).get("id"): n.get("data", {}) 
                 for n in elements.get("nodes", [])}
        edges = elements.get("edges", [])
        
        # Find the node ID for our service
        target_node_id = None
        for node_id, node_data in nodes.items():
            if node_data.get("service") == service_name or node_data.get("workload") == service_name:
                target_node_id = node_id
                break
        
        if not target_node_id:
            return {"upstream": upstream, "downstream": downstream}
        
        for edge in edges:
            edge_data = edge.get("data", {})
            source_id = edge_data.get("source")
            target_id = edge_data.get("target")
            
            traffic = edge_data.get("traffic", {})
            
            if target_id == target_node_id:
                # This is a downstream dependency (something calling us)
                source_node = nodes.get(source_id, {})
                downstream.append({
                    "service_name": source_node.get("service") or source_node.get("workload", "unknown"),
                    "namespace": source_node.get("namespace", ""),
                    "requests_per_second": traffic.get("rates", {}).get("http", 0),
                    "error_rate": traffic.get("rates", {}).get("httpPercentErr", 0),
                    "health_status": "healthy" if traffic.get("rates", {}).get("httpPercentErr", 0) < 5 else "degraded",
                })
            
            if source_id == target_node_id:
                # This is an upstream dependency (something we call)
                target_node = nodes.get(target_id, {})
                upstream.append({
                    "service_name": target_node.get("service") or target_node.get("workload", "unknown"),
                    "namespace": target_node.get("namespace", ""),
                    "requests_per_second": traffic.get("rates", {}).get("http", 0),
                    "error_rate": traffic.get("rates", {}).get("httpPercentErr", 0),
                    "health_status": "healthy" if traffic.get("rates", {}).get("httpPercentErr", 0) < 5 else "degraded",
                })
        
        return {"upstream": upstream, "downstream": downstream}
    
    def get_istio_config(self, namespace: str, service: str) -> Dict:
        """Get Istio configuration (VirtualServices, DestinationRules)."""
        try:
            # Get VirtualServices
            vs_url = f"{self.url}/kiali/api/namespaces/{namespace}/istio/virtualservices"
            response = self.session.get(vs_url, timeout=config.query_timeout_seconds)
            virtual_services = response.json() if response.ok else []
            
            # Get DestinationRules
            dr_url = f"{self.url}/kiali/api/namespaces/{namespace}/istio/destinationrules"
            response = self.session.get(dr_url, timeout=config.query_timeout_seconds)
            destination_rules = response.json() if response.ok else []
            
            return {
                "virtual_services": virtual_services,
                "destination_rules": destination_rules,
            }
        except Exception as e:
            logger.warning(f"Failed to get Istio config: {e}")
            return {"virtual_services": [], "destination_rules": []}


# =============================================================================
# Kubernetes API Query Module
# =============================================================================

class KubernetesQuerier:
    """Query Kubernetes API for pod, deployment, service info."""
    
    def __init__(self):
        self.api_url = config.k8s_api_url
        self.token = self._load_token()
        self.session = None
        self._init_session()
    
    def _load_token(self) -> str:
        """Load service account token."""
        try:
            with open(config.k8s_token_path, "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            # Running outside cluster, try KUBECONFIG
            return os.getenv("K8S_TOKEN", "")
    
    def _init_session(self):
        """Initialize HTTP session."""
        import requests
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        
        # Use CA cert if available
        if os.path.exists(config.k8s_ca_path):
            self.session.verify = config.k8s_ca_path
        else:
            self.session.verify = False
    
    def get_pod(self, namespace: str, pod_name: str) -> Dict:
        """Get pod details."""
        try:
            url = f"{self.api_url}/api/v1/namespaces/{namespace}/pods/{pod_name}"
            response = self.session.get(url, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get pod {namespace}/{pod_name}: {e}")
            return {}
    
    def get_deployment(self, namespace: str, deployment_name: str) -> Dict:
        """Get deployment details."""
        try:
            url = f"{self.api_url}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
            response = self.session.get(url, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get deployment {namespace}/{deployment_name}: {e}")
            return {}
    
    def get_events(self, namespace: str, involved_object_name: str, limit: int = 10) -> List[Dict]:
        """Get recent events for an object."""
        try:
            url = f"{self.api_url}/api/v1/namespaces/{namespace}/events"
            params = {"fieldSelector": f"involvedObject.name={involved_object_name}"}
            response = self.session.get(url, params=params, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            events = response.json().get("items", [])
            
            # Sort by last timestamp and limit
            events.sort(key=lambda e: e.get("lastTimestamp", ""), reverse=True)
            return events[:limit]
        except Exception as e:
            logger.warning(f"Failed to get events for {namespace}/{involved_object_name}: {e}")
            return []
    
    def get_service(self, namespace: str, service_name: str) -> Dict:
        """Get service details."""
        try:
            url = f"{self.api_url}/api/v1/namespaces/{namespace}/services/{service_name}"
            response = self.session.get(url, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get service {namespace}/{service_name}: {e}")
            return {}
    
    def get_namespace(self, namespace: str) -> Dict:
        """Get namespace details."""
        try:
            url = f"{self.api_url}/api/v1/namespaces/{namespace}"
            response = self.session.get(url, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get namespace {namespace}: {e}")
            return {}
    
    def get_node(self, node_name: str) -> Dict:
        """Get node details."""
        try:
            url = f"{self.api_url}/api/v1/nodes/{node_name}"
            response = self.session.get(url, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get node {node_name}: {e}")
            return {}


# =============================================================================
# Loki Log Query Module
# =============================================================================

class LokiQuerier:
    """Query Loki for container logs."""
    
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.session = None
        self._init_session()
    
    def _init_session(self):
        """Initialize HTTP session."""
        import requests
        self.session = requests.Session()
    
    def query_logs(self, namespace: str, pod: str, container: str = None, 
                   limit: int = 100, lookback_minutes: int = 15) -> List[Dict]:
        """Query logs from Loki."""
        try:
            # Build LogQL query
            label_selectors = [f'namespace="{namespace}"', f'pod="{pod}"']
            if container:
                label_selectors.append(f'container="{container}"')
            
            logql = '{' + ','.join(label_selectors) + '}'
            
            # Calculate time range
            end_time = datetime.now()
            start_time = end_time - timedelta(minutes=lookback_minutes)
            
            url = f"{self.url}/loki/api/v1/query_range"
            params = {
                "query": logql,
                "start": int(start_time.timestamp() * 1e9),  # nanoseconds
                "end": int(end_time.timestamp() * 1e9),
                "limit": limit,
            }
            
            response = self.session.get(url, params=params, timeout=config.query_timeout_seconds)
            response.raise_for_status()
            
            data = response.json()
            logs = []
            
            for stream in data.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for value in stream.get("values", []):
                    timestamp_ns, message = value
                    logs.append({
                        "timestamp": int(timestamp_ns) // 1000000,  # Convert to ms
                        "message": message,
                        "labels": labels,
                        "level": self._detect_log_level(message),
                        "container": labels.get("container", ""),
                        "pod": labels.get("pod", ""),
                    })
            
            # Sort by timestamp
            logs.sort(key=lambda x: x["timestamp"], reverse=True)
            return logs[:limit]
            
        except Exception as e:
            logger.warning(f"Failed to query Loki logs: {e}")
            return []
    
    def _detect_log_level(self, message: str) -> str:
        """Detect log level from message content."""
        message_lower = message.lower()
        if "error" in message_lower or "exception" in message_lower or "fatal" in message_lower:
            return "ERROR"
        elif "warn" in message_lower:
            return "WARN"
        elif "debug" in message_lower:
            return "DEBUG"
        return "INFO"
    
    def get_error_logs(self, namespace: str, pod: str, limit: int = 50) -> List[Dict]:
        """Get only error logs."""
        logs = self.query_logs(namespace, pod, limit=limit * 2)
        return [log for log in logs if log["level"] == "ERROR"][:limit]


# =============================================================================
# Alert Context Aggregator
# =============================================================================

class AlertContextAggregator:
    """
    Main aggregator that collects context from all sources and builds
    the complete alert analysis request.
    """
    
    def __init__(self):
        self.prometheus = PrometheusQuerier(config.prometheus_url)
        self.kiali = KialiQuerier(config.kiali_url)
        self.k8s = KubernetesQuerier()
        self.loki = LokiQuerier(config.loki_url)
        self.executor = ThreadPoolExecutor(max_workers=10)
    
    def aggregate_context(self, alert: Dict) -> pb2.AlertAnalysisRequest:
        """
        Aggregate all context for an alert into a protobuf request.
        
        Args:
            alert: Alert payload from AlertManager webhook
            
        Returns:
            AlertAnalysisRequest protobuf message
        """
        request_id = self._generate_request_id(alert)
        logger.info(f"[{request_id}] Aggregating context for alert: {alert.get('labels', {}).get('alertname', 'unknown')}")
        
        # Extract basic info from alert
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        namespace = labels.get("namespace", "default")
        pod = labels.get("pod", "")
        container = labels.get("container", "")
        service = labels.get("service", "")
        
        # Parallel context gathering
        futures = {}
        
        if pod:
            futures["pod"] = self.executor.submit(self.k8s.get_pod, namespace, pod)
            futures["events"] = self.executor.submit(self.k8s.get_events, namespace, pod)
            futures["cpu"] = self.executor.submit(self.prometheus.get_cpu_usage, namespace, pod)
            futures["memory"] = self.executor.submit(self.prometheus.get_memory_usage, namespace, pod)
            futures["network"] = self.executor.submit(self.prometheus.get_network_metrics, namespace, pod)
            futures["logs"] = self.executor.submit(self.loki.query_logs, namespace, pod, container)
            futures["error_logs"] = self.executor.submit(self.loki.get_error_logs, namespace, pod)
        
        if service or pod:
            service_name = service or self._infer_service_name(pod)
            futures["graph"] = self.executor.submit(self.kiali.get_service_graph, namespace, service_name)
            futures["istio_config"] = self.executor.submit(self.kiali.get_istio_config, namespace, service_name)
        
        futures["namespace"] = self.executor.submit(self.k8s.get_namespace, namespace)
        
        # Wait for all futures
        results = {}
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=config.query_timeout_seconds)
            except Exception as e:
                logger.warning(f"[{request_id}] Failed to get {key}: {e}")
                results[key] = None
        
        # Build the protobuf request
        request = pb2.AlertAnalysisRequest()
        request.request_id = request_id
        request.source = "alertmanager"
        request.alert_received_timestamp = int(time.time() * 1000)
        
        # Build AlertInfo
        self._build_alert_info(request.alert, alert)
        
        # Build ContainerContext
        if results.get("pod"):
            self._build_container_context(request.container, results["pod"], container)
        
        # Build KubernetesContext
        self._build_kubernetes_context(request.kubernetes, results, namespace, pod)
        
        # Build ServiceMeshContext
        if results.get("graph"):
            self._build_service_mesh_context(
                request.service_mesh,
                results.get("graph"),
                results.get("istio_config"),
                service or self._infer_service_name(pod),
                namespace
            )
        
        # Build LogContext
        if results.get("logs"):
            self._build_log_context(request.logs, results.get("logs", []), results.get("error_logs", []))
        
        # Build MetricsContext
        self._build_metrics_context(request.metrics, results)
        
        logger.info(f"[{request_id}] Context aggregation complete")
        return request
    
    def _generate_request_id(self, alert: Dict) -> str:
        """Generate a unique request ID."""
        fingerprint = alert.get("fingerprint", str(time.time()))
        return f"cb-{hashlib.sha256(fingerprint.encode()).hexdigest()[:12]}"
    
    def _infer_service_name(self, pod_name: str) -> str:
        """Infer service name from pod name."""
        # Pod names are usually: <deployment>-<replicaset-hash>-<pod-hash>
        parts = pod_name.rsplit("-", 2)
        if len(parts) >= 3:
            return parts[0]
        return pod_name
    
    def _build_alert_info(self, alert_info: pb2.AlertInfo, alert: Dict):
        """Build AlertInfo protobuf from alert dict."""
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        alert_info.alert_name = labels.get("alertname", "unknown")
        alert_info.severity = labels.get("severity", "warning")
        alert_info.state = alert.get("status", "firing")
        alert_info.summary = annotations.get("summary", "")
        alert_info.description = annotations.get("description", "")
        alert_info.fingerprint = alert.get("fingerprint", "")
        alert_info.generator_url = alert.get("generatorURL", "")
        
        # Parse timestamps
        if alert.get("startsAt"):
            try:
                dt = datetime.fromisoformat(alert["startsAt"].replace("Z", "+00:00"))
                alert_info.starts_at = int(dt.timestamp() * 1000)
            except:
                pass
        
        if alert.get("endsAt"):
            try:
                dt = datetime.fromisoformat(alert["endsAt"].replace("Z", "+00:00"))
                alert_info.ends_at = int(dt.timestamp() * 1000)
            except:
                pass
        
        # Copy labels and annotations
        for k, v in labels.items():
            alert_info.labels[k] = v
        for k, v in annotations.items():
            alert_info.annotations[k] = v
    
    def _build_container_context(self, container_ctx: pb2.ContainerContext, 
                                  pod_data: Dict, container_name: str):
        """Build ContainerContext from K8s pod data."""
        spec = pod_data.get("spec", {})
        status = pod_data.get("status", {})
        
        # Find the container
        containers = spec.get("containers", [])
        container_statuses = status.get("containerStatuses", [])
        
        target_container = None
        target_status = None
        
        for c in containers:
            if not container_name or c.get("name") == container_name:
                target_container = c
                break
        
        for cs in container_statuses:
            if not container_name or cs.get("name") == container_name:
                target_status = cs
                break
        
        if not target_container:
            return
        
        container_ctx.container_name = target_container.get("name", "")
        container_ctx.image_name = target_container.get("image", "")
        
        if target_status:
            container_ctx.container_id = target_status.get("containerID", "").replace("containerd://", "")
            container_ctx.image_hash = target_status.get("imageID", "").split("@")[-1] if "@" in target_status.get("imageID", "") else ""
            container_ctx.restart_count = target_status.get("restartCount", 0)
            
            # State
            state = target_status.get("state", {})
            if "running" in state:
                container_ctx.state = "running"
            elif "waiting" in state:
                container_ctx.state = "waiting"
                container_ctx.last_termination_reason = state["waiting"].get("reason", "")
            elif "terminated" in state:
                container_ctx.state = "terminated"
                container_ctx.last_termination_reason = state["terminated"].get("reason", "")
        
        # Resources
        resources = target_container.get("resources", {})
        limits = resources.get("limits", {})
        requests = resources.get("requests", {})
        
        container_ctx.resource_limits.cpu = limits.get("cpu", "")
        container_ctx.resource_limits.memory = limits.get("memory", "")
        container_ctx.resource_requests.cpu = requests.get("cpu", "")
        container_ctx.resource_requests.memory = requests.get("memory", "")
        
        # Ports
        for port in target_container.get("ports", []):
            p = container_ctx.ports.add()
            p.name = port.get("name", "")
            p.container_port = port.get("containerPort", 0)
            p.protocol = port.get("protocol", "TCP")
    
    def _build_kubernetes_context(self, k8s_ctx: pb2.KubernetesContext, 
                                   results: Dict, namespace: str, pod_name: str):
        """Build KubernetesContext from gathered results."""
        # Namespace
        ns_data = results.get("namespace", {})
        if ns_data:
            k8s_ctx.namespace.name = ns_data.get("metadata", {}).get("name", namespace)
            k8s_ctx.namespace.status = ns_data.get("status", {}).get("phase", "")
            for k, v in ns_data.get("metadata", {}).get("labels", {}).items():
                k8s_ctx.namespace.labels[k] = v
        
        # Pod
        pod_data = results.get("pod", {})
        if pod_data:
            metadata = pod_data.get("metadata", {})
            status = pod_data.get("status", {})
            
            k8s_ctx.pod.name = metadata.get("name", pod_name)
            k8s_ctx.pod.uid = metadata.get("uid", "")
            k8s_ctx.pod.phase = status.get("phase", "")
            k8s_ctx.pod.pod_ip = status.get("podIP", "")
            k8s_ctx.pod.host_ip = status.get("hostIP", "")
            k8s_ctx.pod.qos_class = status.get("qosClass", "")
            
            for k, v in metadata.get("labels", {}).items():
                k8s_ctx.pod.labels[k] = v
            for k, v in metadata.get("annotations", {}).items():
                k8s_ctx.pod.annotations[k] = v
            
            # Conditions
            for cond in status.get("conditions", []):
                c = k8s_ctx.pod.conditions.add()
                c.type = cond.get("type", "")
                c.status = cond.get("status", "")
                c.reason = cond.get("reason", "")
                c.message = cond.get("message", "")
        
        # Events
        for event in results.get("events", []):
            e = k8s_ctx.events.add()
            e.type = event.get("type", "")
            e.reason = event.get("reason", "")
            e.message = event.get("message", "")
            e.count = event.get("count", 1)
    
    def _build_service_mesh_context(self, mesh_ctx: pb2.ServiceMeshContext,
                                     graph_data: Dict, istio_config: Dict,
                                     service_name: str, namespace: str):
        """Build ServiceMeshContext from Kiali data."""
        mesh_ctx.service_name = service_name
        mesh_ctx.service_namespace = namespace
        
        # Parse dependencies
        deps = self.kiali.parse_dependencies(graph_data, service_name)
        
        for up in deps.get("upstream", []):
            dep = mesh_ctx.upstream_dependencies.add()
            dep.service_name = up.get("service_name", "")
            dep.namespace = up.get("namespace", "")
            dep.requests_per_second = up.get("requests_per_second", 0)
            dep.error_rate = up.get("error_rate", 0)
            dep.health_status = up.get("health_status", "unknown")
        
        for down in deps.get("downstream", []):
            dep = mesh_ctx.downstream_dependencies.add()
            dep.service_name = down.get("service_name", "")
            dep.namespace = down.get("namespace", "")
            dep.requests_per_second = down.get("requests_per_second", 0)
            dep.error_rate = down.get("error_rate", 0)
            dep.health_status = down.get("health_status", "unknown")
        
        # Istio config
        if istio_config:
            for vs in istio_config.get("virtual_services", []):
                v = mesh_ctx.virtual_services.add()
                v.name = vs.get("metadata", {}).get("name", "")
                v.namespace = vs.get("metadata", {}).get("namespace", "")
            
            for dr in istio_config.get("destination_rules", []):
                d = mesh_ctx.destination_rules.add()
                d.name = dr.get("metadata", {}).get("name", "")
                d.namespace = dr.get("metadata", {}).get("namespace", "")
    
    def _build_log_context(self, log_ctx: pb2.LogContext, 
                           recent_logs: List[Dict], error_logs: List[Dict]):
        """Build LogContext from Loki logs."""
        log_ctx.source = "loki"
        log_ctx.total_lines = len(recent_logs)
        
        if recent_logs:
            log_ctx.start_time = recent_logs[-1].get("timestamp", 0)
            log_ctx.end_time = recent_logs[0].get("timestamp", 0)
        
        for log in recent_logs[:50]:  # Limit to 50 recent logs
            entry = log_ctx.recent_logs.add()
            entry.timestamp = log.get("timestamp", 0)
            entry.level = log.get("level", "INFO")
            entry.message = log.get("message", "")[:1000]  # Truncate long messages
            entry.container = log.get("container", "")
            entry.pod = log.get("pod", "")
        
        for log in error_logs[:20]:  # Limit to 20 error logs
            entry = log_ctx.error_logs.add()
            entry.timestamp = log.get("timestamp", 0)
            entry.level = "ERROR"
            entry.message = log.get("message", "")[:1000]
            entry.container = log.get("container", "")
            entry.pod = log.get("pod", "")
    
    def _build_metrics_context(self, metrics_ctx: pb2.MetricsContext, results: Dict):
        """Build MetricsContext from Prometheus data."""
        # CPU
        cpu_data = results.get("cpu", {})
        if cpu_data:
            metrics_ctx.cpu_usage.name = cpu_data.get("name", "cpu_usage")
            metrics_ctx.cpu_usage.unit = cpu_data.get("unit", "cores")
            metrics_ctx.cpu_usage.current_value = cpu_data.get("current", 0)
            metrics_ctx.cpu_usage.avg_value = cpu_data.get("avg", 0)
            metrics_ctx.cpu_usage.max_value = cpu_data.get("max", 0)
            metrics_ctx.cpu_usage.min_value = cpu_data.get("min", 0)
            
            for dp in cpu_data.get("values", [])[-20:]:  # Last 20 data points
                point = metrics_ctx.cpu_usage.values.add()
                point.timestamp = dp.get("timestamp", 0)
                point.value = dp.get("value", 0)
        
        # Memory
        memory_data = results.get("memory", {})
        if memory_data:
            metrics_ctx.memory_usage.name = memory_data.get("name", "memory_usage")
            metrics_ctx.memory_usage.unit = memory_data.get("unit", "bytes")
            metrics_ctx.memory_usage.current_value = memory_data.get("current", 0)
            metrics_ctx.memory_usage.avg_value = memory_data.get("avg", 0)
            metrics_ctx.memory_usage.max_value = memory_data.get("max", 0)
            metrics_ctx.memory_usage.min_value = memory_data.get("min", 0)
        
        # Network
        network_data = results.get("network", {})
        if network_data.get("rx"):
            metrics_ctx.network_rx_bytes.name = "network_rx_bytes"
            metrics_ctx.network_rx_bytes.unit = "bytes/sec"
            metrics_ctx.network_rx_bytes.current_value = network_data["rx"].get("current", 0)
        if network_data.get("tx"):
            metrics_ctx.network_tx_bytes.name = "network_tx_bytes"
            metrics_ctx.network_tx_bytes.unit = "bytes/sec"
            metrics_ctx.network_tx_bytes.current_value = network_data["tx"].get("current", 0)


# =============================================================================
# gRPC Client for CB Model
# =============================================================================

class CBModelClient:
    """gRPC client for sending requests to CB Model LLM."""
    
    def __init__(self, host: str, port: int):
        self.address = f"{host}:{port}"
        self.channel = None
        self.stub = None
        self._connect()
    
    def _connect(self):
        """Establish gRPC connection."""
        try:
            self.channel = grpc.insecure_channel(self.address)
            self.stub = pb2_grpc.CBModelServiceStub(self.channel)
            logger.info(f"✓ Connected to CB Model at {self.address}")
        except Exception as e:
            logger.error(f"✗ Failed to connect to CB Model: {e}")
    
    def analyze_alert(self, request: pb2.AlertAnalysisRequest) -> pb2.AlertAnalysisResponse:
        """Send alert analysis request to LLM."""
        try:
            response = self.stub.AnalyzeAlert(
                request,
                timeout=config.grpc_timeout_seconds
            )
            logger.info(f"[{request.request_id}] Received response from CB Model")
            return response
        except grpc.RpcError as e:
            logger.error(f"[{request.request_id}] gRPC error: {e.code()} - {e.details()}")
            raise
    
    def simple_completion(self, prompt: str, system_prompt: str = "") -> str:
        """Send a simple completion request (backward compatible)."""
        try:
            request = pb2.CompletionRequest(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=1024,
                temperature=0.7,
            )
            response = self.stub.GenerateCompletion(
                request,
                timeout=config.grpc_timeout_seconds
            )
            return response.completion
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            raise
    
    def health_check(self) -> bool:
        """Check if CB Model is healthy."""
        try:
            response = self.stub.HealthCheck(pb2.HealthCheckRequest(), timeout=10)
            return response.healthy and response.model_loaded
        except:
            return False


# =============================================================================
# Flask Webhook Server
# =============================================================================

app = Flask(__name__)
aggregator = None
cb_client = None


def init_services():
    """Initialize all services."""
    global aggregator, cb_client
    aggregator = AlertContextAggregator()
    cb_client = CBModelClient(config.cb_model_host, config.cb_model_port)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    cb_healthy = cb_client.health_check() if cb_client else False
    return jsonify({
        "status": "healthy",
        "cb_model_connected": cb_healthy,
        "prometheus_url": config.prometheus_url,
        "kiali_url": config.kiali_url,
    })


@app.route("/webhook", methods=["POST"])
def alertmanager_webhook():
    """
    AlertManager webhook receiver.
    
    Receives alerts, aggregates context, and forwards to CB Model.
    """
    try:
        payload = request.json
        alerts = payload.get("alerts", [])
        
        logger.info(f"Received {len(alerts)} alert(s) from AlertManager")
        
        responses = []
        for alert in alerts:
            if alert.get("status") != "firing":
                logger.info(f"Skipping non-firing alert: {alert.get('labels', {}).get('alertname')}")
                continue
            
            try:
                # Aggregate context
                analysis_request = aggregator.aggregate_context(alert)
                
                # Send to CB Model
                if cb_client:
                    response = cb_client.analyze_alert(analysis_request)
                    responses.append({
                        "request_id": analysis_request.request_id,
                        "alert_name": alert.get("labels", {}).get("alertname"),
                        "status": "analyzed",
                        "confidence": response.confidence,
                    })
                else:
                    responses.append({
                        "request_id": analysis_request.request_id,
                        "alert_name": alert.get("labels", {}).get("alertname"),
                        "status": "cb_model_unavailable",
                    })
                    
            except Exception as e:
                logger.error(f"Error processing alert: {e}")
                responses.append({
                    "alert_name": alert.get("labels", {}).get("alertname"),
                    "status": "error",
                    "error": str(e),
                })
        
        return jsonify({"status": "processed", "results": responses})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/test", methods=["POST"])
def test_analysis():
    """
    Test endpoint to manually trigger alert analysis.
    
    Body should contain a mock alert payload.
    """
    try:
        alert = request.json
        
        # Aggregate context
        analysis_request = aggregator.aggregate_context(alert)
        
        # For testing, print the request
        logger.info(f"Generated request with {len(analysis_request.logs.recent_logs)} log entries")
        
        # Send to CB Model if available
        if cb_client and cb_client.health_check():
            response = cb_client.analyze_alert(analysis_request)
            return jsonify({
                "request_id": analysis_request.request_id,
                "raw_response": response.raw_response,
                "confidence": response.confidence,
                "generation_time_ms": response.generation_time_ms,
            })
        else:
            return jsonify({
                "request_id": analysis_request.request_id,
                "status": "context_aggregated",
                "message": "CB Model not available, returning aggregated context",
                "context_summary": {
                    "alert_name": analysis_request.alert.alert_name,
                    "pod": analysis_request.kubernetes.pod.name,
                    "namespace": analysis_request.kubernetes.namespace.name,
                    "log_count": len(analysis_request.logs.recent_logs),
                    "upstream_deps": len(analysis_request.service_mesh.upstream_dependencies),
                    "downstream_deps": len(analysis_request.service_mesh.downstream_dependencies),
                }
            })
            
    except Exception as e:
        logger.error(f"Test error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Alert Context Aggregator - Starting")
    logger.info("=" * 60)
    logger.info(f"Prometheus URL: {config.prometheus_url}")
    logger.info(f"Kiali URL: {config.kiali_url}")
    logger.info(f"Loki URL: {config.loki_url}")
    logger.info(f"CB Model: {config.cb_model_host}:{config.cb_model_port}")
    logger.info(f"Webhook port: {config.webhook_port}")
    logger.info("=" * 60)
    
    # Initialize services
    init_services()
    
    # Start Flask server
    app.run(host=config.webhook_host, port=config.webhook_port)


if __name__ == "__main__":
    main()
