#!/usr/bin/env python3
"""
Fake LLM Server - OpenAI-compatible API that returns scripted responses.

This mimics vLLM's OpenAI-compatible endpoint so we can demo the full pipeline
without needing actual GPU/LLM inference.

Endpoints:
- GET  /v1/models - List models
- POST /v1/chat/completions - Chat completions with tool calling

Run: python3 fake_llm_server.py
"""

import json
import time
import uuid
import re
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Track conversation state for realistic multi-turn responses
conversation_state = {
    "turn": 0,
    "namespace": "target-services",
    "service": "cart-service"
}

def reset_conversation():
    conversation_state["turn"] = 0

@app.route('/v1/models', methods=['GET'])
def list_models():
    """List available models."""
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": "Qwen/Qwen2.5-Coder-7B-Instruct",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local"
            }
        ]
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"})

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """Handle chat completion requests with tool calling."""
    data = request.json
    messages = data.get('messages', [])
    tools = data.get('tools', [])
    
    # Get the last user/tool message
    last_message = messages[-1] if messages else {}
    content = last_message.get('content', '')
    role = last_message.get('role', '')
    
    # Determine what turn we're on based on message history
    tool_calls_count = sum(1 for m in messages if m.get('role') == 'tool')
    
    response_content = ""
    tool_calls = None
    
    # Parse namespace from system prompt
    for msg in messages:
        if msg.get('role') == 'system':
            if 'target-services' in msg.get('content', ''):
                conversation_state['namespace'] = 'target-services'
    
    ns = conversation_state['namespace']
    svc = conversation_state['service']
    
    # Decision logic based on conversation state
    if tool_calls_count == 0:
        # First turn: investigate with kubectl_get
        response_content = f"I'll investigate the alert by checking the current state of pods in the {ns} namespace."
        tool_calls = [{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": "kubectl_get",
                "arguments": json.dumps({
                    "resourceType": "pods",
                    "namespace": ns,
                    "name": ""
                })
            }
        }]
    
    elif tool_calls_count == 1:
        # Second turn: check deployment
        response_content = f"I can see the pods. Let me check the deployment status and recent events."
        tool_calls = [{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": "kubectl_describe",
                "arguments": json.dumps({
                    "resourceType": "deployment",
                    "name": svc,
                    "namespace": ns
                })
            }
        }]
    
    elif tool_calls_count == 2:
        # Third turn: check logs
        response_content = "Let me check the logs for any errors."
        tool_calls = [{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": "kubectl_logs",
                "arguments": json.dumps({
                    "podName": f"{svc}-pod",
                    "namespace": ns,
                    "tail": 50
                })
            }
        }]
    
    elif tool_calls_count == 3:
        # Fourth turn: take action - rollback
        response_content = f"Based on the memory growth pattern and recent deployment, I'll rollback {svc} to the previous version."
        tool_calls = [{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function", 
            "function": {
                "name": "kubectl_rollout",
                "arguments": json.dumps({
                    "action": "undo",
                    "resourceType": "deployment",
                    "name": svc,
                    "namespace": ns
                })
            }
        }]
    
    elif tool_calls_count == 4:
        # Fifth turn: verify
        response_content = "Let me verify the rollback was successful."
        tool_calls = [{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": "kubectl_get",
                "arguments": json.dumps({
                    "resourceType": "pods",
                    "namespace": ns,
                    "name": ""
                })
            }
        }]
    
    else:
        # Final turn: complete
        response_content = f"""REMEDIATION COMPLETE:

Analysis Summary:
- Alert indicated memory pressure on {svc}
- Investigation showed memory usage trending toward limit
- Root cause: Recent deployment introduced memory leak
- Action taken: Rolled back deployment to previous version
- Verification: Pods are now running with stable memory usage

The service should now be healthy. Continue monitoring for any recurrence."""
        tool_calls = None
        reset_conversation()
    
    # Build response
    response = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response_content
            },
            "finish_reason": "stop" if not tool_calls else "tool_calls"
        }],
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 150,
            "total_tokens": 650
        }
    }
    
    if tool_calls:
        response["choices"][0]["message"]["tool_calls"] = tool_calls
        response["choices"][0]["message"]["content"] = response_content
    
    # Add small delay to seem realistic
    time.sleep(0.5)
    
    return jsonify(response)

if __name__ == '__main__':
    print("=" * 60)
    print("Fake LLM Server (OpenAI-compatible)")
    print("=" * 60)
    print(f"Endpoints:")
    print(f"  GET  http://localhost:8000/v1/models")
    print(f"  POST http://localhost:8000/v1/chat/completions")
    print("=" * 60)
    app.run(host='0.0.0.0', port=8000, debug=False)
