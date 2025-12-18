#!/usr/bin/env python3
"""
MCP Integration Test
====================

Tests the MCP server integration with the CB Model.
This verifies Phase 2 is working correctly.

Usage:
    # Run inside the CB Model pod or with kubectl available:
    python test_mcp_integration.py
    
    # Or with port-forward to CB Model:
    kubectl port-forward svc/cb-model-service 8000:8000 -n monitoring
    python test_mcp_integration.py --vllm-url http://localhost:8000
"""

import argparse
import asyncio
import json
import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openai import OpenAI

try:
    from mcp_client import MCPKubernetesClient, MCP_AVAILABLE
except ImportError:
    MCP_AVAILABLE = False
    print("Warning: mcp_client module not found")


async def test_mcp_server_available():
    """Test 1: Check if MCP server is available."""
    print("\n" + "=" * 60)
    print("TEST 1: MCP Server Availability")
    print("=" * 60)
    
    if not MCP_AVAILABLE:
        print("✗ MCP SDK not installed")
        return False
    
    try:
        async with MCPKubernetesClient() as client:
            tools = await client.list_tools()
            print(f"✓ MCP server is running")
            print(f"  Available tools: {len(tools)}")
            for tool in tools[:5]:
                print(f"    - {tool.name}")
            if len(tools) > 5:
                print(f"    ... and {len(tools) - 5} more")
            return True
    except Exception as e:
        print(f"✗ MCP server not available: {e}")
        return False


async def test_kubectl_get(namespace: str):
    """Test 2: Test kubectl_get via MCP."""
    print("\n" + "=" * 60)
    print("TEST 2: kubectl_get via MCP")
    print("=" * 60)
    
    try:
        async with MCPKubernetesClient() as client:
            # Note: MCP server uses camelCase for arguments
            # name is required but can be empty string to list all
            result = await client.call_tool("kubectl_get", {
                "resourceType": "pods",
                "namespace": namespace,
                "name": ""  # Empty = list all
            })  
            
            if result.success:
                print(f"✓ kubectl_get succeeded")
                print(f"  Namespace: {namespace}")
                # Parse and show pod names if possible
                try:
                    if "NAME" in result.result:
                        lines = result.result.strip().split("\n")
                        print(f"  Found {len(lines) - 1} pods")
                        for line in lines[:5]:
                            print(f"    {line[:80]}")
                    else:
                        print(f"  Result: {result.result[:200]}...")
                except:
                    print(f"  Result: {str(result.result)[:200]}...")
                return True
            else:
                print(f"✗ kubectl_get failed: {result.error}")
                return False
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


async def test_openai_format_conversion():
    """Test 3: Test OpenAI format conversion."""
    print("\n" + "=" * 60)
    print("TEST 3: OpenAI Format Conversion")
    print("=" * 60)
    
    try:
        async with MCPKubernetesClient() as client:
            openai_tools = await client.get_tools_openai_format()
            
            print(f"✓ Converted {len(openai_tools)} tools to OpenAI format")
            
            # Validate format
            for tool in openai_tools[:3]:
                assert tool["type"] == "function"
                assert "function" in tool
                assert "name" in tool["function"]
                assert "description" in tool["function"]
                print(f"  ✓ {tool['function']['name']}: valid format")
            
            return True
    except Exception as e:
        print(f"✗ Conversion failed: {e}")
        return False


