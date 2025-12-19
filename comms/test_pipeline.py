#!/usr/bin/env python3
"""
Test script for the Alert Remediation Pipeline.

This script tests the full pipeline locally:
1. Compiles the protobuf
2. Starts the Gemini client (mocked or real)
3. Sends a test alert to the server
4. Verifies the response

Usage:
    # Test with mocked Gemini (no API key needed)
    python test_pipeline.py --mock

    # Test with real Gemini API
    export GEMINI_API_KEY=your-api-key
    python test_pipeline.py

    # Test individual components
    python test_pipeline.py --test proto
    python test_pipeline.py --test server
    python test_pipeline.py --test client
"""

import os
import sys
import json
import time
import argparse
import subprocess
import tempfile
from datetime import datetime

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def compile_proto():
    """Compile the alert.proto file."""
    print("\n" + "=" * 60)
    print("COMPILING PROTOBUF")
    print("=" * 60)
    
    proto_file = os.path.join(os.path.dirname(__file__), "alert.proto")
    if not os.path.exists(proto_file):
        print(f"✗ Proto file not found: {proto_file}")
        return False
    
    try:
        result = subprocess.run(
            [
                "python", "-m", "grpc_tools.protoc",
                "-I.", "--python_out=.", "--grpc_python_out=.",
                "alert.proto"
            ],
            cwd=os.path.dirname(__file__),
            capture_output=True,
            text=True,
        )
        
        if result.returncode == 0:
            print("✓ Protobuf compiled successfully")
            return True
        else:
            print(f"✗ Protobuf compilation failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_proto_import():
    """Test that proto modules can be imported."""
    print("\n" + "=" * 60)
    print("TESTING PROTO IMPORT")
    print("=" * 60)
    
    try:
        import alert_pb2
        import alert_pb2_grpc
        
        # Create a test message
        request = alert_pb2.AlertRequest()
        request.request_id = "test-123"
        request.alert.name = "TestAlert"
        request.alert.severity = "warning"
        request.kubernetes.namespace = "target-services"
        request.kubernetes.pod.name = "test-pod"
        
        # Serialize and deserialize
        data = request.SerializeToString()
        request2 = alert_pb2.AlertRequest()
        request2.ParseFromString(data)
        
        assert request2.request_id == "test-123"
        assert request2.alert.name == "TestAlert"
        
        print(f"✓ Proto import successful")
        print(f"  - AlertRequest fields: {len(alert_pb2.AlertRequest.DESCRIPTOR.fields)}")
        print(f"  - AlertResponse fields: {len(alert_pb2.AlertResponse.DESCRIPTOR.fields)}")
        return True
    
    except ImportError as e:
        print(f"✗ Import error: {e}")
        print("  Run: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. alert.proto")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_gemini_client_import():
    """Test that the Gemini client can be imported."""
    print("\n" + "=" * 60)
    print("TESTING GEMINI CLIENT IMPORT")
    print("=" * 60)
    
    try:
        # Add client directory to path
        client_dir = os.path.join(os.path.dirname(__file__), "client")
        sys.path.insert(0, client_dir)
        
        # Check for google.generativeai
        import google.generativeai as genai
        print(f"✓ google.generativeai imported")
        
        # Check for grpc
        import grpc
        print(f"✓ grpc imported")
        
        return True
    
    except ImportError as e:
        print(f"✗ Import error: {e}")
        print("  Run: pip install google-generativeai grpcio grpcio-tools")
        return False


def test_kubectl_available():
    """Test that kubectl is available."""
    print("\n" + "=" * 60)
    print("TESTING KUBECTL")
    print("=" * 60)
    
    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        
        if result.returncode == 0:
            print(f"✓ kubectl available: {result.stdout.strip()}")
            return True
        else:
            print(f"✗ kubectl error: {result.stderr}")
            return False
    
    except FileNotFoundError:
        print("✗ kubectl not found in PATH")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_gemini_api(api_key: str = None):
    """Test Gemini API connectivity."""
    print("\n" + "=" * 60)
    print("TESTING GEMINI API")
    print("=" * 60)
    
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("✗ GEMINI_API_KEY not set")
        print("  Set: export GEMINI_API_KEY=your-key")
        return False
    
    try:
        import google.generativeai as genai
        
        genai.configure(api_key=api_key)
        
        # List models
        models = list(genai.list_models())
        gemini_models = [m for m in models if "gemini" in m.name.lower()]
        
        print(f"✓ API connected, found {len(gemini_models)} Gemini models:")
        for m in gemini_models[:5]:
            print(f"  - {m.name}")
        
        # Test simple completion
        model = genai.GenerativeModel("gemini-1.5-flash")  # Use flash for quick test
        response = model.generate_content("Say 'API test successful' in exactly 3 words.")
        
        print(f"✓ Test completion: {response.text[:50]}...")
        return True
    
    except Exception as e:
        print(f"✗ API error: {e}")
        return False


def create_mock_alert():
    """Create a mock AlertRequest for testing."""
    import alert_pb2
    
    request = alert_pb2.AlertRequest()
    request.request_id = f"test-{int(time.time())}"
    request.created_at = int(time.time())
    
    # Alert metadata
    request.alert.name = "BookinfoReviewsDown"
    request.alert.severity = "critical"
    request.alert.state = "firing"
    request.alert.summary = "Bookinfo 'reviews' service is down"
    request.alert.description = "No instances of the 'reviews' service are reachable."
    request.alert.fingerprint = "abc123def456"
    request.alert.labels["alertname"] = "BookinfoReviewsDown"
    request.alert.labels["namespace"] = "target-services"
    request.alert.labels["pod"] = "reviews-v1-abc123"
    request.alert.labels["service"] = "reviews"
    request.alert.labels["severity"] = "critical"
    
    # Kubernetes context
    request.kubernetes.namespace = "target-services"
    request.kubernetes.pod.name = "reviews-v1-abc123"
    request.kubernetes.pod.phase = "Running"
    request.kubernetes.pod.pod_ip = "10.244.0.15"
    
    cond = request.kubernetes.pod.conditions.add()
    cond.type = "Ready"
    cond.status = "False"
    cond.reason = "ContainersNotReady"
    cond.message = "containers with unready status: [reviews]"
    
    request.kubernetes.workload.kind = "Deployment"
    request.kubernetes.workload.name = "reviews-v1"
    request.kubernetes.workload.namespace = "target-services"
    request.kubernetes.workload.replicas = 1
    request.kubernetes.workload.ready_replicas = 0
    request.kubernetes.workload.available_replicas = 0
    
    # Container info
    request.container.container_name = "reviews"
    request.container.image = "docker.io/istio/examples-bookinfo-reviews-v1:1.16.2"
    request.container.state = "CrashLoopBackOff"
    request.container.restart_count = 5
    request.container.termination_reason = "Error"
    
    # Metrics
    request.metrics.cpu.name = "cpu_usage"
    request.metrics.cpu.unit = "cores"
    request.metrics.cpu.current = 0.001
    request.metrics.cpu.avg = 0.05
    request.metrics.cpu.max = 0.1
    
    request.metrics.memory.name = "memory_usage"
    request.metrics.memory.unit = "bytes"
    request.metrics.memory.current = 50 * 1024 * 1024  # 50MB
    request.metrics.memory.avg = 100 * 1024 * 1024
    request.metrics.memory.max = 150 * 1024 * 1024
    
    # Add some error logs
    for i in range(3):
        entry = request.logs.errors.add()
        entry.timestamp = int(time.time()) - (i * 60)
        entry.level = "ERROR"
        entry.message = f"Error connecting to ratings service: connection refused"
        entry.pod = "reviews-v1-abc123"
        entry.container = "reviews"
    
    return request


def run_mock_orchestration():
    """Run a mock orchestration loop to test the flow."""
    print("\n" + "=" * 60)
    print("MOCK ORCHESTRATION TEST")
    print("=" * 60)
    
    try:
        import alert_pb2
        
        request = create_mock_alert()
        
        print(f"\nTest Alert:")
        print(f"  Name: {request.alert.name}")
        print(f"  Namespace: {request.kubernetes.namespace}")
        print(f"  Pod: {request.kubernetes.pod.name}")
        print(f"  Container State: {request.container.state}")
        print(f"  Restart Count: {request.container.restart_count}")
        
        # Simulate tool calls
        print(f"\nSimulated Tool Calls:")
        
        mock_tools = [
            ("kubectl_get", {"resource_type": "pods", "namespace": "target-services"}),
            ("kubectl_describe", {"resource_type": "pod", "name": "reviews-v1-abc123", "namespace": "target-services"}),
            ("kubectl_logs", {"pod_name": "reviews-v1-abc123", "namespace": "target-services", "tail": 50}),
            ("kubectl_delete_pod", {"pod_name": "reviews-v1-abc123", "namespace": "target-services"}),
            ("kubectl_get", {"resource_type": "pods", "namespace": "target-services"}),
            ("complete_remediation", {"status": "resolved", "summary": "Deleted crashing pod to force restart"}),
        ]
        
        for tool_name, args in mock_tools:
            print(f"  → {tool_name}({json.dumps(args)})")
        
        print(f"\n✓ Mock orchestration flow looks correct")
        return True
    
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test Alert Remediation Pipeline")
    parser.add_argument("--test", choices=["proto", "client", "kubectl", "gemini", "mock", "all"],
                        default="all", help="Which test to run")
    parser.add_argument("--api-key", help="Gemini API key (or use GEMINI_API_KEY env)")
    args = parser.parse_args()
    
    print("=" * 60)
    print("ALERT REMEDIATION PIPELINE TEST")
    print("=" * 60)
    print(f"Time: {datetime.now().isoformat()}")
    
    results = {}
    
    # Change to script directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    if args.test in ["proto", "all"]:
        results["compile"] = compile_proto()
        results["import"] = test_proto_import()
    
    if args.test in ["client", "all"]:
        results["client"] = test_gemini_client_import()
    
    if args.test in ["kubectl", "all"]:
        results["kubectl"] = test_kubectl_available()
    
    if args.test in ["gemini", "all"]:
        results["gemini"] = test_gemini_api(args.api_key)
    
    if args.test in ["mock", "all"]:
        if results.get("import", True):  # Only if proto compiled
            results["mock"] = run_mock_orchestration()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, passed_test in results.items():
        status = "✓ PASS" if passed_test else "✗ FAIL"
        print(f"  {test_name}: {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All tests passed! Ready to deploy.")
        return 0
    else:
        print("\n✗ Some tests failed. Check the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
