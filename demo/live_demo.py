#!/usr/bin/env python3
"""
Autonomous Operations Platform - Live Demo

Runs actual components:
- Fake LLM (OpenAI-compatible API on :8000)
- Real kubectl execution against minikube
- Real CB orchestrator logic

Usage:
    python3 demo/live_demo.py

Requires:
    pip install flask flask-cors openai requests
"""

import os
import sys
import json
import time
import uuid
import threading
import subprocess
from datetime import datetime

# Flask for fake LLM
from flask import Flask, request, jsonify

# OpenAI client for talking to our fake LLM
from openai import OpenAI

# ============================================================================
# KUBECTL EXECUTOR (Real commands against minikube)
# ============================================================================

def kubectl(cmd: str) -> tuple:
    """Execute kubectl command and return (success, output)."""
    full_cmd = f"kubectl {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=30)
    return result.returncode == 0, result.stdout.strip() or result.stderr.strip()

def execute_tool(name: str, args: dict) -> dict:
    """Execute a kubectl tool and return result."""
    ns = args.get('namespace', 'target-services')
    
    if name == "kubectl_get":
        rtype = args.get('resourceType', 'pods')
        rname = args.get('name', '')
        cmd = f"get {rtype} {rname} -n {ns}".strip()
        ok, out = kubectl(cmd)
        return {"success": ok, "output": out}
    
    elif name == "kubectl_describe":
        rtype = args.get('resourceType', 'pod')
        rname = args.get('name', '')
        cmd = f"describe {rtype} {rname} -n {ns}"
        ok, out = kubectl(cmd)
        # Truncate describe output
        if len(out) > 2000:
            out = out[:2000] + "\n... (truncated)"
        return {"success": ok, "output": out}
    
    elif name == "kubectl_logs":
        pod = args.get('podName', '')
        tail = args.get('tail', 50)
        cmd = f"logs {pod} -n {ns} --tail={tail}"
        ok, out = kubectl(cmd)
        return {"success": ok, "output": out}

    
    elif name == "kubectl_rollout":
        action = args.get('action', 'status')
        rtype = args.get('resourceType', 'deployment')
        rname = args.get('name', '')
        cmd = f"rollout {action} {rtype}/{rname} -n {ns}"
        ok, out = kubectl(cmd)
        return {"success": ok, "output": out}
    
    elif name == "kubectl_scale":
        rtype = args.get('resourceType', 'deployment')
        rname = args.get('name', '')
        replicas = args.get('replicas', 1)
        cmd = f"scale {rtype}/{rname} --replicas={replicas} -n {ns}"

        ok, out = kubectl(cmd)
        return {"success": ok, "output": out}
    
    elif name == "kubectl_delete":
        rtype = args.get('resourceType', 'pod')
        rname = args.get('name', '')
        cmd = f"delete {rtype} {rname} -n {ns}"
        ok, out = kubectl(cmd)
        return {"success": ok, "output": out}
    
    return {"success": False, "output": f"Unknown tool: {name}"}

# ============================================================================
# FAKE LLM SERVER
# ============================================================================

llm_app = Flask(__name__)
conversation_turn = [0]  # Use list to allow mutation in nested function

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "kubectl_get", "description": "List Kubernetes resources", "parameters": {"type": "object", "properties": {"resourceType": {"type": "string"}, "namespace": {"type": "string"}, "name": {"type": "string"}}, "required": ["resourceType", "namespace"]}}},
    {"type": "function", "function": {"name": "kubectl_describe", "description": "Describe a resource", "parameters": {"type": "object", "properties": {"resourceType": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["resourceType", "name", "namespace"]}}},
    {"type": "function", "function": {"name": "kubectl_logs", "description": "Get pod logs", "parameters": {"type": "object", "properties": {"podName": {"type": "string"}, "namespace": {"type": "string"}, "tail": {"type": "integer"}}, "required": ["podName", "namespace"]}}},
    {"type": "function", "function": {"name": "kubectl_rollout", "description": "Rollout operations", "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "resourceType": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["action", "resourceType", "name", "namespace"]}}},
    {"type": "function", "function": {"name": "kubectl_scale", "description": "Scale deployment", "parameters": {"type": "object", "properties": {"resourceType": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}, "replicas": {"type": "integer"}}, "required": ["resourceType", "name", "namespace", "replicas"]}}},
]

@llm_app.route('/v1/models', methods=['GET'])
def models():
    return jsonify({"data": [{"id": "Qwen2.5-Coder-7B", "object": "model"}]})

@llm_app.route('/v1/chat/completions', methods=['POST'])
def completions():

    data = request.json
    messages = data.get('messages', [])
    
    # Count tool responses to track conversation progress
    tool_count = sum(1 for m in messages if m.get('role') == 'tool')
    
    ns = "target-services"
    svc = "cart-service"
    
    # Scripted responses based on turn
    if tool_count == 0:
        content = "Let me check the current pod status in the namespace."
        tool_calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": "kubectl_get", "arguments": json.dumps({"resourceType": "pods", "namespace": ns, "name": ""})}}]
    elif tool_count == 1:
        content = "I see the pods. Let me check the deployment details."
        tool_calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": "kubectl_describe", "arguments": json.dumps({"resourceType": "deployment", "name": svc, "namespace": ns})}}]
    elif tool_count == 2:
        content = "Based on the alert about high memory usage and the deployment state, I'll perform a rollback to restore the previous stable version."
        tool_calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": "kubectl_rollout", "arguments": json.dumps({"action": "undo", "resourceType": "deployment", "name": svc, "namespace": ns})}}]
    elif tool_count == 3:
        content = "Verifying the rollback was successful."
        tool_calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": "kubectl_get", "arguments": json.dumps({"resourceType": "pods", "namespace": ns, "name": ""})}}]
    else:
        content = """REMEDIATION COMPLETE:

Analysis:
- Alert: High memory usage on cart-service (94% of limit)
- Root cause: Recent deployment likely introduced memory leak
- Action: Rolled back to previous revision

Verification:
- Pods are running
- Rollback successful

The service should stabilize. Recommend monitoring for the next 15 minutes."""
        tool_calls = None
    
    resp = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "model": "Qwen2.5-Coder-7B",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop" if not tool_calls else "tool_calls"
        }]
    }
    
    if tool_calls:
        resp["choices"][0]["message"]["tool_calls"] = tool_calls
    
    time.sleep(0.3)  # Small delay for realism
    return jsonify(resp)

