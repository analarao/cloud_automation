#!/usr/bin/env python3
"""
CB Model Tool Calling Test Client
==================================

Tests vLLM's OpenAI-compatible API with tool calling enabled.
This script verifies Phase 1 is working correctly.

Usage:
    # From local machine with port-forward:
    kubectl port-forward svc/cb-model-service 8000:8000 -n monitoring
    python test_tool_calling.py

    # Or specify a custom endpoint:
    python test_tool_calling.py --host localhost --port 8000
"""

import argparse
import json
import sys
from openai import OpenAI


def create_client(host: str, port: int) -> OpenAI:
    """Create an OpenAI client pointing to vLLM."""
    return OpenAI(
        base_url=f"http://{host}:{port}/v1",
        api_key="dummy"  # vLLM doesn't need a real key
    )


def test_health_check(client: OpenAI, host: str, port: int):
    """Test basic connectivity."""
    print("\n" + "=" * 60)
    print("TEST 1: Health Check")
    print("=" * 60)
    
    import httpx
    try:
        response = httpx.get(f"http://{host}:{port}/health", timeout=10)
        if response.status_code == 200:
            print("✓ Health check passed")
            return True
        else:
            print(f"✗ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False


def test_model_list(client: OpenAI):
    """Test listing available models."""
    print("\n" + "=" * 60)
    print("TEST 2: List Models")
    print("=" * 60)
    
    try:
        models = client.models.list()
        for model in models.data:
            print(f"✓ Available model: {model.id}")
        return True
    except Exception as e:
        print(f"✗ Failed to list models: {e}")
        return False


def test_simple_completion(client: OpenAI):
    """Test a simple completion without tools."""
    print("\n" + "=" * 60)
    print("TEST 3: Simple Completion (no tools)")
    print("=" * 60)
    
    try:
        response = client.chat.completions.create(
            model=client.models.list().data[0].id,  # Use first available model
            messages=[
                {"role": "user", "content": "What is 2+2? Answer in one word."}
            ],
            max_tokens=50
        )
        print(f"✓ Response: {response.choices[0].message.content}")
        return True
    except Exception as e:
        print(f"✗ Completion failed: {e}")
        return False


def test_tool_calling(client: OpenAI):
    """Test tool calling capability."""
    print("\n" + "=" * 60)
    print("TEST 4: Tool Calling (required)")
    print("=" * 60)
    
    # Define a simple tool
    tools = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_get",
                "description": "Get Kubernetes resources using kubectl get command",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resource_type": {
                            "type": "string",
                            "description": "The type of resource to get (e.g., pods, deployments, services)"
                        },
                        "namespace": {
                            "type": "string",
                            "description": "The namespace to query"
                        },
                        "name": {
                            "type": "string",
                            "description": "Optional: specific resource name"
                        }
                    },
                    "required": ["resource_type"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "kubectl_scale",
                "description": "Scale a Kubernetes deployment",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "deployment": {
                            "type": "string",
                            "description": "The deployment name to scale"
                        },
                        "namespace": {
                            "type": "string",
                            "description": "The namespace of the deployment"
                        },
                        "replicas": {
                            "type": "integer",
                            "description": "The desired number of replicas"
                        }
                    },
                    "required": ["deployment", "namespace", "replicas"]
                }
            }
        }
    ]
    
    try:
        model_id = client.models.list().data[0].id
        
        # Test with tool_choice="required" - forces tool call with structured output
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": "You are a Kubernetes administrator. Use the available tools to help manage the cluster."
                },
                {
                    "role": "user",
                    "content": "List all pods in the target-services namespace."
                }
            ],
            tools=tools,
            tool_choice="required",  # Changed from "auto" to "required"
            max_tokens=512
        )
        
        message = response.choices[0].message
        
        if message.tool_calls:
            print("✓ Tool calling works! Model generated tool calls:")
            for tool_call in message.tool_calls:
                print(f"  - Function: {tool_call.function.name}")
                print(f"    Arguments: {tool_call.function.arguments}")
            return True
        else:
            print(f"⚠ No tool calls generated. Model response: {message.content}")
            print("  This is unexpected with tool_choice='required'")
            return False
            
    except Exception as e:
        print(f"✗ Tool calling failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_named_function(client: OpenAI):
    """Test named function calling (forcing a specific tool)."""
    print("\n" + "=" * 60)
    print("TEST 5: Named Function Calling (forced)")
    print("=" * 60)
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_get",
                "description": "Get Kubernetes resources",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resource_type": {
                            "type": "string",
                            "description": "The type of resource"
                        },
                        "namespace": {
                            "type": "string",
                            "description": "The namespace"
                        }
                    },
                    "required": ["resource_type"]
                }
            }
        }
    ]
    
    try:
        model_id = client.models.list().data[0].id
        
        # Force the model to use kubectl_get
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "user", "content": "Get pod information"}
            ],
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "kubectl_get"}},
            max_tokens=512
        )
        
        message = response.choices[0].message
        
        if message.tool_calls:
            tool_call = message.tool_calls[0]
            if tool_call.function.name == "kubectl_get":
                print("✓ Named function calling works!")
                print(f"  Function: {tool_call.function.name}")
                print(f"  Arguments: {tool_call.function.arguments}")
                return True
            else:
                print(f"✗ Expected kubectl_get, got {tool_call.function.name}")
                return False
        else:
            print("✗ No tool calls generated despite tool_choice being set")
            return False
            
    except Exception as e:
        print(f"✗ Named function calling failed: {e}")
        return False


