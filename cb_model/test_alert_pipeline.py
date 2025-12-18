#!/usr/bin/env python3
"""
Alert Pipeline Integration Tests
=================================

This module tests the end-to-end alert pipeline:
1. Simulating AlertManager webhook calls
2. Testing context aggregation
3. Verifying LLM orchestrator processing
4. Validating Istio dependency discovery

Usage:
    # Run all tests
    python test_alert_pipeline.py
    
    # Test specific alert
    python test_alert_pipeline.py --alert BookinfoReviewsDown
    
    # Test only context aggregation (no LLM)
    python test_alert_pipeline.py --context-only
    
    # Test against local services
    python test_alert_pipeline.py --local
"""

import os
import sys
import json
import time
import asyncio
import argparse
import requests
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alert_pipeline import (
    AlertPipeline,
    AlertContextAggregator,
    IstioPrometheusQuerier,
    AlertContext,
    PipelineConfig,
    config
)


# =============================================================================
# Test Alerts - Based on alerts.yaml
# =============================================================================

@dataclass
class TestAlert:
    """Test alert definition."""
    name: str
    payload: Dict[str, Any]
    expected_service: str
    expected_namespace: str
    description: str


class TestAlerts:
    """Collection of test alerts matching alerts.yaml definitions."""
    
    @staticmethod
    def bookinfo_reviews_down() -> TestAlert:
        """
        Test: BookinfoReviewsDown
        From alerts.yaml - triggers when reviews service is down
        """
        return TestAlert(
            name="BookinfoReviewsDown",
            payload={
                "status": "firing",
                "labels": {
                    "alertname": "BookinfoReviewsDown",
                    "severity": "critical",
                    "namespace": "target-services",
                    "service": "reviews",
                    "app": "reviews",
                    "job": "istio-system/envoy-stats-monitor",
                },
                "annotations": {
                    "summary": "Bookinfo 'reviews' service is down",
                    "description": "No instances of the 'reviews' service in the 'target-services' namespace are reachable."
                },
                "startsAt": datetime.now().isoformat() + "Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=sum%28up%7Bpod%3D~%22reviews-v.%2A%22%7D%29+%3D%3D+0",
                "fingerprint": "abc123def456",
            },
            expected_service="reviews",
            expected_namespace="target-services",
            description="Reviews service is completely down - no pods responding"
        )
    
    @staticmethod
    def predicted_cpu_breach() -> TestAlert:
        """
        Test: PredictedCpuBreach
        From cs_rules.yml - ML-predicted CPU breach
        """
        return TestAlert(
            name="PredictedCpuBreach",
            payload={
                "status": "firing",
                "labels": {
                    "alertname": "PredictedCpuBreach",
                    "severity": "critical",
                    "alert_type": "SLO_PREDICTION",
                    "source": "CS_ML_Service",
                    "target_pod": "productpage-v1-xxx",
                    "target_namespace": "target-services",
                    "target_app": "productpage",
                    "target_container": "productpage",
                },
                "annotations": {
                    "summary": "Predicted CPU breach for pod productpage-v1-xxx in namespace target-services",
                    "description": "The CS ML service predicts CPU usage will exceed SLO threshold (0.01) within 15 minutes.",
                    "predicted_value": "0.015",
                    "threshold": "0.01",
                },
                "startsAt": datetime.now().isoformat() + "Z",
                "fingerprint": "cpu-breach-001",
            },
            expected_service="productpage",
            expected_namespace="target-services",
            description="ML model predicted CPU will breach threshold"
        )
    
    @staticmethod
    def cs_memory_exhaustion() -> TestAlert:
        """
        Test: CS_Memory_Exhaustion_Predicted
        From cs_rules.yml - Memory exhaustion prediction
        """
        return TestAlert(
            name="CS_Memory_Exhaustion_Predicted",
            payload={
                "status": "firing",
                "labels": {
                    "alertname": "CS_Memory_Exhaustion_Predicted",
                    "severity": "high",
                    "alert_type": "SLO_PREDICTION",
                    "metric": "node_memory_MemAvailable_bytes",
                    "instance": "node-1:9100",
                },
                "annotations": {
                    "summary": "Memory exhaustion predicted on host in less than 6 hours.",
                    "description": "The available memory is trending toward 0 on node-1:9100.",
                },
                "startsAt": datetime.now().isoformat() + "Z",
                "fingerprint": "mem-exhaust-001",
            },
            expected_service="unknown",
            expected_namespace="target-services",
            description="Node memory predicted to exhaust"
        )
    
    @staticmethod
    def cs_cpu_anomaly() -> TestAlert:
        """
        Test: CS_CPU_High_Anomaly_Detected
        From cs_rules.yml - Statistical anomaly detection
        """
        return TestAlert(
            name="CS_CPU_High_Anomaly_Detected",
            payload={
                "status": "firing",
                "labels": {
                    "alertname": "CS_CPU_High_Anomaly_Detected",
                    "severity": "critical",
                    "alert_type": "ANOMALY_DETECTED",
                    "metric": "node_cpu_seconds_total",
                    "instance": "node-1:9100",
                    "cpu": "0",
                },
                "annotations": {
                    "summary": "Anomalous high CPU utilization detected.",
                    "description": "CPU idle rate is 3-sigma below its 24-hour baseline. Likely a runaway process.",
                },
                "startsAt": datetime.now().isoformat() + "Z",
                "fingerprint": "cpu-anomaly-001",
            },
            expected_service="unknown",
            expected_namespace="target-services",
            description="Statistical anomaly - CPU 3-sigma above baseline"
        )
    
    @staticmethod
    def custom_service_alert(service: str, namespace: str = "target-services") -> TestAlert:
        """Create a custom service alert for testing."""
        return TestAlert(
            name=f"{service.title()}Down",
            payload={
                "status": "firing",
                "labels": {
                    "alertname": f"{service.title()}Down",
                    "severity": "critical",
                    "namespace": namespace,
                    "service": service,
                    "app": service,
                },
                "annotations": {
                    "summary": f"Service '{service}' is experiencing issues",
                    "description": f"The {service} service in {namespace} needs investigation."
                },
                "startsAt": datetime.now().isoformat() + "Z",
                "fingerprint": f"{service}-{int(time.time())}",
            },
            expected_service=service,
            expected_namespace=namespace,
            description=f"Custom alert for {service} service"
        )


