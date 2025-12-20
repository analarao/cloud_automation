#!/usr/bin/env python3
"""
Alert Remediation Client - Claude + kubectl
============================================

This client:
1. Receives enriched alert context via gRPC (protobuf)
2. Formats the context as a prompt for Anthropic Claude
3. Calls Claude API with tool use (function calling)
4. Executes remediation actions via kubectl
5. Returns results via gRPC

Deployment: Runs in Kubernetes with RBAC for kubectl operations
"""

import os
import sys
import json
import time
import logging
import subprocess
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from concurrent import futures

import grpc
import anthropic

# Import generated protobuf modules
import alert_pb2
import alert_pb2_grpc

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("claude_client")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Config:
    """Client configuration from environment variables."""
    # gRPC server
    grpc_port: int = int(os.getenv("GRPC_PORT", "50051"))
    grpc_max_workers: int = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    
    # Claude API
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
    claude_temperature: float = float(os.getenv("CLAUDE_TEMPERATURE", "0.1"))
    claude_max_tokens: int = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))
    
    # MCP/Kubernetes settings
    target_namespace: str = os.getenv("TARGET_NAMESPACE", "target-services")
    mcp_non_destructive: bool = os.getenv("MCP_NON_DESTRUCTIVE", "false").lower() == "true"
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "10"))
    
    # Safety - use field with default_factory for mutable defaults
    require_approval: bool = os.getenv("REQUIRE_APPROVAL", "false").lower() == "true"
    allowed_actions: List[str] = field(default_factory=lambda: os.getenv("ALLOWED_ACTIONS", "get,describe,logs,scale,delete_pod").split(","))


config = Config()


# =============================================================================
# Kubernetes Tools Definition (for Claude tool use)
# =============================================================================

def get_kubectl_tools() -> List[Dict]:
    """Define kubectl tools for Claude tool use."""
    
    return [
        {
            "name": "kubectl_get",
            "description": "Get or list Kubernetes resources. Use to check current state of pods, deployments, services, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "resource_type": {
                        "type": "string",
                        "description": "Resource type: pods, deployments, services, replicasets, configmaps, events, nodes",
                        "enum": ["pods", "deployments", "services", "replicasets", "configmaps", "events", "nodes"]
                    },
                    "name": {
                        "type": "string",
                        "description": "Resource name (optional, omit to list all)"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace"
                    },
                    "output": {
                        "type": "string",
                        "description": "Output format",
                        "enum": ["wide", "yaml", "json", "name"]
                    }
                },
                "required": ["resource_type", "namespace"]
            }
        },
        {
            "name": "kubectl_describe",
            "description": "Get detailed information about a Kubernetes resource including events and conditions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "resource_type": {
                        "type": "string",
                        "description": "Resource type: pod, deployment, service, replicaset, node",
                        "enum": ["pod", "deployment", "service", "replicaset", "node"]
                    },
                    "name": {
                        "type": "string",
                        "description": "Resource name"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace"
                    }
                },
                "required": ["resource_type", "name", "namespace"]
            }
        },
        {
            "name": "kubectl_logs",
            "description": "Get logs from a pod or container. Essential for diagnosing application errors.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pod_name": {
                        "type": "string",
                        "description": "Name of the pod"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace"
                    },
                    "container": {
                        "type": "string",
                        "description": "Container name (optional if pod has single container)"
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Number of lines to show from end of logs (default 100)"
                    },
                    "previous": {
                        "type": "boolean",
                        "description": "Get logs from previous container instance (useful for crash loops)"
                    }
                },
                "required": ["pod_name", "namespace"]
            }
        },
        {
            "name": "kubectl_scale",
            "description": "Scale a deployment to a specified number of replicas.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "deployment_name": {
                        "type": "string",
                        "description": "Name of the deployment"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace"
                    },
                    "replicas": {
                        "type": "integer",
                        "description": "Desired number of replicas"
                    }
                },
                "required": ["deployment_name", "namespace", "replicas"]
            }
        },
        {
            "name": "kubectl_delete_pod",
            "description": "Delete a pod to force a restart. The deployment controller will create a new pod automatically.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pod_name": {
                        "type": "string",
                        "description": "Name of the pod to delete"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace"
                    }
                },
                "required": ["pod_name", "namespace"]
            }
        },
        {
            "name": "kubectl_rollout",
            "description": "Manage deployment rollouts - restart, undo, or check status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Rollout action",
                        "enum": ["restart", "undo", "status", "history"]
                    },
                    "deployment_name": {
                        "type": "string",
                        "description": "Name of the deployment"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace"
                    }
                },
                "required": ["action", "deployment_name", "namespace"]
            }
        },
        {
            "name": "complete_remediation",
            "description": "Call this when remediation is complete or no action is needed. Provide a summary.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Final status",
                        "enum": ["resolved", "partial", "no_action_needed", "failed", "requires_manual"]
                    },
                    "summary": {
                        "type": "string",
                        "description": "Summary of actions taken and current state"
                    },
                    "root_cause": {
                        "type": "string",
                        "description": "Identified root cause of the issue"
                    }
                },
                "required": ["status", "summary"]
            }
        }
    ]