def start_llm_server():
    """Start fake LLM server in background."""
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    llm_app.run(host='127.0.0.1', port=8000, debug=False, use_reloader=False)

# ============================================================================
# CB ORCHESTRATOR (Simplified but real logic)
# ============================================================================

def run_orchestrator(alert: dict):
    """Run the orchestrator loop."""
    client = OpenAI(base_url="http://localhost:8000/v1", api_key="fake")
    
    system_prompt = """You are a Kubernetes operations assistant. Analyze alerts and take remediation actions.
Use the provided tools to investigate and fix issues. Target namespace: target-services."""
    
    alert_text = f"""ALERT: {alert['name']}
Severity: {alert['severity']}
Service: {alert['service']}
Namespace: {alert['namespace']}
Message: {alert['message']}
Value: {alert.get('value', 'N/A')}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Please remediate this alert:\n\n{alert_text}"}
    ]
    
    print(f"\n{'='*60}")
    print("CB Orchestrator - Processing Alert")
    print(f"{'='*60}")
    print(f"Alert: {alert['name']}")
    print(f"Service: {alert['service']}")
    print(f"Message: {alert['message']}")
    print(f"{'='*60}\n")
    
    max_iterations = 6
    for i in range(max_iterations):
        print(f"--- Turn {i+1} ---")
        
        response = client.chat.completions.create(
            model="Qwen2.5-Coder-7B",
            messages=messages,
            tools=TOOLS_SCHEMA,
            temperature=0.1
        )
        
        msg = response.choices[0].message
        
        if msg.content:
            print(f"LLM: {msg.content[:200]}{'...' if len(msg.content) > 200 else ''}")
        
        if not msg.tool_calls:
            print(f"\n{'='*60}")
            print("Final Response:")
            print(f"{'='*60}")
            print(msg.content)
            break
        
        # Add assistant message
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
        })
        
        # Execute tools
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except:
                args = {}
            
            print(f"  -> Executing: {tool_name}({json.dumps(args)})")
            result = execute_tool(tool_name, args)
            
            print(f"     Result: {result['output'][:100]}{'...' if len(result['output']) > 100 else ''}")
            
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result)
            })
        
        print()

# ============================================================================
# MAIN
# ============================================================================

def setup_workload():
    """Ensure cart-service exists."""
    print("Setting up demo workload...")
    ok, _ = kubectl("get deployment cart-service -n target-services")
    if not ok:
        yaml = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: cart-service
  namespace: target-services
spec:
  replicas: 2
  selector:
    matchLabels:
      app: cart-service
  template:
    metadata:
      labels:
        app: cart-service
    spec:
      containers:
      - name: app
        image: nginx:alpine
        resources:
          limits:
            memory: "128Mi"
            cpu: "100m"
"""
        with open('/tmp/cart.yaml', 'w') as f:
            f.write(yaml)
        kubectl("create namespace target-services 2>/dev/null || true")
        kubectl("apply -f /tmp/cart.yaml")
        print("Created cart-service deployment")
        time.sleep(3)
    else:
        print("cart-service already exists")

def main():
    print("\n" + "="*60)
    print("Autonomous Operations Platform - Live Demo")
    print("="*60 + "\n")
    
    # Check cluster
    ok, out = kubectl("cluster-info --request-timeout=5s 2>/dev/null")
    if not ok:
        print("ERROR: Kubernetes cluster not reachable. Run: minikube start")
        sys.exit(1)
    print("✓ Kubernetes cluster is running\n")
    
    # Setup workload
    setup_workload()
    
    # Start fake LLM
    print("\nStarting fake LLM server on :8000...")
    llm_thread = threading.Thread(target=start_llm_server, daemon=True)
    llm_thread.start()
    time.sleep(1)
    print("✓ LLM server running\n")
    
    # Wait for user
    input("Press ENTER to trigger an alert and start remediation...")
    
    # Create alert
    alert = {
        "name": "HighMemoryUsage",
        "severity": "critical",
        "service": "cart-service",
        "namespace": "target-services",
        "message": "Memory usage at 94% of limit",
        "value": 0.94
    }
    
    # Run orchestrator
    run_orchestrator(alert)
    
    print("\n" + "="*60)
    print("Demo Complete")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