# =============================================================================
# Test Runner
# =============================================================================

class AlertPipelineTestRunner:
    """Runs tests against the alert pipeline."""
    
    def __init__(
        self,
        prometheus_url: str = None,
        vllm_url: str = None,
        namespace: str = "target-services",
        context_only: bool = False,
    ):
        self.prometheus_url = prometheus_url or config.prometheus_url
        self.vllm_url = vllm_url or config.vllm_url
        self.namespace = namespace
        self.context_only = context_only
        
        # Initialize components
        self.prometheus = IstioPrometheusQuerier(self.prometheus_url)
        self.aggregator = AlertContextAggregator(
            prometheus_url=self.prometheus_url,
            namespace=namespace
        )
        
        if not context_only:
            # Create pipeline config
            test_config = PipelineConfig()
            test_config.prometheus_url = self.prometheus_url
            test_config.vllm_url = self.vllm_url
            test_config.target_namespace = namespace
            
            self.pipeline = AlertPipeline(test_config)
        else:
            self.pipeline = None
    
    def test_prometheus_connection(self) -> bool:
        """Test Prometheus connectivity."""
        print("\n" + "=" * 60)
        print("TEST: Prometheus Connection")
        print("=" * 60)
        
        try:
            result = self.prometheus.query("up")
            if result:
                print(f"✓ Connected to Prometheus")
                print(f"  Found {len(result)} 'up' metrics")
                return True
            else:
                print("✗ No data returned from Prometheus")
                return False
        except Exception as e:
            print(f"✗ Failed to connect: {e}")
            return False
    
    def test_istio_metrics(self) -> bool:
        """Test Istio metrics availability."""
        print("\n" + "=" * 60)
        print("TEST: Istio Metrics")
        print("=" * 60)
        
        queries = [
            ("istio_requests_total", "Istio request counter"),
            ("istio_request_duration_milliseconds_bucket", "Istio latency histogram"),
        ]
        
        all_passed = True
        for query, description in queries:
            result = self.prometheus.query(query)
            if result:
                print(f"✓ {description}: {len(result)} series found")
            else:
                print(f"✗ {description}: No data")
                all_passed = False
        
        return all_passed
    
    def test_service_dependencies(self, service: str = "reviews") -> Dict:
        """Test Istio dependency discovery for a service."""
        print("\n" + "=" * 60)
        print(f"TEST: Service Dependencies for '{service}'")
        print("=" * 60)
        
        upstream = self.prometheus.get_upstream_dependencies(self.namespace, service)
        downstream = self.prometheus.get_downstream_dependencies(self.namespace, service)
        
        print(f"\nUpstream Dependencies (services {service} calls):")
        if upstream:
            for dep in upstream:
                print(f"  → {dep['service_name']} ({dep['namespace']}): {dep['requests_per_second']} rps")
        else:
            print("  None found")
        
        print(f"\nDownstream Dependencies (services that call {service}):")
        if downstream:
            for dep in downstream:
                print(f"  ← {dep['service_name']} ({dep['namespace']}): {dep['requests_per_second']} rps")
        else:
            print("  None found")
        
        return {
            "upstream": upstream,
            "downstream": downstream
        }
    
    def test_context_aggregation(self, alert: TestAlert) -> AlertContext:
        """Test context aggregation for an alert."""
        print("\n" + "=" * 60)
        print(f"TEST: Context Aggregation for '{alert.name}'")
        print("=" * 60)
        print(f"Description: {alert.description}")
        
        start_time = time.time()
        context = self.aggregator.aggregate_from_alertmanager(alert.payload)
        elapsed = time.time() - start_time
        
        print(f"\nAggregation completed in {elapsed:.2f}s")
        print(f"\nContext Summary:")
        print(f"  Alert: {context.alert_name}")
        print(f"  Service: {context.service_name}")
        print(f"  Namespace: {context.namespace}")
        print(f"  Status: {context.status}")
        print(f"  Severity: {context.severity}")
        
        print(f"\nService Status: {json.dumps(context.service_status, indent=4)}")
        print(f"Deployment Status: {json.dumps(context.deployment_status, indent=4)}")
        
        print(f"\nPods Found: {len(context.pod_statuses)}")
        for pod in context.pod_statuses[:3]:
            print(f"  - {pod.get('pod', 'unknown')}: {pod.get('phase', 'unknown')}")
        
        print(f"\nUpstream Dependencies: {len(context.upstream_dependencies)}")
        for dep in context.upstream_dependencies[:3]:
            print(f"  → {dep['service_name']}: {dep['requests_per_second']} rps")
        
        print(f"\nDownstream Dependencies: {len(context.downstream_dependencies)}")
        for dep in context.downstream_dependencies[:3]:
            print(f"  ← {dep['service_name']}: {dep['requests_per_second']} rps")
        
        print(f"\nError Rate: {json.dumps(context.error_rate, indent=4)}")
        print(f"Latency: {json.dumps(context.latency, indent=4)}")
        
        print(f"\nLogs Collected: {len(context.recent_logs)} pods")
        for pod, logs in list(context.recent_logs.items())[:2]:
            log_preview = logs[:200] if logs else "No logs"
            print(f"  {pod}: {log_preview}...")
        
        return context
    
    def test_full_pipeline(self, alert: TestAlert) -> Dict:
        """Test the full alert pipeline including LLM remediation."""
        print("\n" + "=" * 60)
        print(f"TEST: Full Pipeline for '{alert.name}'")
        print("=" * 60)
        print(f"Description: {alert.description}")
        
        if self.context_only:
            print("\n⚠ Context-only mode - skipping LLM orchestrator")
            context = self.test_context_aggregation(alert)
            return {
                "status": "context_only",
                "context": context,
            }
        
        if not self.pipeline:
            print("✗ Pipeline not initialized")
            return {"status": "error", "error": "Pipeline not initialized"}
        
        print("\nSending alert to pipeline...")
        start_time = time.time()
        
        result = self.pipeline.process_alert(alert.payload)
        
        elapsed = time.time() - start_time
        
        print(f"\nPipeline Result:")
        print(f"  Status: {result.get('status')}")
        print(f"  Success: {result.get('success')}")
        print(f"  Iterations: {result.get('iterations')}")
        print(f"  Actions Taken: {result.get('actions_taken')}")
        print(f"  Elapsed: {elapsed:.2f}s")
        
        if result.get('context_summary'):
            print(f"\nContext Summary:")
            for k, v in result['context_summary'].items():
                print(f"  {k}: {v}")
        
        if result.get('final_response'):
            print(f"\nFinal Response (truncated):")
            print(result['final_response'][:500])
        
        return result
    
    def test_webhook_simulation(self, alert: TestAlert, 
                                 webhook_url: str = "http://localhost:9095/webhook") -> Dict:
        """Test by sending an actual webhook request."""
        print("\n" + "=" * 60)
        print(f"TEST: Webhook Simulation for '{alert.name}'")
        print("=" * 60)
        print(f"Webhook URL: {webhook_url}")
        
        # AlertManager sends alerts in a wrapper
        payload = {
            "version": "4",
            "groupKey": f"group-{alert.name}",
            "status": "firing",
            "receiver": "cb-model-webhook",
            "alerts": [alert.payload],
        }
        
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=300  # 5 minutes for remediation
            )
            
            result = response.json()
            print(f"\nWebhook Response:")
            print(json.dumps(result, indent=2))
            
            return result
            
        except requests.exceptions.ConnectionError:
            print(f"✗ Could not connect to webhook at {webhook_url}")
            print("  Make sure the alert pipeline is running:")
            print("  python alert_pipeline.py")
            return {"status": "error", "error": "Connection refused"}
        except Exception as e:
            print(f"✗ Error: {e}")
            return {"status": "error", "error": str(e)}


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Alert Pipeline Integration Tests")
    parser.add_argument(
        "--alert",
        type=str,
        choices=["BookinfoReviewsDown", "PredictedCpuBreach", 
                 "CS_Memory_Exhaustion_Predicted", "CS_CPU_High_Anomaly_Detected"],
        default="BookinfoReviewsDown",
        help="Alert to test"
    )
    parser.add_argument(
        "--service",
        type=str,
        help="Custom service to test (creates a generic alert)"
    )
    parser.add_argument(
        "--context-only",
        action="store_true",
        help="Only test context aggregation, skip LLM"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local URLs (localhost)"
    )
    parser.add_argument(
        "--prometheus-url",
        type=str,
        help="Override Prometheus URL"
    )
    parser.add_argument(
        "--vllm-url",
        type=str,
        help="Override vLLM URL"
    )
    parser.add_argument(
        "--namespace",
        type=str,
        default="target-services",
        help="Target namespace"
    )
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="Test via actual webhook call"
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        default="http://localhost:9095/webhook",
        help="Webhook URL for webhook test"
    )
    parser.add_argument(
        "--dependencies-only",
        action="store_true",
        help="Only test Istio dependency discovery"
    )
    
    args = parser.parse_args()
    
    # Set URLs
    if args.local:
        prometheus_url = "http://localhost:9090"
        vllm_url = "http://localhost:8000/v1"
    else:
        prometheus_url = args.prometheus_url or config.prometheus_url
        vllm_url = args.vllm_url or config.vllm_url
    
    print("=" * 60)
    print("ALERT PIPELINE INTEGRATION TESTS")
    print("=" * 60)
    print(f"Prometheus: {prometheus_url}")
    print(f"vLLM: {vllm_url}")
    print(f"Namespace: {args.namespace}")
    print(f"Context Only: {args.context_only}")
    
    # Create test runner
    runner = AlertPipelineTestRunner(
        prometheus_url=prometheus_url,
        vllm_url=vllm_url,
        namespace=args.namespace,
        context_only=args.context_only,
    )
    
    # Test Prometheus connection
    if not runner.test_prometheus_connection():
        print("\n⚠ Prometheus connection failed - some tests may fail")
    
    # Test Istio metrics
    runner.test_istio_metrics()
    
    # If dependencies-only, just test that
    if args.dependencies_only:
        service = args.service or "reviews"
        runner.test_service_dependencies(service)
        return
    
    # Get test alert
    if args.service:
        test_alert = TestAlerts.custom_service_alert(args.service, args.namespace)
    else:
        alert_map = {
            "BookinfoReviewsDown": TestAlerts.bookinfo_reviews_down,
            "PredictedCpuBreach": TestAlerts.predicted_cpu_breach,
            "CS_Memory_Exhaustion_Predicted": TestAlerts.cs_memory_exhaustion,
            "CS_CPU_High_Anomaly_Detected": TestAlerts.cs_cpu_anomaly,
        }
        test_alert = alert_map[args.alert]()
    
    # Test service dependencies
    runner.test_service_dependencies(test_alert.expected_service)
    
    # Run the main test
    if args.webhook:
        # Test via webhook
        result = runner.test_webhook_simulation(test_alert, args.webhook_url)
    else:
        # Direct test
        result = runner.test_full_pipeline(test_alert)
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Alert: {test_alert.name}")
    print(f"Result: {result.get('status', 'unknown')}")
    
    if result.get('success'):
        print("✓ TEST PASSED")
    elif args.context_only:
        print("✓ CONTEXT AGGREGATION COMPLETE")
    else:
        print("✗ TEST FAILED or INCOMPLETE")


if __name__ == "__main__":
    main()
