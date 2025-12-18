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
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from openai import OpenAI

from mcp_client import MCPKubernetesClient

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
    """
    
    SYSTEM_PROMPT = """You are an AI-powered Kubernetes operations assistant for the Autonomous Operations Platform (AOP). Your role is to analyze alerts and take remediation actions.

IMPORTANT CONSTRAINTS:
1. You can ONLY operate on resources in the 'target-services' namespace
2. Always gather information before taking destructive actions
3. Prefer scaling operations over deletion
4. Log all actions you take
5. If you're unsure, gather more information first

AVAILABLE TOOLS:
You have access to Kubernetes tools via MCP (Model Context Protocol). Use them to:
- List pods, deployments, services in target-services namespace
- Get logs from pods to diagnose issues
- Describe resources for detailed information
- Scale deployments up or down
- Restart pods by deleting them (Kubernetes will recreate)
- Apply or patch resources

REMEDIATION WORKFLOW:
1. ANALYZE: Understand the alert and what it indicates
2. INVESTIGATE: Use tools to gather more information
3. DIAGNOSE: Identify the root cause
4. PLAN: Decide on remediation actions
5. EXECUTE: Take remediation actions
6. VERIFY: Confirm the issue is resolved

When you have completed your analysis and remediation, provide a final summary starting with "REMEDIATION COMPLETE:" or "REMEDIATION FAILED:" followed by a description of what you did."""

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
        
        # Initialize MCP client
        self.mcp_client = MCPKubernetesClient()
        
        # Track model name
        self.model_name: Optional[str] = None
        
        # Tools in OpenAI format (populated from MCP)
        self.tools: List[Dict[str, Any]] = []
        
        logger.info(f"Orchestrator initialized with vLLM at {self.vllm_base_url}")
    
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
    
    def _load_mcp_tools(self) -> List[Dict[str, Any]]:
        """Load tools from MCP and convert to OpenAI format."""
        if self.tools:
            return self.tools
        
        try:
            mcp_tools = self.mcp_client.list_tools()
            
            # Convert MCP tools to OpenAI function format
            self.tools = []
            for tool in mcp_tools:
                openai_tool = {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {})
                    }
                }
                self.tools.append(openai_tool)
            
            logger.info(f"Loaded {len(self.tools)} tools from MCP")
            return self.tools
            
        except Exception as e:
            logger.error(f"Failed to load MCP tools: {e}")
            return []
    
    def _execute_tool_call(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool call via MCP.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments for the tool
            
        Returns:
            Tool execution result
        """
        logger.info(f"Executing tool: {tool_name} with args: {arguments}")
        
        try:
            result = self.mcp_client.execute_tool(tool_name, arguments)
            logger.info(f"Tool result: {json.dumps(result, indent=2)[:500]}...")
            return result
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return {"error": str(e)}
    
    def process_alert(self, alert: Alert) -> RemediationResult:
        """
        Process an alert through the LLM reasoning loop.
        
        Args:
            alert: The alert to process
            
        Returns:
            RemediationResult with details of actions taken
        """
        logger.info(f"Processing alert: {alert.alert_name}")
        
        # Initialize
        self._discover_model()
        tools = self._load_mcp_tools()
        
        if not tools:
            return RemediationResult(
                success=False,
                actions_taken=[],
                final_response="No MCP tools available",
                iterations=0,
                error="Failed to load MCP tools"
            )
        
        # Build initial messages
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"Please analyze and remediate the following alert:\n\n{alert.to_prompt()}"}
        ]
        
        actions_taken = []
        iteration = 0
        
        try:
            while iteration < self.max_iterations:
                iteration += 1
                logger.info(f"Iteration {iteration}/{self.max_iterations}")
                
                # Call LLM with tools
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",  # or "required" for forced tool use
                    temperature=self.temperature,
                    max_tokens=2048
                )
                
                choice = response.choices[0]
                message = choice.message
                
                # Add assistant message to history
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
                        for tc in (message.tool_calls or [])
                    ] if message.tool_calls else None
                })
                
                # Check if LLM is done (no tool calls)
                if not message.tool_calls:
                    content = message.content or ""
                    
                    # Check for completion markers
                    if "REMEDIATION COMPLETE:" in content or "REMEDIATION FAILED:" in content:
                        success = "REMEDIATION COMPLETE:" in content
                        return RemediationResult(
                            success=success,
                            actions_taken=actions_taken,
                            final_response=content,
                            iterations=iteration
                        )
                    
                    # If no tool calls but no completion marker, prompt for action
                    messages.append({
                        "role": "user",
                        "content": "Please continue with your analysis. Use the available tools to investigate or take action. When done, provide a summary starting with 'REMEDIATION COMPLETE:' or 'REMEDIATION FAILED:'."
                    })
                    continue
                
                # Execute tool calls
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    
                    # Execute the tool
                    result = self._execute_tool_call(tool_name, arguments)
                    
                    # Record action
                    actions_taken.append({
                        "tool": tool_name,
                        "arguments": arguments,
                        "result": result
                    })
                    
                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result)
                    })
            
            # Max iterations reached
            return RemediationResult(
                success=False,
                actions_taken=actions_taken,
                final_response="Max iterations reached without resolution",
                iterations=iteration,
                error="Max iterations exceeded"
            )
            
        except Exception as e:
            logger.error(f"Error in processing loop: {e}")
            return RemediationResult(
                success=False,
                actions_taken=actions_taken,
                final_response=str(e),
                iterations=iteration,
                error=str(e)
            )
        finally:
            # Cleanup MCP client
            self.mcp_client.stop()
    
    def start(self):
        """Start the orchestrator (initialize MCP)."""
        logger.info("Starting orchestrator...")
        self.mcp_client.start()
        self._load_mcp_tools()
        logger.info("Orchestrator started")
    
    def stop(self):
        """Stop the orchestrator."""
        logger.info("Stopping orchestrator...")
        self.mcp_client.stop()
        logger.info("Orchestrator stopped")
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


