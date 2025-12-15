#!/usr/bin/env python3
"""
CB Model Test Client
Test script to verify the CB (Container-Brain) LLM gRPC service is working.

Usage:
    # From local machine with port-forward:
    kubectl port-forward svc/cb-model-service 50051:50051 -n monitoring
    python test_cb_model.py

    # Or from within the cluster:
    python test_cb_model.py --host cb-model-service.monitoring.svc.cluster.local
"""

import argparse
import sys
import os

# Add the cb_model directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import grpc
    import cb_model_pb2
    import cb_model_pb2_grpc
except ImportError:
    print("Error: gRPC modules not found. Generate them first:")
    print("  python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cb_model.proto")
    sys.exit(1)


def test_health_check(stub):
    """Test the health check endpoint."""
    print("\n" + "="*60)
    print("TEST 1: Health Check")
    print("="*60)
    
    try:
        response = stub.HealthCheck(cb_model_pb2.HealthCheckRequest(), timeout=10)
        print(f"✓ Healthy: {response.healthy}")
        print(f"✓ Model Loaded: {response.model_loaded}")
        print(f"✓ Status: {response.status}")
        print(f"✓ GPU Memory Usage: {response.gpu_memory_usage:.1f}%")
        return response.healthy and response.model_loaded
    except grpc.RpcError as e:
        print(f"✗ Health check failed: {e.code()} - {e.details()}")
        return False


def test_model_info(stub):
    """Test the model info endpoint."""
    print("\n" + "="*60)
    print("TEST 2: Model Info")
    print("="*60)
    
    try:
        response = stub.GetModelInfo(cb_model_pb2.ModelInfoRequest(), timeout=10)
        print(f"✓ Model Name: {response.model_name}")
        print(f"✓ Max Context Length: {response.max_context_length}")
        print(f"✓ Data Type: {response.dtype}")
        print(f"✓ GPU Info: {response.gpu_info}")
        print(f"✓ Quantization: {response.quantization}")
        return True
    except grpc.RpcError as e:
        print(f"✗ Get model info failed: {e.code()} - {e.details()}")
        return False


def test_simple_completion(stub):
    """Test a simple completion request."""
    print("\n" + "="*60)
    print("TEST 3: Simple Completion")
    print("="*60)
    
    prompt = "What is Kubernetes? Answer in one sentence."
    
    try:
        request = cb_model_pb2.CompletionRequest(
            prompt=prompt,
            max_tokens=100,
            temperature=0.0,
            request_id="test-simple-001",
            source="test_client"
        )
        
        print(f"→ Prompt: {prompt}")
        print("→ Generating completion...")
        
        response = stub.GenerateCompletion(request, timeout=60)
        
        print(f"\n✓ Completion: {response.completion}")
        print(f"✓ Prompt Tokens: {response.prompt_tokens}")
        print(f"✓ Completion Tokens: {response.completion_tokens}")
        print(f"✓ Generation Time: {response.generation_time_ms}ms")
        print(f"✓ Finish Reason: {response.finish_reason}")
        return True
    except grpc.RpcError as e:
        print(f"✗ Completion failed: {e.code()} - {e.details()}")
        return False