def test_alert_analysis_with_tools(client: OpenAI):
    """Test a realistic alert analysis scenario with tools."""
    print("\n" + "=" * 60)
    print("TEST 6: Alert Analysis with Tool Calling")
    print("=" * 60)
    
    # Define MCP-like tools
    tools = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_get",
                "description": "Get Kubernetes resources (pods, deployments, services, etc.)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resource_type": {"type": "string"},
                        "namespace": {"type": "string"},
                        "name": {"type": "string"},
                        "output_format": {"type": "string", "enum": ["json", "yaml", "wide"]}
                    },
                    "required": ["resource_type"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "kubectl_describe",
                "description": "Describe a Kubernetes resource to get detailed information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resource_type": {"type": "string"},
                        "name": {"type": "string"},
                        "namespace": {"type": "string"}
                    },
                    "required": ["resource_type", "name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "kubectl_logs",
                "description": "Get logs from a pod",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pod_name": {"type": "string"},
                        "namespace": {"type": "string"},
                        "container": {"type": "string"},
                        "tail_lines": {"type": "integer"}
                    },
                    "required": ["pod_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "kubectl_scale",
                "description": "Scale a deployment to a specified number of replicas",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "deployment": {"type": "string"},
                        "namespace": {"type": "string"},
                        "replicas": {"type": "integer"}
                    },
                    "required": ["deployment", "namespace", "replicas"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "kubectl_rollout",
                "description": "Manage deployment rollouts (undo, status, restart)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["undo", "status", "restart"]},
                        "deployment": {"type": "string"},
                        "namespace": {"type": "string"}
                    },
                    "required": ["action", "deployment", "namespace"]
                }
            }
        }
    ]
    
    # Simulated alert context
    alert_context = """
ALERT: High Memory Usage Detected

Severity: warning
Service: cart-service
Namespace: target-services

Alert Details:
- Memory usage at 92% of limit (1.84GB / 2GB)
- Trend: increasing over last 30 minutes
- Pod restarts: 2 in last hour

Recent Logs:
[ERROR] 2024-12-18T10:45:23Z OutOfMemoryError: Java heap space
[WARN] 2024-12-18T10:44:18Z GC overhead limit exceeded
[ERROR] 2024-12-18T10:43:55Z Failed to allocate memory for request handler

Metrics:
- CPU: 45% (stable)
- Memory: 92% (increasing)
- Request latency p99: 2.3s (normal: 200ms)

Please diagnose this issue and take appropriate action to remediate it.
"""
    
    try:
        model_id = client.models.list().data[0].id
        
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": """You are CB (Container-Brain), an expert SRE AI assistant.
Your role is to analyze alerts and take remediation actions using the available tools.

When you receive an alert, use the available tools to:
1. First gather more information using kubectl_get, kubectl_describe, or kubectl_logs
2. Then take remediation action (scale, rollout undo, etc.)

You MUST use the tools to take action. Do not just explain - act."""
                },
                {
                    "role": "user",
                    "content": alert_context
                }
            ],
            tools=tools,
            tool_choice="required",  # Force tool usage
            max_tokens=1024
        )
        
        message = response.choices[0].message
        
        print("Model Response:")
        if message.content:
            print(f"  Text: {message.content[:300]}...")
        
        if message.tool_calls:
            print(f"\n✓ Generated {len(message.tool_calls)} tool call(s):")
            for i, tool_call in enumerate(message.tool_calls, 1):
                print(f"\n  [{i}] {tool_call.function.name}")
                try:
                    args = json.loads(tool_call.function.arguments)
                    print(f"      Arguments: {json.dumps(args, indent=8)}")
                except:
                    print(f"      Arguments: {tool_call.function.arguments}")
            return True
        else:
            print("\n✗ No tool calls generated (unexpected with tool_choice='required')")
            return False
            
    except Exception as e:
        print(f"✗ Alert analysis test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Test CB Model Tool Calling")
    parser.add_argument("--host", default="localhost", help="vLLM server host")
    parser.add_argument("--port", type=int, default=8000, help="vLLM server port")
    parser.add_argument("--test", choices=["health", "models", "completion", "tools", "named", "alert", "all"],
                        default="all", help="Which test to run")
    args = parser.parse_args()
    
    print("=" * 60)
    print("CB Model Tool Calling Test Suite")
    print("=" * 60)
    print(f"Target: http://{args.host}:{args.port}")
    
    client = create_client(args.host, args.port)
    
    results = {}
    
    if args.test in ["health", "all"]:
        results["health"] = test_health_check(client, args.host, args.port)
    
    if args.test in ["models", "all"]:
        results["models"] = test_model_list(client)
    
    if args.test in ["completion", "all"]:
        results["completion"] = test_simple_completion(client)
    
    if args.test in ["tools", "all"]:
        results["tools"] = test_tool_calling(client)
    
    if args.test in ["named", "all"]:
        results["named"] = test_named_function(client)
    
    if args.test in ["alert", "all"]:
        results["alert"] = test_alert_analysis_with_tools(client)
    
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
        print("\n🎉 All tests passed! Phase 1 is complete.")
        print("   vLLM tool calling is working correctly.")
        print("\n   Next steps:")
        print("   - Phase 2: Enable MCP server in CB pod")
        print("   - Phase 3: Build orchestrator loop")
    else:
        print("\n⚠ Some tests failed. Check the output above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
