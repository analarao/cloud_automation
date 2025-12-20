#!/usr/bin/env python3
"""
Alert Remediation Server
========================

This server:
1. Receives alerts from AlertManager via webhook
2. Extracts and enriches context from Prometheus, Kiali, K8s API, Loki
3. Packages everything into a protobuf message
4. Sends to the Gemini client via gRPC for analysis and remediation

Deployment: Runs in Kubernetes, receives webhooks from AlertManager
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
import uuid

import grpc
from flask import Flask, request, jsonify
from prometheus_api_client import PrometheusConnect
import requests

# Import generated protobuf modules
import alert_pb2
import alert_pb2_grpc

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("alert_server")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Config:
    """Server configuration from environment variables."""
    # Webhook server
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "9095"))
    webhook_host: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    
    # Data sources
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090")
    alertmanager_url: str = os.getenv("ALERTMANAGER_URL", "http://prometheus-kube-prometheus-alertmanager.monitoring.svc.cluster.local:9093")
    kiali_url: str = os.getenv("KIALI_URL", "http://kiali.istio-system.svc.cluster.local:20001")
    loki_url: str = os.getenv("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100")
    
    # Kubernetes API
    k8s_api_url: str = os.getenv("K8S_API_URL", "https://kubernetes.default.svc")
    k8s_token_path: str = os.getenv("K8S_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token")
    k8s_ca_path: str = os.getenv("K8S_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    
    # Gemini client (where to send enriched alerts)
    gemini_client_host: str = os.getenv("GEMINI_CLIENT_HOST", "gemini-client-service.monitoring.svc.cluster.local")
    gemini_client_port: int = int(os.getenv("GEMINI_CLIENT_PORT", "50051"))
    
    # Query settings
    metrics_lookback_minutes: int = int(os.getenv("METRICS_LOOKBACK_MINUTES", "30"))
    logs_lookback_minutes: int = int(os.getenv("LOGS_LOOKBACK_MINUTES", "15"))
    max_log_lines: int = int(os.getenv("MAX_LOG_LINES", "100"))
    
    # Timeouts
    grpc_timeout_seconds: int = int(os.getenv("GRPC_TIMEOUT_SECONDS", "300"))
    query_timeout_seconds: int = int(os.getenv("QUERY_TIMEOUT_SECONDS", "30"))


config = Config()


# =============================================================================
# Prometheus Querier
# =============================================================================

class PrometheusQuerier:
    """Query Prometheus for metrics and container info."""
    
    def __init__(self, url: str):
        self.url = url
        self.client: Optional[PrometheusConnect] = None
        self._connect()
    
    def _connect(self):
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
            return self.client.custom_query(query=promql)
        except Exception as e:
            logger.error(f"PromQL query failed: {promql[:100]}... Error: {e}")
            return []
    
    def query_range(self, promql: str, start_time: datetime, end_time: datetime, step: str = "1m") -> List[Dict]:
        """Execute a range query."""
        if not self.client:
            return []
        
        try:
            return self.client.custom_query_range(
                query=promql,
                start_time=start_time,
                end_time=end_time,
                step=step
            )
        except Exception as e:
            logger.error(f"Range query failed: {e}")
            return []
    
    def get_container_info(self, namespace: str, pod: str) -> Dict:
        """Get container metadata from cAdvisor metrics."""
        result = {}
        
        # Get image info
        query = f'kube_pod_container_info{{namespace="{namespace}", pod="{pod}"}}'
        data = self.query(query)
        if data:
            metric = data[0].get("metric", {})
            result["image"] = metric.get("image", "")
            result["image_id"] = metric.get("image_id", "")
            result["container_name"] = metric.get("container", "")
        
        # Get restart count
        query = f'kube_pod_container_status_restarts_total{{namespace="{namespace}", pod="{pod}"}}'
        data = self.query(query)
        if data:
            result["restart_count"] = int(float(data[0].get("value", [0, 0])[1]))
        
        # Get resource limits
        query = f'kube_pod_container_resource_limits{{namespace="{namespace}", pod="{pod}"}}'
        data = self.query(query)
        for item in data:
            metric = item.get("metric", {})
            resource = metric.get("resource", "")
            value = item.get("value", [0, 0])[1]
            if resource == "cpu":
                result["cpu_limit"] = value
            elif resource == "memory":
                result["memory_limit"] = value
        
        return result
    
    def get_cpu_metrics(self, namespace: str, pod: str, lookback_minutes: int = 30) -> Dict:
        """Get CPU usage time series."""
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=lookback_minutes)
        
        query = f'rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod="{pod}", container!=""}}[5m])'
        data = self.query_range(query, start_time, end_time)
        
        if not data:
            return {"name": "cpu_usage", "unit": "cores", "current": 0, "avg": 0, "max": 0, "min": 0, "values": []}
        
        series = data[0]
        values = series.get("values", [])
        float_values = [float(v[1]) for v in values]
        
        return {
            "name": "cpu_usage",
            "unit": "cores",
            "current": float_values[-1] if float_values else 0,
            "avg": sum(float_values) / len(float_values) if float_values else 0,
            "max": max(float_values) if float_values else 0,
            "min": min(float_values) if float_values else 0,
            "values": [{"timestamp": int(v[0]), "value": float(v[1])} for v in values[-20:]]  # Last 20 points
        }
    
    def get_memory_metrics(self, namespace: str, pod: str, lookback_minutes: int = 30) -> Dict:
        """Get memory usage time series."""
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=lookback_minutes)
        
        query = f'container_memory_working_set_bytes{{namespace="{namespace}", pod="{pod}", container!=""}}'
        data = self.query_range(query, start_time, end_time)
        
        if not data:
            return {"name": "memory_usage", "unit": "bytes", "current": 0, "avg": 0, "max": 0, "min": 0, "values": []}
        
        series = data[0]
        values = series.get("values", [])
        float_values = [float(v[1]) for v in values]
        
        return {
            "name": "memory_usage",
            "unit": "bytes",
            "current": float_values[-1] if float_values else 0,
            "avg": sum(float_values) / len(float_values) if float_values else 0,
            "max": max(float_values) if float_values else 0,
            "min": min(float_values) if float_values else 0,
            "values": [{"timestamp": int(v[0]), "value": float(v[1])} for v in values[-20:]]
        }


# =============================================================================
# Kubernetes API Querier
# =============================================================================

class KubernetesQuerier:
    """Query Kubernetes API for pod/deployment info."""
    
    def __init__(self, api_url: str, token_path: str, ca_path: str):
        self.api_url = api_url
        self.token = None
        self.session = requests.Session()
        
        # Load service account token
        if os.path.exists(token_path):
            with open(token_path, "r") as f:
                self.token = f.read().strip()
            self.session.headers["Authorization"] = f"Bearer {self.token}"
            logger.info("✓ Loaded Kubernetes service account token")
        
        # Configure TLS
        if os.path.exists(ca_path):
            self.session.verify = ca_path
        else:
            self.session.verify = False
    
    def _get(self, path: str) -> Optional[Dict]:
        """Make GET request to K8s API."""
        try:
            url = f"{self.api_url}{path}"
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"K8s API error: {resp.status_code} - {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"K8s API request failed: {e}")
            return None
    
    def get_pod(self, namespace: str, pod_name: str) -> Optional[Dict]:
        """Get pod details."""
        return self._get(f"/api/v1/namespaces/{namespace}/pods/{pod_name}")
    
    def get_deployment(self, namespace: str, deployment_name: str) -> Optional[Dict]:
        """Get deployment details."""
        return self._get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}")
    
    def get_events(self, namespace: str, field_selector: str = "") -> List[Dict]:
        """Get events for a namespace."""
        path = f"/api/v1/namespaces/{namespace}/events"
        if field_selector:
            path += f"?fieldSelector={field_selector}"
        
        data = self._get(path)
        if data:
            return data.get("items", [])
        return []
    
    def get_services(self, namespace: str) -> List[Dict]:
        """Get services in namespace."""
        data = self._get(f"/api/v1/namespaces/{namespace}/services")
        if data:
            return data.get("items", [])
        return []
    
    def get_pod_info(self, namespace: str, pod_name: str) -> alert_pb2.PodInfo:
        """Get pod info as protobuf message."""
        pod_info = alert_pb2.PodInfo()
        
        pod = self.get_pod(namespace, pod_name)
        if not pod:
            return pod_info
        
        metadata = pod.get("metadata", {})
        status = pod.get("status", {})
        
        pod_info.name = metadata.get("name", "")
        pod_info.uid = metadata.get("uid", "")
        pod_info.phase = status.get("phase", "")
        pod_info.pod_ip = status.get("podIP", "")
        pod_info.host_ip = status.get("hostIP", "")
        pod_info.created_at = int(datetime.fromisoformat(
            metadata.get("creationTimestamp", "1970-01-01T00:00:00Z").replace("Z", "+00:00")
        ).timestamp())
        
        for k, v in metadata.get("labels", {}).items():
            pod_info.labels[k] = str(v)
        
        for k, v in metadata.get("annotations", {}).items():
            pod_info.annotations[k] = str(v)
        
        for cond in status.get("conditions", []):
            condition = pod_info.conditions.add()
            condition.type = cond.get("type", "")
            condition.status = cond.get("status", "")
            condition.reason = cond.get("reason", "")
            condition.message = cond.get("message", "")
        
        return pod_info
    
    def get_workload_info(self, namespace: str, pod_name: str) -> alert_pb2.WorkloadInfo:
        """Determine parent workload and get its info."""
        workload_info = alert_pb2.WorkloadInfo()
        
        pod = self.get_pod(namespace, pod_name)
        if not pod:
            return workload_info
        
        # Find owner reference
        for owner in pod.get("metadata", {}).get("ownerReferences", []):
            kind = owner.get("kind", "")
            name = owner.get("name", "")
            
            # If owned by ReplicaSet, find parent Deployment
            if kind == "ReplicaSet":
                # ReplicaSet name format: deployment-name-hash
                deployment_name = "-".join(name.split("-")[:-1])
                deployment = self.get_deployment(namespace, deployment_name)
                if deployment:
                    spec = deployment.get("spec", {})
                    status = deployment.get("status", {})
                    
                    workload_info.kind = "Deployment"
                    workload_info.name = deployment.get("metadata", {}).get("name", "")
                    workload_info.namespace = namespace
                    workload_info.replicas = spec.get("replicas", 0)
                    workload_info.ready_replicas = status.get("readyReplicas", 0)
                    workload_info.available_replicas = status.get("availableReplicas", 0)
                    
                    for k, v in deployment.get("metadata", {}).get("labels", {}).items():
                        workload_info.labels[k] = str(v)
                    
                    return workload_info
        
        return workload_info


# =============================================================================
# Kiali Querier (Service Mesh Dependencies)
# =============================================================================

class KialiQuerier:
    """Query Kiali for service mesh topology."""
    
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        
        # Auth if configured
        kiali_user = os.getenv("KIALI_USERNAME", "")
        kiali_pass = os.getenv("KIALI_PASSWORD", "")
        if kiali_user and kiali_pass:
            self.session.auth = (kiali_user, kiali_pass)
    
    def get_service_graph(self, namespace: str, service: str) -> Dict:
        """Get service dependency graph."""
        try:
            url = f"{self.url}/kiali/api/namespaces/{namespace}/services/{service}/graph"
            params = {"graphType": "service", "duration": "10m"}
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"Kiali query failed: {e}")
        return {}
    
    def get_dependencies(self, namespace: str, service: str) -> alert_pb2.ServiceMeshInfo:
        """Get upstream/downstream dependencies as protobuf."""
        mesh_info = alert_pb2.ServiceMeshInfo()
        mesh_info.service_name = service
        mesh_info.service_namespace = namespace
        
        graph = self.get_service_graph(namespace, service)
        if not graph:
            return mesh_info
        
        # Parse graph to find dependencies
        elements = graph.get("elements", {})
        nodes = {n.get("data", {}).get("id"): n.get("data", {}) 
                 for n in elements.get("nodes", [])}
        edges = elements.get("edges", [])
        
        # Find this service's node ID
        this_node_id = None
        for node_id, node_data in nodes.items():
            if node_data.get("service") == service and node_data.get("namespace") == namespace:
                this_node_id = node_id
                break
        
        if not this_node_id:
            return mesh_info
        
        # Parse edges for dependencies
        for edge in edges:
            edge_data = edge.get("data", {})
            source = edge_data.get("source")
            target = edge_data.get("target")
            traffic = edge_data.get("traffic", {})
            
            if source == this_node_id:
                # Upstream dependency (this service calls target)
                target_data = nodes.get(target, {})
                dep = mesh_info.upstream.add()
                dep.service_name = target_data.get("service", "")
                dep.namespace = target_data.get("namespace", "")
                dep.requests_per_second = traffic.get("rate", 0)
                dep.error_rate = traffic.get("percentErr", 0)
            
            elif target == this_node_id:
                # Downstream dependency (source calls this service)
                source_data = nodes.get(source, {})
                dep = mesh_info.downstream.add()
                dep.service_name = source_data.get("service", "")
                dep.namespace = source_data.get("namespace", "")
                dep.requests_per_second = traffic.get("rate", 0)
                dep.error_rate = traffic.get("percentErr", 0)
        
        return mesh_info


# =============================================================================
# Loki Querier (Logs)
# =============================================================================

class LokiQuerier:
    """Query Loki for logs."""
    
    def __init__(self, url: str):
        self.url = url.rstrip("/")
    
    def query_logs(self, namespace: str, pod: str, limit: int = 100, lookback_minutes: int = 15) -> List[Dict]:
        """Query logs from Loki."""
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(minutes=lookback_minutes)
            
            query = f'{{namespace="{namespace}", pod="{pod}"}}'
            url = f"{self.url}/loki/api/v1/query_range"
            params = {
                "query": query,
                "start": int(start_time.timestamp() * 1e9),
                "end": int(end_time.timestamp() * 1e9),
                "limit": limit,
            }
            
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("data", {}).get("result", [])
                
                logs = []
                for stream in results:
                    labels = stream.get("stream", {})
                    for ts, msg in stream.get("values", []):
                        logs.append({
                            "timestamp": int(ts) // 1_000_000_000,
                            "message": msg,
                            "labels": labels,
                        })
                
                # Sort by timestamp
                logs.sort(key=lambda x: x["timestamp"])
                return logs
        except Exception as e:
            logger.error(f"Loki query failed: {e}")
        
        return []
    
    def get_log_info(self, namespace: str, pod: str, limit: int = 100) -> alert_pb2.LogInfo:
        """Get logs as protobuf message."""
        log_info = alert_pb2.LogInfo()
        log_info.source = "loki"
        
        logs = self.query_logs(namespace, pod, limit)
        
        if logs:
            log_info.start_time = logs[0]["timestamp"]
            log_info.end_time = logs[-1]["timestamp"]
            
            for log in logs:
                entry = log_info.entries.add()
                entry.timestamp = log["timestamp"]
                entry.message = log["message"]
                entry.pod = log.get("labels", {}).get("pod", "")
                entry.container = log.get("labels", {}).get("container", "")
                
                # Detect log level
                msg_lower = log["message"].lower()
                if "error" in msg_lower or "exception" in msg_lower:
                    entry.level = "ERROR"
                    # Also add to errors list
                    error_entry = log_info.errors.add()
                    error_entry.CopyFrom(entry)
                elif "warn" in msg_lower:
                    entry.level = "WARN"
                elif "debug" in msg_lower:
                    entry.level = "DEBUG"
                else:
                    entry.level = "INFO"
        
        return log_info


# =============================================================================
# Alert Context Aggregator
# =============================================================================

class AlertContextAggregator:
    """Aggregates context from multiple sources for an alert."""
    
    def __init__(self):
        self.prometheus = PrometheusQuerier(config.prometheus_url)
        self.kubernetes = KubernetesQuerier(
            config.k8s_api_url,
            config.k8s_token_path,
            config.k8s_ca_path
        )
        self.kiali = KialiQuerier(config.kiali_url)
        self.loki = LokiQuerier(config.loki_url)
    
    def extract_alert_metadata(self, alert_data: Dict) -> alert_pb2.AlertMetadata:
        """Extract alert metadata from AlertManager webhook payload."""
        metadata = alert_pb2.AlertMetadata()
        
        metadata.name = alert_data.get("labels", {}).get("alertname", "")
        metadata.severity = alert_data.get("labels", {}).get("severity", "warning")
        metadata.state = alert_data.get("status", "firing")
        metadata.summary = alert_data.get("annotations", {}).get("summary", "")
        metadata.description = alert_data.get("annotations", {}).get("description", "")
        metadata.fingerprint = alert_data.get("fingerprint", "")
        metadata.generator_url = alert_data.get("generatorURL", "")
        
        # Parse timestamps
        starts_at = alert_data.get("startsAt", "")
        if starts_at:
            try:
                ts = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                metadata.starts_at = int(ts.timestamp())
            except:
                pass
        
        # Copy labels and annotations
        for k, v in alert_data.get("labels", {}).items():
            metadata.labels[k] = str(v)
        
        for k, v in alert_data.get("annotations", {}).items():
            metadata.annotations[k] = str(v)
        
        return metadata
    
    def build_alert_request(self, alert_data: Dict) -> alert_pb2.AlertRequest:
        """Build complete AlertRequest with all context."""
        request = alert_pb2.AlertRequest()
        request.request_id = str(uuid.uuid4())
        request.created_at = int(datetime.now().timestamp())
        
        # Extract basic alert metadata
        request.alert.CopyFrom(self.extract_alert_metadata(alert_data))
        
        # Get namespace and pod from labels
        labels = alert_data.get("labels", {})
        namespace = labels.get("namespace", "default")
        pod = labels.get("pod", "")
        service = labels.get("service", "")
        
        # If no pod specified, try to find from service
        if not pod and service:
            # Query Prometheus for pods with this service label
            pass  # Would need service discovery here
        
        logger.info(f"Building context for alert: {request.alert.name} in {namespace}/{pod}")
        
        # Gather context in parallel
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {}
            
            if pod:
                # Container info from Prometheus
                futures["container"] = executor.submit(
                    self.prometheus.get_container_info, namespace, pod
                )
                
                # CPU metrics
                futures["cpu"] = executor.submit(
                    self.prometheus.get_cpu_metrics, namespace, pod
                )
                
                # Memory metrics
                futures["memory"] = executor.submit(
                    self.prometheus.get_memory_metrics, namespace, pod
                )
                
                # Pod info from K8s API
                futures["pod"] = executor.submit(
                    self.kubernetes.get_pod_info, namespace, pod
                )
                
                # Workload info
                futures["workload"] = executor.submit(
                    self.kubernetes.get_workload_info, namespace, pod
                )
                
                # Logs from Loki
                futures["logs"] = executor.submit(
                    self.loki.get_log_info, namespace, pod
                )
            
            if service:
                # Service mesh dependencies
                futures["mesh"] = executor.submit(
                    self.kiali.get_dependencies, namespace, service
                )
            
            # Collect results
            for name, future in futures.items():
                try:
                    result = future.result(timeout=config.query_timeout_seconds)
                    
                    if name == "container" and result:
                        request.container.image = result.get("image", "")
                        request.container.image_hash = result.get("image_id", "")
                        request.container.container_name = result.get("container_name", "")
                        request.container.restart_count = result.get("restart_count", 0)
                        if result.get("cpu_limit"):
                            request.container.limits.cpu = str(result.get("cpu_limit", ""))
                        if result.get("memory_limit"):
                            request.container.limits.memory = str(result.get("memory_limit", ""))
                    
                    elif name == "cpu" and result:
                        request.metrics.cpu.name = result.get("name", "")
                        request.metrics.cpu.unit = result.get("unit", "")
                        request.metrics.cpu.current = result.get("current", 0)
                        request.metrics.cpu.avg = result.get("avg", 0)
                        request.metrics.cpu.max = result.get("max", 0)
                        request.metrics.cpu.min = result.get("min", 0)
                        for v in result.get("values", []):
                            point = request.metrics.cpu.values.add()
                            point.timestamp = v.get("timestamp", 0)
                            point.value = v.get("value", 0)
                    
                    elif name == "memory" and result:
                        request.metrics.memory.name = result.get("name", "")
                        request.metrics.memory.unit = result.get("unit", "")
                        request.metrics.memory.current = result.get("current", 0)
                        request.metrics.memory.avg = result.get("avg", 0)
                        request.metrics.memory.max = result.get("max", 0)
                        request.metrics.memory.min = result.get("min", 0)
                        for v in result.get("values", []):
                            point = request.metrics.memory.values.add()
                            point.timestamp = v.get("timestamp", 0)
                            point.value = v.get("value", 0)
                    
                    elif name == "pod" and result:
                        request.kubernetes.pod.CopyFrom(result)
                        request.kubernetes.namespace = namespace
                    
                    elif name == "workload" and result:
                        request.kubernetes.workload.CopyFrom(result)
                    
                    elif name == "logs" and result:
                        request.logs.CopyFrom(result)
                    
                    elif name == "mesh" and result:
                        request.service_mesh.CopyFrom(result)
                    
                    logger.info(f"  ✓ Gathered {name} context")
                
                except Exception as e:
                    logger.error(f"  ✗ Failed to gather {name} context: {e}")
        
        return request


# =============================================================================
# gRPC Client to Gemini Service
# =============================================================================

class GeminiClient:
    """gRPC client to send enriched alerts to Gemini service."""
    
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.channel = None
        self.stub = None
    
    def connect(self):
        """Establish gRPC connection."""
        try:
            address = f"{self.host}:{self.port}"
            self.channel = grpc.insecure_channel(address)
            self.stub = alert_pb2_grpc.AlertRemediationServiceStub(self.channel)
            logger.info(f"✓ Connected to Gemini client at {address}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to connect to Gemini client: {e}")
            return False
    
    def send_alert(self, request: alert_pb2.AlertRequest, timeout: int = 300) -> alert_pb2.AlertResponse:
        """Send alert request and get response."""
        if not self.stub:
            if not self.connect():
                response = alert_pb2.AlertResponse()
                response.success = False
                response.error = "Failed to connect to Gemini client"
                return response
        
        try:
            logger.info(f"Sending alert {request.alert.name} to Gemini client...")
            response = self.stub.AnalyzeAndRemediate(request, timeout=timeout)
            logger.info(f"Received response: success={response.success}, status={response.status}")
            return response
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            response = alert_pb2.AlertResponse()
            response.success = False
            response.error = f"gRPC error: {e.details()}"
            return response


# =============================================================================
# Flask Webhook Server
# =============================================================================

app = Flask(__name__)
aggregator: Optional[AlertContextAggregator] = None
gemini_client: Optional[GeminiClient] = None


def init_services():
    """Initialize services."""
    global aggregator, gemini_client
    aggregator = AlertContextAggregator()
    gemini_client = GeminiClient(config.gemini_client_host, config.gemini_client_port)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "alert-server",
        "gemini_client_host": config.gemini_client_host,
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    """AlertManager webhook endpoint."""
    global aggregator, gemini_client
    
    if not aggregator:
        init_services()
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data"}), 400
        
        alerts = data.get("alerts", [])
        logger.info(f"Received {len(alerts)} alert(s) from AlertManager")
        
        results = []
        for alert_data in alerts:
            alert_name = alert_data.get("labels", {}).get("alertname", "unknown")
            status = alert_data.get("status", "unknown")
            
            logger.info(f"Processing alert: {alert_name} ({status})")
            
            # Only process firing alerts
            if status != "firing":
                logger.info(f"Skipping {alert_name} - status is {status}")
                results.append({"alert": alert_name, "status": "skipped", "reason": f"status={status}"})
                continue
            
            # Build enriched request
            alert_request = aggregator.build_alert_request(alert_data)
            
            # Send to Gemini client
            response = gemini_client.send_alert(alert_request, timeout=config.grpc_timeout_seconds)
            
            results.append({
                "alert": alert_name,
                "request_id": alert_request.request_id,
                "success": response.success,
                "status": response.status,
                "error": response.error if response.error else None,
            })
        
        return jsonify({"results": results})
    
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/test", methods=["POST"])
def test_alert():
    """Test endpoint - send a mock alert."""
    global aggregator, gemini_client
    
    if not aggregator:
        init_services()
    
    try:
        data = request.get_json() or {}
        
        # Build mock alert
        mock_alert = {
            "status": "firing",
            "labels": {
                "alertname": data.get("alertname", "TestAlert"),
                "namespace": data.get("namespace", "target-services"),
                "pod": data.get("pod", "reviews-v1-123"),
                "service": data.get("service", "reviews"),
                "severity": data.get("severity", "warning"),
            },
            "annotations": {
                "summary": data.get("summary", "Test alert for debugging"),
                "description": data.get("description", "This is a test alert"),
            },
            "fingerprint": hashlib.md5(str(data).encode()).hexdigest(),
            "startsAt": datetime.now().isoformat() + "Z",
        }
        
        # Build and send
        alert_request = aggregator.build_alert_request(mock_alert)
        response = gemini_client.send_alert(alert_request, timeout=config.grpc_timeout_seconds)
        
        return jsonify({
            "request_id": alert_request.request_id,
            "success": response.success,
            "status": response.status,
            "analysis": {
                "primary_cause": response.analysis.primary_cause if response.analysis else "",
                "category": response.analysis.category if response.analysis else "",
            },
            "actions": [
                {
                    "description": a.description,
                    "success": a.success,
                    "output": a.output[:500] if a.output else "",
                }
                for a in response.actions
            ],
            "raw_response": response.raw_llm_response[:1000] if response.raw_llm_response else "",
        })
    
    except Exception as e:
        logger.exception(f"Test error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Alert Remediation Server")
    logger.info("=" * 60)
    logger.info(f"Webhook port: {config.webhook_port}")
    logger.info(f"Gemini client: {config.gemini_client_host}:{config.gemini_client_port}")
    logger.info(f"Prometheus: {config.prometheus_url}")
    logger.info(f"Kiali: {config.kiali_url}")
    logger.info(f"Loki: {config.loki_url}")
    logger.info("=" * 60)
    
    init_services()
    
    app.run(
        host=config.webhook_host,
        port=config.webhook_port,
        debug=False,
        threaded=True,
    )
