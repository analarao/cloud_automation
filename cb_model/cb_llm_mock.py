#!/usr/bin/env python3
"""
CB LLM Mock Service - Simulates vLLM/Ollama API with streaming responses.

Provides:
- OpenAI-compatible /v1/chat/completions endpoint
- Token-by-token streaming for realistic output
- RAG integration with local knowledge base
- Proper latency simulation

Run: python3 cb_llm_mock.py
Endpoint: http://localhost:8000/v1/chat/completions
"""

import os
import sys
import json
import time
import random
import logging
from datetime import datetime
from typing import Generator, Dict, Any, List

from flask import Flask, request, Response, jsonify, stream_with_context

# Configure logging - format matches production services
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CB_LLM] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load knowledge base
KB_PATH = os.path.join(os.path.dirname(__file__), 'kb.json')

def load_knowledge_base() -> Dict:
    """Load the knowledge base JSON."""
    try:
        with open(KB_PATH, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Knowledge base not found at {KB_PATH}")
        return {"incidents": [], "runbooks": [], "service_topology": {}}

# ============================================================================
# RAG SIMULATION
# ============================================================================

def vector_search(query: str, top_k: int = 3) -> List[Dict]:
    """
    Simulate vector similarity search against knowledge base.
    Uses keyword matching to mimic semantic search.
    """
    logger.info(f"[RAG] Vector search: '{query[:50]}...'")
    
    # Simulate embedding + search latency
    time.sleep(random.uniform(0.3, 0.6))
    
    kb = load_knowledge_base()
    results = []
    query_lower = query.lower()
    query_terms = set(query_lower.split())
    
    # Search incidents
    for incident in kb.get("incidents", []):
        score = 0
        text = f"{incident['symptom']} {incident['root_cause']} {' '.join(incident['tags'])}".lower()
        for term in query_terms:
            if term in text:
                score += 1
        if score > 0:
            results.append({
                "type": "incident",
                "score": score,
                "data": incident
            })
    
    # Search runbooks
    for runbook in kb.get("runbooks", []):
        score = 0
        text = f"{runbook['title']} {runbook['trigger']} {' '.join(runbook['tags'])}".lower()
        for term in query_terms:
            if term in text:
                score += 1
        if score > 0:
            results.append({
                "type": "runbook", 
                "score": score,
                "data": runbook
            })
    
    # Sort by score and return top_k
    results.sort(key=lambda x: x['score'], reverse=True)
    retrieved = results[:top_k]
    
    logger.info(f"[RAG] Retrieved {len(retrieved)} documents (scores: {[r['score'] for r in retrieved]})")
    return retrieved

def format_context(retrieved_docs: List[Dict]) -> str:
    """Format retrieved documents as context for LLM."""
    if not retrieved_docs:
        return "No relevant historical data found."
    
    context_parts = []
    for doc in retrieved_docs:
        if doc['type'] == 'incident':
            inc = doc['data']
            context_parts.append(
                f"[Past Incident {inc['id']}] Service: {inc['service']}, "
                f"Symptom: {inc['symptom']}, Resolution: {inc['resolution']}"
            )
        elif doc['type'] == 'runbook':
            rb = doc['data']
            context_parts.append(
                f"[Runbook {rb['id']}] {rb['title']}: {'; '.join(rb['steps'][:2])}..."
            )
    
    return "\n".join(context_parts)

# ============================================================================
# LLM RESPONSE GENERATION
# ============================================================================

def analyze_alert(messages: List[Dict]) -> Dict[str, Any]:
    """Extract alert info from messages and generate analysis."""
    
    # Find the user message with alert details
    alert_text = ""
    for msg in messages:
        if msg.get('role') == 'user':
            alert_text = msg.get('content', '')
            break
    
    # Extract key info (simplified parsing)
    service = "cart-service"  # Default
    symptom = "high memory usage"
    namespace = "target-services"
    
    if "cart-service" in alert_text.lower():
        service = "cart-service"
    elif "reviews" in alert_text.lower():
        service = "reviews-v3"
    elif "payment" in alert_text.lower():
        service = "payment-service"
    
    if "memory" in alert_text.lower():
        symptom = "high memory usage"
    elif "cpu" in alert_text.lower():
        symptom = "high CPU utilization"
    elif "latency" in alert_text.lower():
        symptom = "elevated latency"
    elif "crash" in alert_text.lower():
        symptom = "pod crash loop"
    
    return {
        "service": service,
        "symptom": symptom,
        "namespace": namespace,
        "alert_text": alert_text
    }

def generate_response_text(alert_info: Dict, context: str, has_tools: bool) -> str:
    """Generate a realistic LLM response based on alert and context."""
    
    service = alert_info['service']
    symptom = alert_info['symptom']
    
    if has_tools:
        # Response that will use tools
        return f"I'll analyze the {symptom} alert for {service}. Let me investigate the current state."
    
    # Final summary response
    response = f"""Based on my analysis of the {symptom} in {service}:

**Retrieved Context:**
{context}

**Root Cause Analysis:**
The symptoms match historical incident patterns. The {symptom} pattern correlates with recent deployment activity, suggesting a regression in the latest release.

**Remediation Actions Taken:**
1. Verified current pod state and resource utilization
2. Checked deployment history for recent changes
3. Initiated rollback to previous stable revision
4. Confirmed pods are recovering

**Verification:**
- Pod status: Running
- Memory utilization: Normalizing
- No new error logs detected

REMEDIATION COMPLETE: The service should stabilize within 2-3 minutes. Recommend continued monitoring."""

    return response

def generate_tool_call(alert_info: Dict, tool_turn: int) -> Dict:
    """Generate appropriate tool call based on conversation turn."""
    
    ns = alert_info['namespace']
    svc = alert_info['service']
    
    tool_sequence = [
        {"name": "kubectl_get", "args": {"resourceType": "pods", "namespace": ns, "name": ""}},
        {"name": "kubectl_describe", "args": {"resourceType": "deployment", "name": svc, "namespace": ns}},
        {"name": "kubectl_rollout", "args": {"action": "undo", "resourceType": "deployment", "name": svc, "namespace": ns}},
        {"name": "kubectl_get", "args": {"resourceType": "pods", "namespace": ns, "name": ""}},
    ]
    
    if tool_turn < len(tool_sequence):
        tool = tool_sequence[tool_turn]
        return {
            "id": f"call_{random.randint(10000, 99999)}",
            "type": "function",
            "function": {
                "name": tool["name"],
                "arguments": json.dumps(tool["args"])
            }
        }
    return None

# ============================================================================
# STREAMING RESPONSE
# ============================================================================

def stream_tokens(text: str, delay_range: tuple = (0.02, 0.08)) -> Generator[str, None, None]:
    """Stream response token by token with realistic latency."""
    
    words = text.split()
    for i, word in enumerate(words):
        token = word + (" " if i < len(words) - 1 else "")
        
        chunk = {
            "id": f"chatcmpl-{random.randint(100000, 999999)}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "choices": [{
                "index": 0,
                "delta": {"content": token},
                "finish_reason": None
            }]
        }
        
        yield f"data: {json.dumps(chunk)}\n\n"
        time.sleep(random.uniform(*delay_range))
    
    # Final chunk
    final_chunk = {
        "id": f"chatcmpl-{random.randint(100000, 999999)}",
        "object": "chat.completion.chunk", 
        "created": int(time.time()),
        "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop"
        }]
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/v1/models', methods=['GET'])
def list_models():
    """List available models - mimics vLLM."""
    return jsonify({
        "object": "list",
        "data": [{
            "id": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "object": "model",
            "created": int(time.time()) - 86400,
            "owned_by": "local",
            "permission": [],
            "root": "Qwen/Qwen2.5-Coder-7B-Instruct"
        }]
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "model": "loaded", "gpu": "cuda:0"})

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """
    OpenAI-compatible chat completions endpoint.
    Supports both streaming and non-streaming responses.
    """
    data = request.json
    messages = data.get('messages', [])
    tools = data.get('tools', [])
    stream = data.get('stream', False)
    
    logger.info(f"[LLM] Received request with {len(messages)} messages, tools={bool(tools)}, stream={stream}")
    
    # Simulate model loading/thinking time
    time.sleep(random.uniform(0.2, 0.5))
    
    # Analyze the alert
    alert_info = analyze_alert(messages)
    
    # Count tool responses to determine conversation turn
    tool_responses = sum(1 for m in messages if m.get('role') == 'tool')
    
    # RAG retrieval
    query = f"{alert_info['service']} {alert_info['symptom']}"
    retrieved = vector_search(query)
    context = format_context(retrieved)
    
    # Determine if we should make a tool call or give final response
    if tools and tool_responses < 4:
        # Generate tool call
        tool_call = generate_tool_call(alert_info, tool_responses)
        
        explanations = [
            "Let me check the current pod status.",
            "I'll examine the deployment configuration.",
            "Based on the pattern, I'll initiate a rollback.",
            "Verifying the remediation was successful."
        ]
        content = explanations[min(tool_responses, len(explanations)-1)]
        
        response = {
            "id": f"chatcmpl-{random.randint(100000, 999999)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [tool_call] if tool_call else None
                },
                "finish_reason": "tool_calls" if tool_call else "stop"
            }],
            "usage": {
                "prompt_tokens": len(str(messages)) // 4,
                "completion_tokens": len(content) // 4,
                "total_tokens": (len(str(messages)) + len(content)) // 4
            }
        }
        
        logger.info(f"[LLM] Responding with tool call: {tool_call['function']['name'] if tool_call else 'none'}")
        return jsonify(response)
    
    # Generate final response
    response_text = generate_response_text(alert_info, context, False)
    
    if stream:
        logger.info("[LLM] Streaming response...")
        return Response(
            stream_with_context(stream_tokens(response_text)),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )
    
    # Non-streaming response
    response = {
        "id": f"chatcmpl-{random.randint(100000, 999999)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response_text
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": len(str(messages)) // 4,
            "completion_tokens": len(response_text) // 4,
            "total_tokens": (len(str(messages)) + len(response_text)) // 4
        }
    }
    
    logger.info("[LLM] Returning final response")
    return jsonify(response)

# ============================================================================
# RAG DEBUG ENDPOINT
# ============================================================================

@app.route('/v1/rag/search', methods=['POST'])
def rag_search():
    """Debug endpoint to test RAG retrieval."""
    data = request.json
    query = data.get('query', '')
    top_k = data.get('top_k', 3)
    
    results = vector_search(query, top_k)
    return jsonify({
        "query": query,
        "results": results,
        "context": format_context(results)
    })

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("CB LLM Mock Service")
    print("=" * 60)
    print(f"Model: Qwen/Qwen2.5-Coder-7B-Instruct (simulated)")
    print(f"Knowledge Base: {KB_PATH}")
    print(f"Endpoints:")
    print(f"  GET  http://localhost:8000/v1/models")
    print(f"  POST http://localhost:8000/v1/chat/completions")
    print(f"  POST http://localhost:8000/v1/rag/search")
    print(f"  GET  http://localhost:8000/health")
    print("=" * 60)
    
    # Verify KB exists
    kb = load_knowledge_base()
    print(f"Loaded {len(kb.get('incidents', []))} incidents, {len(kb.get('runbooks', []))} runbooks")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