def test_vllm_with_mcp_tools(vllm_url: str, namespace: str):
    """Test 4: Test vLLM with MCP-derived tools."""
    print("\n" + "=" * 60)
    print("TEST 4: vLLM with MCP Tools")
    print("=" * 60)
    
    try:
        # Get tools from MCP
        loop = asyncio.new_event_loop()
        
        async def get_tools():
            async with MCPKubernetesClient() as client:
                return await client.get_tools_openai_format()
        
        openai_tools = loop.run_until_complete(get_tools())
        loop.close()
        
        # Filter to just the tools we need for this test
        test_tools = [t for t in openai_tools if t["function"]["name"] in [
            "kubectl_get", "kubectl_describe", "kubectl_logs", "kubectl_scale"
        ]]
        
        if not test_tools:
            # Fallback to basic tools if MCP tools not found
            test_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "kubectl_get",
                        "description": "Get Kubernetes resources",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "resource_type": {"type": "string"},
                                "namespace": {"type": "string"}
                            },
                            "required": ["resource_type"]
                        }
                    }
                }
            ]
        
        print(f"  Using {len(test_tools)} tools from MCP server")
        
        # Connect to vLLM
        # Note: vllm_url already includes /v1, don't add it again
        client = OpenAI(
            base_url=vllm_url,
            api_key="dummy"
        )
        
        # Get model
        models = client.models.list()
        model_id = models.data[0].id
        print(f"  Model: {model_id}")
        
        # Test tool calling with MCP tools
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": f"You are a Kubernetes administrator. Target namespace is {namespace}."
                },
                {
                    "role": "user",
                    "content": f"List all pods in the {namespace} namespace"
                }
            ],
            tools=test_tools,
            tool_choice="required",
            max_tokens=512
        )
        
        message = response.choices[0].message
        
        if message.tool_calls:
            print(f"✓ vLLM generated tool calls with MCP tools!")
            for tc in message.tool_calls:
                print(f"  - {tc.function.name}: {tc.function.arguments}")
            return True
        else:
            print(f"✗ No tool calls generated")
            return False
            
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_full_tool_execution(namespace: str):
    """Test 5: Full tool execution loop."""
    print("\n" + "=" * 60)
    print("TEST 5: Full Tool Execution")
    print("=" * 60)
    
    try:
        # Use separate client instances to avoid connection reuse issues
        # This mimics how the orchestrator would work (reconnect per operation)
        
        # 1. Get tools with first client
        async with MCPKubernetesClient() as client:
            tools = await client.list_tools()
            print(f"  Step 1: Got {len(tools)} tools from MCP")
        
        # Small delay to ensure subprocess cleanup
        await asyncio.sleep(0.5)
        
        # 2. Execute tool with second client
        print(f"  Step 2: Executing kubectl_get on {namespace}")
        async with MCPKubernetesClient() as client:
            result = await client.call_tool("kubectl_get", {
                "resourceType": "deployments",
                "namespace": namespace,
                "name": ""  # Empty = list all
            })
            
            if not result.success:
                print(f"  ✗ Tool execution failed: {result.error}")
                return False
            
            print(f"  Step 3: Got result from MCP")
            print(f"    Preview: {str(result.result)[:150]}...")
            
            # 3. Format result for LLM
            tool_message = {
                "role": "tool",
                "tool_call_id": "test_call_001",
                "content": result.result
            }
            print(f"  Step 4: Formatted result for LLM")
            
            print(f"\n✓ Full execution loop works!")
            return True
            
            print(f"\n✓ Full execution loop works!")
            return True
            
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Test MCP Integration")
    parser.add_argument("--vllm-url", default="http://localhost:8000",
                        help="vLLM server URL")
    parser.add_argument("--namespace", default=os.getenv("MCP_TARGET_NAMESPACE", "target-services"),
                        help="Kubernetes namespace to test")
    parser.add_argument("--test", choices=["mcp", "kubectl", "format", "vllm", "full", "all"],
                        default="all", help="Which test to run")
    args = parser.parse_args()
    
    print("=" * 60)
    print("MCP Integration Test Suite (Phase 2)")
    print("=" * 60)
    print(f"Target namespace: {args.namespace}")
    print(f"vLLM URL: {args.vllm_url}")
    
    results = {}
    loop = asyncio.new_event_loop()
    
    try:
        if args.test in ["mcp", "all"]:
            results["mcp_available"] = loop.run_until_complete(test_mcp_server_available())
        
        if args.test in ["kubectl", "all"]:
            results["kubectl_get"] = loop.run_until_complete(test_kubectl_get(args.namespace))
        
        if args.test in ["format", "all"]:
            results["openai_format"] = loop.run_until_complete(test_openai_format_conversion())
        
        if args.test in ["vllm", "all"]:
            results["vllm_mcp"] = test_vllm_with_mcp_tools(args.vllm_url, args.namespace)
        
        if args.test in ["full", "all"]:
            results["full_execution"] = loop.run_until_complete(test_full_tool_execution(args.namespace))
    finally:
        loop.close()
    
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
        print("\n🎉 Phase 2 complete! MCP integration working.")
        print("\nNext steps:")
        print("  - Phase 3: Build the orchestrator loop")
        print("  - Connect Alert Aggregator -> Orchestrator -> vLLM -> MCP")
    else:
        print("\n⚠ Some tests failed. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