# =============================================================================
# Kubectl Executor
# =============================================================================

class KubectlExecutor:
    """Execute kubectl commands."""
    
    def __init__(self, default_namespace: str = "default", non_destructive: bool = False):
        self.default_namespace = default_namespace
        self.non_destructive = non_destructive
    
    def _run_kubectl(self, args: List[str], timeout: int = 30) -> Dict[str, Any]:
        """Run kubectl command and return result."""
        cmd = ["kubectl"] + args
        logger.info(f"Executing: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
                "command": " ".join(cmd),
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": "Command timed out",
                "command": " ".join(cmd),
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "command": " ".join(cmd),
            }
    
    def kubectl_get(self, resource_type: str, namespace: str, name: str = "", output: str = "wide") -> Dict:
        """kubectl get <resource>"""
        args = ["get", resource_type, "-n", namespace, "-o", output]
        if name:
            args.insert(2, name)
        return self._run_kubectl(args)
    
    def kubectl_describe(self, resource_type: str, name: str, namespace: str) -> Dict:
        """kubectl describe <resource>"""
        args = ["describe", resource_type, name, "-n", namespace]
        return self._run_kubectl(args)
    
    def kubectl_logs(self, pod_name: str, namespace: str, container: str = "", 
                     tail: int = 100, previous: bool = False) -> Dict:
        """kubectl logs <pod>"""
        args = ["logs", pod_name, "-n", namespace, "--tail", str(tail)]
        if container:
            args.extend(["-c", container])
        if previous:
            args.append("--previous")
        return self._run_kubectl(args)
    
    def kubectl_scale(self, deployment_name: str, namespace: str, replicas: int) -> Dict:
        """kubectl scale deployment"""
        if self.non_destructive:
            return {
                "success": False,
                "output": "",
                "error": "Non-destructive mode: scale operation blocked",
                "command": f"kubectl scale deployment {deployment_name} --replicas={replicas} -n {namespace}",
            }
        
        args = ["scale", "deployment", deployment_name, f"--replicas={replicas}", "-n", namespace]
        return self._run_kubectl(args)
    
    def kubectl_delete_pod(self, pod_name: str, namespace: str) -> Dict:
        """kubectl delete pod"""
        if self.non_destructive:
            return {
                "success": False,
                "output": "",
                "error": "Non-destructive mode: delete operation blocked",
                "command": f"kubectl delete pod {pod_name} -n {namespace}",
            }
        
        args = ["delete", "pod", pod_name, "-n", namespace]
        return self._run_kubectl(args)
    
    def kubectl_rollout(self, action: str, deployment_name: str, namespace: str) -> Dict:
        """kubectl rollout <action>"""
        if action in ["restart", "undo"] and self.non_destructive:
            return {
                "success": False,
                "output": "",
                "error": f"Non-destructive mode: rollout {action} operation blocked",
                "command": f"kubectl rollout {action} deployment/{deployment_name} -n {namespace}",
            }
        
        args = ["rollout", action, f"deployment/{deployment_name}", "-n", namespace]
        return self._run_kubectl(args)
    
    def execute_tool(self, tool_name: str, arguments: Dict) -> Dict:
        """Execute a tool by name."""
        logger.info(f"Executing tool: {tool_name} with args: {arguments}")
        
        if tool_name == "kubectl_get":
            return self.kubectl_get(
                resource_type=arguments.get("resource_type", "pods"),
                namespace=arguments.get("namespace", self.default_namespace),
                name=arguments.get("name", ""),
                output=arguments.get("output", "wide"),
            )
        
        elif tool_name == "kubectl_describe":
            return self.kubectl_describe(
                resource_type=arguments.get("resource_type", "pod"),
                name=arguments.get("name", ""),
                namespace=arguments.get("namespace", self.default_namespace),
            )
        
        elif tool_name == "kubectl_logs":
            return self.kubectl_logs(
                pod_name=arguments.get("pod_name", ""),
                namespace=arguments.get("namespace", self.default_namespace),
                container=arguments.get("container", ""),
                tail=arguments.get("tail", 100),
                previous=arguments.get("previous", False),
            )
        
        elif tool_name == "kubectl_scale":
            return self.kubectl_scale(
                deployment_name=arguments.get("deployment_name", ""),
                namespace=arguments.get("namespace", self.default_namespace),
                replicas=arguments.get("replicas", 1),
            )
        
        elif tool_name == "kubectl_delete_pod":
            return self.kubectl_delete_pod(
                pod_name=arguments.get("pod_name", ""),
                namespace=arguments.get("namespace", self.default_namespace),
            )
        
        elif tool_name == "kubectl_rollout":
            return self.kubectl_rollout(
                action=arguments.get("action", "status"),
                deployment_name=arguments.get("deployment_name", ""),
                namespace=arguments.get("namespace", self.default_namespace),
            )
        
        elif tool_name == "complete_remediation":
            return {
                "success": True,
                "output": json.dumps(arguments),
                "error": "",
                "command": "complete_remediation",
                "is_final": True,
            }
        
        else:
            return {
                "success": False,
                "output": "",
                "error": f"Unknown tool: {tool_name}",
                "command": tool_name,
            }