# =============================================================================
# gRPC Service Integration
# =============================================================================

class CBModelGRPCHandler:
    """
    Handles gRPC requests from the alert aggregator or CS Model.
    Integrates with the orchestrator for LLM-based remediation.
    """
    
    def __init__(self, orchestrator: CBOrchestrator):
        self.orchestrator = orchestrator
    
    def handle_alert(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle an incoming alert from gRPC.
        
        Args:
            alert_data: Alert data from protobuf message
            
        Returns:
            Response data for protobuf message
        """
        # Parse alert from gRPC data
        alert = Alert(
            alert_name=alert_data.get("alertname", "Unknown"),
            severity=alert_data.get("severity", "warning"),
            namespace=alert_data.get("namespace", "target-services"),
            pod_name=alert_data.get("pod"),
            deployment_name=alert_data.get("deployment"),
            message=alert_data.get("message", ""),
            labels=alert_data.get("labels", {}),
            annotations=alert_data.get("annotations", {}),
            value=alert_data.get("value"),
            threshold=alert_data.get("threshold")
        )
        
        # Process alert
        result = self.orchestrator.process_alert(alert)
        
        # Convert to response format
        return {
            "success": result.success,
            "actions_taken": result.actions_taken,
            "response": result.final_response,
            "iterations": result.iterations,
            "error": result.error
        }


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
        help="Pod name"
    )
    parser.add_argument(
        "--deployment",
        help="Deployment name"
    )
    parser.add_argument(
        "--message",
        default="High CPU usage detected",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually execute tools (not implemented yet)"
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
    print(f"vLLM URL: {args.vllm_url}")
    print("=" * 60)
    
    # Run orchestrator
    with CBOrchestrator(
        vllm_base_url=args.vllm_url,
        target_namespace=args.namespace,
        max_iterations=args.max_iterations
    ) as orchestrator:
        result = orchestrator.process_alert(alert)
    
    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Iterations: {result.iterations}")
    print(f"Actions Taken: {len(result.actions_taken)}")
    if result.error:
        print(f"Error: {result.error}")
    print("\nFinal Response:")
    print(result.final_response)
    
    if result.actions_taken:
        print("\nActions Taken:")
        for i, action in enumerate(result.actions_taken, 1):
            print(f"  {i}. {action['tool']}")
            print(f"     Args: {action['arguments']}")


if __name__ == "__main__":
    main()