def test_alert_analysis(stub):
    """Test an alert analysis request (simulating CS/AlertManager)."""
    print("\n" + "="*60)
    print("TEST 4: Alert Analysis (MCP Command Generation)")
    print("="*60)
    
    # Simulate an alert from CS model
    alert_context = """
ALERT CONTEXT:
- Alert Type: SLO_PREDICTION
- Service: cart-service
- Namespace: target-services
- Predicted Latency Breach: 95th percentile will exceed 500ms in 15 minutes

LOGS (from cart-service):
{"level": "error", "message": "java.lang.OutOfMemoryError: Java heap space", "pod": "cart-service-abc123"}
{"level": "warn", "message": "GC overhead limit exceeded", "pod": "cart-service-abc123"}

TOPOLOGY:
- Upstream: api-gateway
- Downstream: redis-cache, inventory-service

METRICS:
- Current Memory Usage: 95%
- CPU Usage: 45%
- Request Rate: 1200 req/s

Based on this context, what is the root cause and what MCP commands should be executed?
"""
    
    try:
        request = cb_model_pb2.CompletionRequest(
            prompt=alert_context,
            # System prompt will use default from environment
            max_tokens=500,
            temperature=0.3,  # Lower temperature for more deterministic output
            request_id="test-alert-001",
            source="test_client_simulating_cs_model"
        )
        
        print("→ Sending simulated alert context...")
        print("→ Generating MCP command recommendations...")
        
        response = stub.GenerateCompletion(request, timeout=120)
        
        print(f"\n✓ LLM Response:\n{'-'*40}")
        print(response.completion)
        print(f"{'-'*40}")
        print(f"✓ Generation Time: {response.generation_time_ms}ms")
        print(f"✓ Tokens Used: {response.total_tokens}")
        return True
    except grpc.RpcError as e:
        print(f"✗ Alert analysis failed: {e.code()} - {e.details()}")
        return False


def test_custom_system_prompt(stub):
    """Test with a custom system prompt override."""
    print("\n" + "="*60)
    print("TEST 5: Custom System Prompt Override")
    print("="*60)
    
    custom_system_prompt = """
You are a Kubernetes expert. 
When given a problem, respond ONLY with kubectl commands that solve it.
Do not explain, just output the commands.
"""
    
    prompt = "Scale the deployment 'web-app' in namespace 'production' to 5 replicas"
    
    try:
        request = cb_model_pb2.CompletionRequest(
            prompt=prompt,
            system_prompt=custom_system_prompt,
            max_tokens=100,
            temperature=0.1,
            request_id="test-custom-001",
            source="test_client"
        )
        
        print(f"→ Custom System Prompt: {custom_system_prompt[:50]}...")
        print(f"→ User Prompt: {prompt}")
        
        response = stub.GenerateCompletion(request, timeout=60)
        
        print(f"\n✓ Response: {response.completion}")
        return True
    except grpc.RpcError as e:
        print(f"✗ Custom system prompt test failed: {e.code()} - {e.details()}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test the CB Model gRPC service")
    parser.add_argument("--host", default="localhost", help="gRPC server host")
    parser.add_argument("--port", default="50051", help="gRPC server port")
    parser.add_argument("--test", choices=["health", "info", "simple", "alert", "custom", "all"],
                        default="all", help="Which test to run")
    args = parser.parse_args()
    
    address = f"{args.host}:{args.port}"
    print(f"\n{'#'*60}")
    print(f"CB Model (Container-Brain) Test Client")
    print(f"{'#'*60}")
    print(f"Connecting to: {address}")
    
    # Create gRPC channel
    channel = grpc.insecure_channel(address)
    
    # Check if channel is ready
    try:
        grpc.channel_ready_future(channel).result(timeout=10)
        print("✓ Connected to gRPC server")
    except grpc.FutureTimeoutError:
        print("✗ Failed to connect to gRPC server")
        print("\nMake sure the service is running and port-forwarded:")
        print("  kubectl port-forward svc/cb-model-service 50051:50051 -n monitoring")
        sys.exit(1)
    
    stub = cb_model_pb2_grpc.CBModelServiceStub(channel)
    
    # Run tests
    results = {}
    
    if args.test in ["health", "all"]:
        results["Health Check"] = test_health_check(stub)
    
    if args.test in ["info", "all"]:
        results["Model Info"] = test_model_info(stub)
    
    if args.test in ["simple", "all"]:
        results["Simple Completion"] = test_simple_completion(stub)
    
    if args.test in ["alert", "all"]:
        results["Alert Analysis"] = test_alert_analysis(stub)
    
    if args.test in ["custom", "all"]:
        results["Custom System Prompt"] = test_custom_system_prompt(stub)
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False
    
    print("="*60)
    
    channel.close()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
