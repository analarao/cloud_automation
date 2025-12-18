#!/usr/bin/env python3
"""
CB Model Orchestrator

This module orchestrates the interaction between:
1. The vLLM server (LLM with tool calling)
2. The MCP Kubernetes client (for executing K8s operations)
3. The alert context aggregator (for receiving alerts)

Flow:
1. Receive alert from CS Model via gRPC
2. Send alert to vLLM with available MCP tools
3. Parse tool calls from LLM response
4. Execute tool calls via MCP client
5. Return results to LLM for further reasoning
6. Repeat until remediation complete or max iterations

Phase 3: Orchestrator Loop Implementation
"""

import asyncio
import os
import json
import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from openai import OpenAI

from mcp_client import MCPKubernetesClient, MCPTool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """Represents an alert from the CS Model."""
    alert_name: str
    severity: str
    namespace: str
    pod_name: Optional[str] = None
    deployment_name: Optional[str] = None
    message: str = ""
    labels: Optional[Dict[str, str]] = None
    annotations: Optional[Dict[str, str]] = None
    value: Optional[float] = None
    threshold: Optional[float] = None
    
    def to_prompt(self) -> str:
        """Convert alert to a prompt string for the LLM."""
        prompt = f"""ALERT: {self.alert_name}
Severity: {self.severity}
Namespace: {self.namespace}
"""
        if self.pod_name:
            prompt += f"Pod: {self.pod_name}\n"
        if self.deployment_name:
            prompt += f"Deployment: {self.deployment_name}\n"
        if self.message:
            prompt += f"Message: {self.message}\n"
        if self.value is not None:
            prompt += f"Current Value: {self.value}\n"
        if self.threshold is not None:
            prompt += f"Threshold: {self.threshold}\n"
        if self.labels:
            prompt += f"Labels: {json.dumps(self.labels, indent=2)}\n"
        if self.annotations:
            prompt += f"Annotations: {json.dumps(self.annotations, indent=2)}\n"
        return prompt


@dataclass
class RemediationResult:
    """Result of a remediation attempt."""
    success: bool
    actions_taken: List[Dict[str, Any]]
    final_response: str
    iterations: int
    error: Optional[str] = None