# =============================================================================
# Claude Orchestrator
# =============================================================================

class ClaudeOrchestrator:
    """Orchestrates Claude API calls and tool execution."""
    
    SYSTEM_PROMPT = """You are an expert Kubernetes SRE AI assistant. Your job is to analyze alerts and perform remediation actions.

CRITICAL INSTRUCTIONS:
1. You MUST use the provided tools to interact with Kubernetes - do not just describe what you would do
2. Always start by investigating the current state using kubectl_get and kubectl_describe
3. Check logs with kubectl_logs if there are application errors
4. Only take remediation actions (scale, delete_pod, rollout) after understanding the root cause
5. You can ONLY operate on resources in the '{namespace}' namespace
6. After taking actions, verify the fix worked using kubectl_get
7. When done, call complete_remediation with a summary

WORKFLOW:
1. Investigate: kubectl_get pods, kubectl_describe pod, kubectl_logs
2. Analyze: Identify root cause from the information gathered
3. Remediate: Scale deployment, delete pod to restart, or rollout restart
4. Verify: Check that the issue is resolved
5. Complete: Call complete_remediation with status and summary

Be concise and action-oriented. Start investigating now."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "claude-sonnet-4-20250514",
        target_namespace: str = "target-services",
        max_iterations: int = 10,
        non_destructive: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        
        # Initialize the Anthropic client
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model_name = model_name
        self.target_namespace = target_namespace
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.tools = get_kubectl_tools()
        self.system_prompt = self.SYSTEM_PROMPT.format(namespace=target_namespace)
        
        self.kubectl = KubectlExecutor(
            default_namespace=target_namespace,
            non_destructive=non_destructive,
        )
        
        logger.info(f"✓ Initialized Claude orchestrator with model: {model_name}")
    
    def format_alert_prompt(self, request: alert_pb2.AlertRequest) -> str:
        """Format the alert request into a prompt for Claude."""
        parts = []
        
        # Alert info
        parts.append("=" * 60)
        parts.append("ALERT INFORMATION")
        parts.append("=" * 60)
        parts.append(f"Alert Name: {request.alert.name}")
        parts.append(f"Severity: {request.alert.severity}")
        parts.append(f"State: {request.alert.state}")
        parts.append(f"Summary: {request.alert.summary}")
        parts.append(f"Description: {request.alert.description}")
        parts.append(f"Fingerprint: {request.alert.fingerprint}")
        
        if request.alert.labels:
            parts.append("\nLabels:")
            for k, v in request.alert.labels.items():
                parts.append(f"  {k}: {v}")
        
        # Kubernetes context
        if request.kubernetes.namespace:
            parts.append("")
            parts.append("=" * 60)
            parts.append("KUBERNETES CONTEXT")
            parts.append("=" * 60)
            parts.append(f"Namespace: {request.kubernetes.namespace}")
            
            if request.kubernetes.pod.name:
                pod = request.kubernetes.pod
                parts.append(f"\nPod: {pod.name}")
                parts.append(f"  Phase: {pod.phase}")
                parts.append(f"  Pod IP: {pod.pod_ip}")
                parts.append(f"  Host IP: {pod.host_ip}")
                
                if pod.conditions:
                    parts.append("  Conditions:")
                    for cond in pod.conditions:
                        parts.append(f"    - {cond.type}: {cond.status} ({cond.reason})")
            
            if request.kubernetes.workload.name:
                wl = request.kubernetes.workload
                parts.append(f"\nWorkload: {wl.kind}/{wl.name}")
                parts.append(f"  Replicas: {wl.ready_replicas}/{wl.replicas} ready")
                parts.append(f"  Available: {wl.available_replicas}")
        
        # Container info
        if request.container.container_name:
            parts.append("")
            parts.append("=" * 60)
            parts.append("CONTAINER CONTEXT")
            parts.append("=" * 60)
            parts.append(f"Container: {request.container.container_name}")
            parts.append(f"Image: {request.container.image}")
            parts.append(f"State: {request.container.state}")
            parts.append(f"Restart Count: {request.container.restart_count}")
            if request.container.termination_reason:
                parts.append(f"Last Termination: {request.container.termination_reason}")
        
        # Metrics
        if request.metrics.cpu.name or request.metrics.memory.name:
            parts.append("")
            parts.append("=" * 60)
            parts.append("METRICS")
            parts.append("=" * 60)
            
            if request.metrics.cpu.current > 0:
                cpu = request.metrics.cpu
                parts.append(f"CPU: current={cpu.current:.3f} cores, avg={cpu.avg:.3f}, max={cpu.max:.3f}")
            
            if request.metrics.memory.current > 0:
                mem = request.metrics.memory
                mem_mb = mem.current / (1024 * 1024)
                parts.append(f"Memory: current={mem_mb:.1f} MB, max={mem.max / (1024*1024):.1f} MB")
        
        # Service mesh dependencies
        if request.service_mesh.service_name:
            parts.append("")
            parts.append("=" * 60)
            parts.append("SERVICE MESH DEPENDENCIES")
            parts.append("=" * 60)
            parts.append(f"Service: {request.service_mesh.service_name}")
            
            if request.service_mesh.upstream:
                parts.append("\nUpstream (calls to):")
                for dep in request.service_mesh.upstream:
                    parts.append(f"  - {dep.service_name}: {dep.requests_per_second:.1f} rps, err={dep.error_rate:.1%}")
            
            if request.service_mesh.downstream:
                parts.append("\nDownstream (called by):")
                for dep in request.service_mesh.downstream:
                    parts.append(f"  - {dep.service_name}: {dep.requests_per_second:.1f} rps, err={dep.error_rate:.1%}")
        
        # Recent logs (errors only)
        if request.logs.errors:
            parts.append("")
            parts.append("=" * 60)
            parts.append("RECENT ERROR LOGS")
            parts.append("=" * 60)
            for entry in request.logs.errors[:10]:
                ts = datetime.fromtimestamp(entry.timestamp).strftime("%H:%M:%S")
                parts.append(f"[{ts}] {entry.message[:200]}")
        
        parts.append("")
        parts.append("=" * 60)
        parts.append("TASK")
        parts.append("=" * 60)
        parts.append("Analyze this alert, investigate the root cause, and perform remediation actions.")
        parts.append("Start by getting the current state of pods and describing the affected resources.")
        
        return "\n".join(parts)
    
    def process_alert(self, request: alert_pb2.AlertRequest) -> alert_pb2.AlertResponse:
        """Process an alert through Claude with tool calling."""
        start_time = time.time()
        
        response = alert_pb2.AlertResponse()
        response.request_id = request.request_id
        
        try:
            # Format the prompt
            prompt = self.format_alert_prompt(request)
            logger.info(f"Processing alert {request.alert.name} with {len(prompt)} char prompt")
            
            # Build conversation history
            messages = [
                {"role": "user", "content": prompt}
            ]
            
            # Agentic loop
            iteration = 0
            actions_taken = []
            final_result = None
            raw_responses = []
            
            while iteration < self.max_iterations:
                iteration += 1
                logger.info(f"Iteration {iteration}/{self.max_iterations}")
                
                # Call Claude
                claude_response = self.client.messages.create(
                    model=self.model_name,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=self.system_prompt,
                    tools=self.tools,
                    messages=messages,
                )
                
                # Add assistant response to history
                messages.append({"role": "assistant", "content": claude_response.content})
                
                # Process response content
                tool_uses = []
                for block in claude_response.content:
                    if block.type == "text":
                        raw_responses.append(block.text)
                        logger.info(f"Claude: {block.text[:200]}...")
                    elif block.type == "tool_use":
                        tool_uses.append(block)
                
                # Check if Claude is done (no tool calls)
                if claude_response.stop_reason == "end_turn" and not tool_uses:
                    logger.info("Claude finished without tool calls - ending loop")
                    break
                
                if not tool_uses:
                    logger.info("No tool calls - ending loop")
                    break
                
                # Execute tool calls and build tool results
                tool_results = []
                
                for tool_use in tool_uses:
                    tool_name = tool_use.name
                    tool_input = tool_use.input
                    tool_use_id = tool_use.id
                    
                    logger.info(f"Tool call: {tool_name}({tool_input})")
                    
                    # Execute the tool
                    result = self.kubectl.execute_tool(tool_name, tool_input)
                    
                    # Record the action
                    action = alert_pb2.RemediationAction()
                    action.action_type = tool_name
                    action.description = f"{tool_name} with {tool_input}"
                    action.command = result.get("command", "")
                    action.output = result.get("output", "")[:2000]
                    action.success = result.get("success", False)
                    action.error = result.get("error", "")
                    action.executed_at = int(time.time())
                    actions_taken.append(action)
                    
                    # Check if final
                    if result.get("is_final"):
                        try:
                            final_data = json.loads(result.get("output", "{}"))
                            final_result = {
                                "status": final_data.get("status", "completed"),
                                "summary": final_data.get("summary", ""),
                                "root_cause": final_data.get("root_cause", ""),
                            }
                        except:
                            final_result = {"status": "completed", "summary": "Completed"}
                    
                    # Build tool result
                    response_content = result.get("output", "") or result.get("error", "No output")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": response_content[:4000],
                    })
                
                if final_result:
                    break
                
                # Add tool results to messages
                messages.append({"role": "user", "content": tool_results})
            
            # Build response
            response.success = True
            response.status = final_result.get("status", "completed") if final_result else "completed"
            response.raw_llm_response = "\n---\n".join(raw_responses)[:5000]
            
            if final_result:
                response.analysis.primary_cause = final_result.get("root_cause", "")
                response.analysis.category = "application"
            
            for action in actions_taken:
                response.actions.append(action)
            
            successful_actions = sum(1 for a in actions_taken if a.success)
            total_actions = len(actions_taken)
            response.confidence = successful_actions / total_actions if total_actions > 0 else 0.5
            
        except Exception as e:
            logger.exception(f"Error processing alert: {e}")
            response.success = False
            response.error = str(e)
            response.status = "failed"
        
        response.processing_time_ms = int((time.time() - start_time) * 1000)
        return response


# =============================================================================
# gRPC Service
# =============================================================================

class AlertRemediationServicer(alert_pb2_grpc.AlertRemediationServiceServicer):
    """gRPC service for alert remediation."""
    
    def __init__(self):
        self.orchestrator = ClaudeOrchestrator(
            api_key=config.anthropic_api_key,
            model_name=config.claude_model,
            target_namespace=config.target_namespace,
            max_iterations=config.max_iterations,
            non_destructive=config.mcp_non_destructive,
            max_tokens=config.claude_max_tokens,
            temperature=config.claude_temperature,
        )
        logger.info("✓ Alert remediation service initialized")
    
    def AnalyzeAndRemediate(
        self,
        request: alert_pb2.AlertRequest,
        context: grpc.ServicerContext
    ) -> alert_pb2.AlertResponse:
        """Process an alert and return remediation results."""
        logger.info(f"Received alert: {request.alert.name} (request_id={request.request_id})")
        
        try:
            response = self.orchestrator.process_alert(request)
            logger.info(f"Completed: success={response.success}, status={response.status}, "
                       f"actions={len(response.actions)}, time={response.processing_time_ms}ms")
            return response
        
        except Exception as e:
            logger.exception(f"Error in AnalyzeAndRemediate: {e}")
            response = alert_pb2.AlertResponse()
            response.request_id = request.request_id
            response.success = False
            response.error = str(e)
            response.status = "failed"
            return response
    
    def AnalyzeAndRemediateStream(
        self,
        request: alert_pb2.AlertRequest,
        context: grpc.ServicerContext
    ):
        """Stream remediation progress updates."""
        response = self.orchestrator.process_alert(request)
        
        for i, action in enumerate(response.actions):
            update = alert_pb2.RemediationUpdate()
            update.request_id = request.request_id
            update.step = f"Step {i+1}: {action.action_type}"
            update.message = action.description
            update.status = "completed" if action.success else "failed"
            update.timestamp = action.executed_at
            yield update
        
        final = alert_pb2.RemediationUpdate()
        final.request_id = request.request_id
        final.step = "Complete"
        final.message = f"Remediation {response.status}"
        final.status = response.status
        final.timestamp = int(time.time())
        yield final
    
    def HealthCheck(
        self,
        request: alert_pb2.HealthRequest,
        context: grpc.ServicerContext
    ) -> alert_pb2.HealthResponse:
        """Health check."""
        response = alert_pb2.HealthResponse()
        response.healthy = True
        response.status = "ready"
        response.details["model"] = config.claude_model
        response.details["namespace"] = config.target_namespace
        response.details["non_destructive"] = str(config.mcp_non_destructive)
        return response


# =============================================================================
# Server
# =============================================================================

def serve():
    """Start the gRPC server."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=config.grpc_max_workers))
    
    servicer = AlertRemediationServicer()
    alert_pb2_grpc.add_AlertRemediationServiceServicer_to_server(servicer, server)
    
    address = f"[::]:{config.grpc_port}"
    server.add_insecure_port(address)
    
    logger.info("=" * 60)
    logger.info("Alert Remediation Client (Claude + kubectl)")
    logger.info("=" * 60)
    logger.info(f"gRPC port: {config.grpc_port}")
    logger.info(f"Claude model: {config.claude_model}")
    logger.info(f"Target namespace: {config.target_namespace}")
    logger.info(f"Non-destructive mode: {config.mcp_non_destructive}")
    logger.info(f"Max iterations: {config.max_iterations}")
    logger.info("=" * 60)
    
    server.start()
    logger.info(f"Server started, listening on {address}")
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.stop(5)


if __name__ == "__main__":
    serve()
