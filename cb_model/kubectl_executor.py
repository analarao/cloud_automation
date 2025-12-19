#!/usr/bin/env python3
"""
Direct Kubectl Executor - Simpler alternative to MCP for demo purposes.

Executes kubectl commands directly via subprocess.
"""

import subprocess
import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger("kubectl_exec")

@dataclass
class ToolResult:
    success: bool
    result: str
    error: Optional[str] = None

def kubectl_get(resource_type: str, namespace: str, name: str = "", selector: str = "") -> ToolResult:
    """Get Kubernetes resources."""
    cmd = ["kubectl", "get", resource_type, "-n", namespace, "-o", "wide"]
    if name:
        cmd.insert(3, name)
    if selector:
        cmd.extend(["-l", selector])
    
    logger.info(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return ToolResult(success=True, result=result.stdout)
    return ToolResult(success=False, result="", error=result.stderr)

def kubectl_describe(resource_type: str, name: str, namespace: str) -> ToolResult:
    """Describe a Kubernetes resource."""
    cmd = ["kubectl", "describe", resource_type, name, "-n", namespace]
    
    logger.info(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return ToolResult(success=True, result=result.stdout)
    return ToolResult(success=False, result="", error=result.stderr)

def kubectl_logs(pod_name: str, namespace: str, tail: int = 50, container: str = "") -> ToolResult:
    """Get pod logs."""
    cmd = ["kubectl", "logs", pod_name, "-n", namespace, f"--tail={tail}"]
    if container:
        cmd.extend(["-c", container])
    
    logger.info(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return ToolResult(success=True, result=result.stdout)
    return ToolResult(success=False, result="", error=result.stderr)

def kubectl_rollout(action: str, resource_type: str, name: str, namespace: str) -> ToolResult:
    """Manage rollouts (undo, status, restart)."""
    cmd = ["kubectl", "rollout", action, f"{resource_type}/{name}", "-n", namespace]
    
    logger.info(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return ToolResult(success=True, result=result.stdout)
    return ToolResult(success=False, result="", error=result.stderr)

def kubectl_scale(resource_type: str, name: str, namespace: str, replicas: int) -> ToolResult:
    """Scale a deployment."""
    cmd = ["kubectl", "scale", f"{resource_type}/{name}", "-n", namespace, f"--replicas={replicas}"]
    
    logger.info(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return ToolResult(success=True, result=result.stdout)
    return ToolResult(success=False, result="", error=result.stderr)

def kubectl_delete(resource_type: str, name: str, namespace: str) -> ToolResult:
    """Delete a resource (e.g., pod to restart it)."""
    cmd = ["kubectl", "delete", resource_type, name, "-n", namespace]
    
    logger.info(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return ToolResult(success=True, result=result.stdout)
    return ToolResult(success=False, result="", error=result.stderr)

# Tool registry
TOOLS = {
    "kubectl_get": kubectl_get,
    "kubectl_describe": kubectl_describe,
    "kubectl_logs": kubectl_logs,
    "kubectl_rollout": kubectl_rollout,
    "kubectl_scale": kubectl_scale,
    "kubectl_delete": kubectl_delete,
}

def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
    """Execute a tool by name with given arguments."""
    if tool_name not in TOOLS:
        return ToolResult(success=False, result="", error=f"Unknown tool: {tool_name}")
    
    func = TOOLS[tool_name]
    try:
        return func(**arguments)
    except Exception as e:
        return ToolResult(success=False, result="", error=str(e))

# OpenAI tool definitions for the LLM
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kubectl_get",
            "description": "Get/list Kubernetes resources",
            "parameters": {
                "type": "object",
                "properties": {
                    "resourceType": {"type": "string", "description": "Resource type: pods, deployments, services, etc."},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "name": {"type": "string", "description": "Resource name (empty to list all)"},
                    "selector": {"type": "string", "description": "Label selector"}
                },
                "required": ["resourceType", "namespace"]
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "kubectl_describe",
            "description": "Get detailed info about a resource",
            "parameters": {
                "type": "object",
                "properties": {
                    "resourceType": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"}
                },
                "required": ["resourceType", "name", "namespace"]
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
                    "podName": {"type": "string"},
                    "namespace": {"type": "string"},
                    "tail": {"type": "integer", "description": "Number of lines"},
                    "container": {"type": "string", "description": "Container name if multiple"}
                },
                "required": ["podName", "namespace"]
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
                    "resourceType": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"}
                },
                "required": ["action", "resourceType", "name", "namespace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kubectl_scale",
            "description": "Scale a deployment",
            "parameters": {
                "type": "object",
                "properties": {
                    "resourceType": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                    "replicas": {"type": "integer"}
                },
                "required": ["resourceType", "name", "namespace", "replicas"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kubectl_delete",
            "description": "Delete a resource (e.g., pod to restart it)",
            "parameters": {
                "type": "object",
                "properties": {
                    "resourceType": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"}
                },
                "required": ["resourceType", "name", "namespace"]
            }
        }
    }
]