class CBOrchestrator:
    """
    Orchestrates LLM reasoning and MCP tool execution for alert remediation.
    
    This class manages the agentic loop:
    1. Send context to LLM
    2. LLM generates tool calls
    3. Execute tools via MCP
    4. Send results back to LLM
    5. Repeat until done
    """
    
    SYSTEM_PROMPT = """You are an AI-powered Kubernetes operations assistant with FULL access to diagnose and remediate cluster issues.

CRITICAL TOOL USAGE RULES:
1. You MUST use the tool_calls mechanism to invoke tools - NEVER write JSON in your text response
2. When you want to run kubectl_get, CALL the function directly, don't describe it
3. Use DIFFERENT tools in sequence: get → describe → logs → remediate
4. If a resource from an alert doesn't exist, investigate existing resources instead
5. You can operate on resources in the '{namespace}' namespace

DIAGNOSTIC WORKFLOW - Follow these steps in order:
1. kubectl_get pods - list all pods to see current state
2. kubectl_describe on specific pods showing issues
3. kubectl_logs on pods that need investigation  
4. kubectl_generic for advanced commands (top, exec, events)
5. Take remediation action (scale, delete, patch, rollout)
6. Verify the fix with kubectl_get

AVAILABLE TOOLS:
- kubectl_get: Get/list resources (pods, deployments, services, ingresses, networkpolicies)
- kubectl_describe: Get detailed resource info including events and conditions
- kubectl_logs: Get pod logs (use previous=true for crashed containers)
- kubectl_scale: Scale deployments/statefulsets up or down
- kubectl_delete: Delete pods to restart them, or delete stuck resources
- kubectl_patch: Patch resources to update configurations
- kubectl_rollout: Manage deployment rollouts (restart, status, history, undo)
- kubectl_apply: Apply YAML manifests for configuration changes
- kubectl_generic: Execute ANY kubectl command including:
  * top pods - check CPU/memory usage
  * get events - check cluster events
  * exec -it <pod> -- <command> - run commands in pods
- port_forward: Start port forwarding to pods or services
- stop_port_forward: Stop port forwarding sessions

TOOL ARGUMENT FORMAT (use camelCase):
- resourceType: "pods", "deployments", "services", "ingresses", "networkpolicies"
- name: resource name (use empty string "" to list all)
- namespace: use "{namespace}" for primary target

EXAMPLES OF CORRECT TOOL USAGE:
- To list pods: call kubectl_get with resourceType="pods", name="", namespace="{namespace}"
- To describe a pod: call kubectl_describe with resourceType="pods", name="pod-name", namespace="{namespace}"
- To get logs: call kubectl_logs with name="pod-name", namespace="{namespace}"
- To check CPU: call kubectl_generic with command="top pods -n {namespace}"

COMPLETION:
- Say "REMEDIATION COMPLETE:" when the issue is verified fixed
- Say "REMEDIATION FAILED:" when you've exhausted options
- Always verify your fix worked before declaring completion"""

    def __init__(
        self,
        vllm_base_url: str = None,
        vllm_api_key: str = "EMPTY",
        target_namespace: str = "target-services",
        max_iterations: int = 10,
        temperature: float = 0.1,
    ):
        """
        Initialize the orchestrator.
        
        Args:
            vllm_base_url: Base URL for vLLM server (default from env)
            vllm_api_key: API key for vLLM (default "EMPTY")
            target_namespace: Kubernetes namespace to operate on
            max_iterations: Maximum LLM reasoning iterations
            temperature: LLM temperature for generation
        """
        self.vllm_base_url = vllm_base_url or os.getenv(
            "CB_MODEL_OPENAI_API_URL", 
            "http://localhost:8000/v1"
        )
        self.vllm_api_key = vllm_api_key
        self.target_namespace = target_namespace
        self.max_iterations = max_iterations
        self.temperature = temperature
        
        # Initialize OpenAI client for vLLM
        self.client = OpenAI(
            base_url=self.vllm_base_url,
            api_key=self.vllm_api_key
        )
        
        # Track model name
        self.model_name: Optional[str] = None
        
        # Tools in OpenAI format (cached)
        self._tools_cache: Optional[List[Dict[str, Any]]] = None
        
        # Essential tools for remediation (reduces token usage)
        # Full list has 22 tools which uses ~6000 tokens!
        # Added kubectl_generic for exec, port-forwarding, and advanced operations
        self.essential_tools = [
            "kubectl_get",
            "kubectl_describe", 
            "kubectl_logs",
            "kubectl_scale",
            "kubectl_delete",
            "kubectl_patch",
            "kubectl_rollout",
            "kubectl_generic",  # For exec, port-forward, and any custom kubectl command
            "kubectl_apply",    # For applying YAML manifests
            "port_forward",     # For port forwarding to pods/services
            "stop_port_forward", # To clean up port forwards
        ]
        
        logger.info(f"Orchestrator initialized")
        logger.info(f"  vLLM URL: {self.vllm_base_url}")
        logger.info(f"  Target namespace: {self.target_namespace}")
        logger.info(f"  Max iterations: {self.max_iterations}")
    
    def _discover_model(self) -> str:
        """Discover the model name from vLLM."""
        if self.model_name:
            return self.model_name
            
        try:
            models = self.client.models.list()
            if models.data:
                self.model_name = models.data[0].id
                logger.info(f"Discovered model: {self.model_name}")
                return self.model_name
        except Exception as e:
            logger.error(f"Failed to discover model: {e}")
        
        # Fallback
        self.model_name = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
        return self.model_name
    
    async def _load_mcp_tools_async(self) -> List[Dict[str, Any]]:
        """Load tools from MCP and convert to OpenAI format (async)."""
        if self._tools_cache:
            return self._tools_cache
        
        try:
            async with MCPKubernetesClient(
                target_namespace=self.target_namespace
            ) as client:
                mcp_tools = await client.list_tools()
                
                # Filter to essential tools only to reduce token usage
                # Full 22 tools = ~6000 tokens, filtered = ~1500 tokens
                filtered_tools = [
                    tool for tool in mcp_tools 
                    if tool.name in self.essential_tools
                ]
                
                logger.info(f"Filtered {len(mcp_tools)} tools to {len(filtered_tools)} essential tools")
                
                # Convert MCP tools to OpenAI function format
                self._tools_cache = [tool.to_openai_format() for tool in filtered_tools]
                
                logger.info(f"Loaded {len(self._tools_cache)} tools from MCP")
                return self._tools_cache
                
        except Exception as e:
            logger.error(f"Failed to load MCP tools: {e}")
            return []
    
    async def _execute_tool_call_async(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool call via MCP (async).
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments for the tool
            
        Returns:
            Tool execution result
        """
        logger.info(f"Executing tool: {tool_name}")
        logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")
        
        try:
            async with MCPKubernetesClient(
                target_namespace=self.target_namespace
            ) as client:
                result = await client.call_tool(tool_name, arguments)
                
                if result.success:
                    # Truncate long results for logging
                    result_preview = str(result.result)[:500]
                    logger.info(f"Tool succeeded: {result_preview}...")
                    return {"success": True, "result": result.result}
                else:
                    logger.error(f"Tool failed: {result.error}")
                    return {"success": False, "error": result.error}
                    
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def process_alert_async(self, alert: Alert) -> RemediationResult:
        """
        Process an alert through the LLM reasoning loop (async).
        
        Args:
            alert: The alert to process
            
        Returns:
            RemediationResult with details of actions taken
        """
        start_time = time.time()
        logger.info(f"=" * 60)
        logger.info(f"Processing alert: {alert.alert_name}")
        logger.info(f"Severity: {alert.severity}")
        logger.info(f"Namespace: {alert.namespace}")
        logger.info(f"=" * 60)
        
        # Discover model
        self._discover_model()
        
        # Load tools
        tools = await self._load_mcp_tools_async()
        
        if not tools:
            return RemediationResult(
                success=False,
                actions_taken=[],
                final_response="No MCP tools available",
                iterations=0,
                error="Failed to load MCP tools"
            )
        
        logger.info(f"Loaded {len(tools)} tools for LLM")
        
        # Build system prompt with namespace
        system_prompt = self.SYSTEM_PROMPT.format(namespace=self.target_namespace)
        
        # Build initial messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please analyze and remediate the following alert:\n\n{alert.to_prompt()}"}
        ]
        
        actions_taken = []
        iteration = 0
        last_tool_call = None  # Track last tool call to detect loops
        repeated_call_count = 0
        
        try:
            while iteration < self.max_iterations:
                iteration += 1
                logger.info(f"\n--- Iteration {iteration}/{self.max_iterations} ---")
                
                # Determine tool_choice strategy:
                # - First 2 iterations: force tool use to ensure real MCP calls
                # - Later iterations: allow model to finish with "auto"
                # - If stuck in a loop, switch to auto earlier
                if iteration <= 2 and repeated_call_count < 2:
                    # Force tool use for first 2 iterations
                    tool_choice = "required"
                    logger.info("Tool choice: required (forcing tool use)")
                else:
                    # Allow model to finish on later iterations
                    tool_choice = "auto"
                    logger.info("Tool choice: auto (can finish)")
                
                # Call LLM with tools
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=self.temperature,
                    max_tokens=1024  # Reduced to leave room for context
                )
                
                choice = response.choices[0]
                message = choice.message
                
                # Log LLM response
                if message.content:
                    logger.info(f"LLM: {message.content[:200]}...")
                
                # Check for tool calls
                if message.tool_calls:
                    logger.info(f"LLM requested {len(message.tool_calls)} tool call(s)")
                    
                    # Detect repeated tool calls (loop detection)
                    current_call = f"{message.tool_calls[0].function.name}:{message.tool_calls[0].function.arguments}"
                    if current_call == last_tool_call:
                        repeated_call_count += 1
                        logger.warning(f"Detected repeated tool call ({repeated_call_count}x): {message.tool_calls[0].function.name}")
                        if repeated_call_count >= 2:
                            # Add guidance to break out of loop
                            messages.append({
                                "role": "user", 
                                "content": "STOP: You are repeating the same tool call. You already have this information. Now use a DIFFERENT tool like kubectl_describe, kubectl_logs, or kubectl_generic to investigate further. If the resource in the alert doesn't exist, investigate what resources DO exist and check their health."
                            })
                            continue
                    else:
                        repeated_call_count = 0
                        last_tool_call = current_call
                    
                    # Add assistant message with tool calls to history
                    messages.append({
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    })
                    
                    # Execute each tool call
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            arguments = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                        
                        logger.info(f"  -> {tool_name}({json.dumps(arguments)})")
                        
                        # Execute the tool
                        result = await self._execute_tool_call_async(tool_name, arguments)
                        
                        # Record action
                        actions_taken.append({
                            "iteration": iteration,
                            "tool": tool_name,
                            "arguments": arguments,
                            "result": result
                        })
                        
                        # Add tool result to messages
                        result_content = json.dumps(result) if isinstance(result, dict) else str(result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_content
                        })
                    
                    # Small delay between MCP calls
                    await asyncio.sleep(0.3)
                    
                else:
                    # No tool calls - LLM is providing final response
                    content = message.content or ""
                    logger.info(f"LLM final response: {content[:300]}...")
                    
                    # Detect if LLM is writing JSON in text instead of using tools
                    if '```json' in content and '"name":' in content and '"arguments":' in content:
                        logger.warning("LLM wrote JSON in text instead of calling tools - prompting to use actual tools")
                        messages.append({
                            "role": "assistant",
                            "content": content
                        })
                        messages.append({
                            "role": "user",
                            "content": "IMPORTANT: Do NOT write JSON in your response. You must CALL the tool functions directly using the tool_calls mechanism. Simply invoke the tool - do not describe what you would do."
                        })
                        continue
                    
                    # Add to messages
                    messages.append({
                        "role": "assistant",
                        "content": content
                    })
                    
                    # Check for completion markers
                    if "REMEDIATION COMPLETE:" in content:
                        elapsed = time.time() - start_time
                        logger.info(f"✓ Remediation completed in {elapsed:.1f}s")
                        return RemediationResult(
                            success=True,
                            actions_taken=actions_taken,
                            final_response=content,
                            iterations=iteration
                        )
                    elif "REMEDIATION FAILED:" in content:
                        elapsed = time.time() - start_time
                        logger.info(f"✗ Remediation failed after {elapsed:.1f}s")
                        return RemediationResult(
                            success=False,
                            actions_taken=actions_taken,
                            final_response=content,
                            iterations=iteration
                        )
                    
                    # If no completion marker, prompt for action
                    if iteration < self.max_iterations - 1:
                        messages.append({
                            "role": "user",
                            "content": "Continue investigating. Use the kubectl_get, kubectl_describe, or kubectl_logs tools to gather more information. Do NOT write JSON - call the tool functions directly."
                        })
            
            # Max iterations reached
            elapsed = time.time() - start_time
            logger.warning(f"Max iterations ({self.max_iterations}) reached after {elapsed:.1f}s")
            return RemediationResult(
                success=False,
                actions_taken=actions_taken,
                final_response="Max iterations reached without resolution",
                iterations=iteration,
                error="Max iterations exceeded"
            )
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Error in processing loop after {elapsed:.1f}s: {e}")
            import traceback
            traceback.print_exc()
            return RemediationResult(
                success=False,
                actions_taken=actions_taken,
                final_response=str(e),
                iterations=iteration,
                error=str(e)
            )
    
    def process_alert(self, alert: Alert) -> RemediationResult:
        """
        Process an alert (sync wrapper for async method).
        
        Args:
            alert: The alert to process
            
        Returns:
            RemediationResult with details of actions taken
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.process_alert_async(alert))
        finally:
            loop.close()


