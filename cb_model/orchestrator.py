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
    
    SYSTEM_PROMPT = """You are an AI-powered Kubernetes operations assistant for the Autonomous Operations Platform (AOP). Your role is to analyze alerts and take remediation actions.

IMPORTANT CONSTRAINTS:
1. You can ONLY operate on resources in the '{namespace}' namespace
2. Always gather information before taking destructive actions
3. Prefer scaling operations over deletion
4. Log all actions you take
5. If you're unsure, gather more information first

AVAILABLE TOOLS:
You have access to Kubernetes tools via MCP (Model Context Protocol). Use them to:
- kubectl_get: List/get pods, deployments, services, etc.
- kubectl_describe: Get detailed info about resources
- kubectl_logs: Get pod logs for debugging
- kubectl_scale: Scale deployments up or down
- kubectl_delete: Delete pods (they will be recreated by deployment)
- kubectl_apply: Apply YAML configurations
- kubectl_patch: Patch resources

NOTE: Tool arguments use camelCase (e.g., resourceType, not resource_type).
For kubectl_get, provide name="" to list all resources of that type.

REMEDIATION WORKFLOW:
1. ANALYZE: Understand the alert and what it indicates
2. INVESTIGATE: Use kubectl_get and kubectl_describe to gather information
3. DIAGNOSE: Identify the root cause based on evidence
4. PLAN: Decide on remediation actions
5. EXECUTE: Take remediation actions (scale, restart, etc.)
6. VERIFY: Use kubectl_get to confirm the issue is resolved

When you have completed your analysis and remediation, provide a final summary starting with:
- "REMEDIATION COMPLETE:" if successful, followed by what you did
- "REMEDIATION FAILED:" if unsuccessful, followed by the reason

Be concise and action-oriented. Start by investigating the current state."""

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
        self.essential_tools = [
            "kubectl_get",
            "kubectl_describe", 
            "kubectl_logs",
            "kubectl_scale",
            "kubectl_delete",
            "kubectl_patch",
            "kubectl_rollout",
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
        
        try:
            while iteration < self.max_iterations:
                iteration += 1
                logger.info(f"\n--- Iteration {iteration}/{self.max_iterations} ---")
                
                # Determine tool_choice strategy:
                # - First few iterations: force tool use to gather information
                # - Later iterations: allow model to decide (auto) or finish
                # - If we have taken actions, allow finishing
                if len(actions_taken) == 0 and iteration <= 3:
                    # Force tool use initially to gather information
                    tool_choice = "required"
                    logger.info("Tool choice: required (gathering information)")
                else:
                    # Allow model to decide after initial investigation
                    tool_choice = "auto"
                    logger.info("Tool choice: auto (can finish or continue)")
                
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
                            "content": "Please continue with your analysis. Use the available tools to investigate or take action. When done, provide a summary starting with 'REMEDIATION COMPLETE:' or 'REMEDIATION FAILED:'."
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