# =============================================================================
# CLI for testing
# =============================================================================

def main():
    """CLI entry point for testing the orchestrator."""
    import argparse
    
    parser = argparse.ArgumentParser(description="CB Model Orchestrator")
    parser.add_argument(
        "--alert-name", 
        default="HighCPUUsage",
        help="Name of the test alert"
    )
    parser.add_argument(
        "--severity",
        default="warning",
        choices=["info", "warning", "critical"],
        help="Alert severity"
    )
    parser.add_argument(
        "--namespace",
        default="target-services",
        help="Target namespace"
    )
    parser.add_argument(
        "--pod",
        help="Pod name (optional)"
    )
    parser.add_argument(
        "--deployment",
        help="Deployment name (optional)"
    )
    parser.add_argument(
        "--message",
        default="High CPU usage detected on pod",
        help="Alert message"
    )
    parser.add_argument(
        "--vllm-url",
        default=os.getenv("CB_MODEL_OPENAI_API_URL", "http://localhost:8000/v1"),
        help="vLLM server URL"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Maximum LLM iterations"
    )
    
    args = parser.parse_args()
    
    # Create alert
    alert = Alert(
        alert_name=args.alert_name,
        severity=args.severity,
        namespace=args.namespace,
        pod_name=args.pod,
        deployment_name=args.deployment,
        message=args.message
    )
    
    print("=" * 60)
    print("CB Model Orchestrator - Test Run")
    print("=" * 60)
    print(f"Alert: {alert.alert_name}")
    print(f"Severity: {alert.severity}")
    print(f"Namespace: {alert.namespace}")
    if alert.pod_name:
        print(f"Pod: {alert.pod_name}")
    if alert.deployment_name:
        print(f"Deployment: {alert.deployment_name}")
    print(f"Message: {alert.message}")
    print(f"vLLM URL: {args.vllm_url}")
    print(f"Max Iterations: {args.max_iterations}")
    print("=" * 60)
    
    # Create orchestrator
    orchestrator = CBOrchestrator(
        vllm_base_url=args.vllm_url,
        target_namespace=args.namespace,
        max_iterations=args.max_iterations
    )
    
    # Process alert
    result = orchestrator.process_alert(alert)
    
    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Iterations: {result.iterations}")
    print(f"Actions Taken: {len(result.actions_taken)}")
    if result.error:
        print(f"Error: {result.error}")
    
    print("\n--- Actions ---")
    for i, action in enumerate(result.actions_taken, 1):
        print(f"{i}. [{action.get('iteration', '?')}] {action['tool']}")
        print(f"   Args: {json.dumps(action['arguments'], indent=6)}")
        if action.get('result', {}).get('success'):
            print(f"   Result: OK")
        else:
            print(f"   Result: {action.get('result', {}).get('error', 'Unknown')}")
    
    print("\n--- Final Response ---")
    print(result.final_response)
    
    return 0 if result.success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
