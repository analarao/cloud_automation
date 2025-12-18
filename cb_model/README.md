# CB Model (Container-Brain) - Agentic LLM with MCP Integration

The CB Model is the AI Decision Engine for the Autonomous Operations Platform. It runs a vLLM-based LLM with OpenAI-compatible API, integrated with MCP (Model Context Protocol) for real Kubernetes operations.

## Architecture (Phase 1-3 Complete)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CB Model Pod                                    │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  vLLM OpenAI Server (:8000)                                            │ │
│  │  - Qwen/Qwen2.5-Coder-14B-Instruct-AWQ                                │ │
│  │  - --enable-auto-tool-choice                                          │ │
│  │  - --tool-call-parser hermes                                          │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                      ▲                                       │
│                                      │ HTTP                                  │
│                                      ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  Orchestrator (orchestrator.py)                                        │ │
│  │  - Agentic reasoning loop                                             │ │
│  │  - Tool call execution                                                │ │
│  │  - MCP client management                                              │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                      ▲                                       │
│                                      │ JSON-RPC (stdio)                      │
│                                      ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  MCP Server (mcp-server-kubernetes)                                    │ │
│  │  - kubectl_get, kubectl_describe, kubectl_logs                        │ │
│  │  - kubectl_apply, kubectl_delete, kubectl_scale                       │ │
│  │  - Real K8s operations via ServiceAccount RBAC                        │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  gRPC Bridge (:50051) - Optional backward compatibility               │ │
│  │  - Receives AlertAnalysisRequest                                      │ │
│  │  - Routes to Orchestrator                                             │ │
│  │  - Returns AlertAnalysisResponse                                      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ServiceAccount: cb-model-sa (RBAC scoped to target-services namespace)     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Prerequisites

- Kubernetes cluster with NVIDIA GPU support
- NVIDIA GPU drivers and nvidia-docker installed
- kubectl configured
- Helm 3.x installed
- Node.js (for mcp-server-kubernetes)

### 2. Build and Deploy

```bash
# Navigate to cb_model directory
cd /home/big_scroll/Documents/cloud_automation/cb_model

# Build the Docker image with tool calling + MCP
docker build -f Dockerfile.toolcall -t chandrashekar316/capstone:cb_model-toolcall .

# Push to registry
docker push chandrashekar316/capstone:cb_model-toolcall

# Deploy with Helm
cd ../helm/monitoring-services
helm upgrade --install monitoring-services . --namespace monitoring --create-namespace

# Create target-services namespace (for RBAC scope)
kubectl create namespace target-services --dry-run=client -o yaml | kubectl apply -f -

# Watch the pod come up
kubectl get pods -n monitoring -l app=cb-model -w
```

### 3. Test Tool Calling (Phase 1)

```bash
# Port-forward to the vLLM server
kubectl port-forward svc/cb-model-service 8000:8000 -n monitoring &

# Run tool calling test
python test_tool_calling.py
```

### 4. Test MCP Integration (Phase 2)

```bash
# Run MCP integration test
python test_mcp_integration.py --vllm-url http://localhost:8000/v1 --namespace target-services
```

### 5. Test Orchestrator (Phase 3)

```bash
# Test the full orchestrator loop with a simulated alert
python orchestrator.py \
  --vllm-url http://localhost:8000/v1 \
  --alert-name "PodNotReady" \
  --message "Pod reviews-v1 is not ready" \
  --namespace target-services \
  --max-iterations 5
```

## Key Files

| File | Purpose |
|------|---------|
| `orchestrator.py` | Main agentic loop - LLM reasoning + MCP tool execution |
| `mcp_client.py` | Async Python wrapper for mcp-server-kubernetes |
| `grpc_bridge.py` | gRPC server for backward compatibility with alert aggregator |
| `Dockerfile.toolcall` | Docker image with vLLM + MCP + orchestrator |
| `entrypoint.sh` | Container startup script |
| `test_tool_calling.py` | Phase 1 test - vLLM tool calling |
| `test_mcp_integration.py` | Phase 2 test - MCP integration |
| `cb_model_v2.proto` | gRPC protocol definition (V2 with rich context) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CB_MODEL_NAME` | HuggingFace model name | `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ` |
| `CB_MAX_MODEL_LEN` | Maximum context length | `8192` |
| `CB_GPU_MEMORY_UTILIZATION` | GPU memory usage (0.0-1.0) | `0.90` |
| `CB_ENABLE_TOOL_CALLING` | Enable vLLM tool calling | `true` |
| `CB_TOOL_CALL_PARSER` | Tool call parser (hermes) | `hermes` |
| `CB_ENABLE_GRPC_BRIDGE` | Enable gRPC backward compat | `false` |
| `CB_HTTP_PORT` | vLLM OpenAI API port | `8000` |
| `CB_GRPC_PORT` | gRPC bridge port | `50051` |
| `CB_TARGET_NAMESPACE` | K8s namespace for MCP operations | `target-services` |

## RBAC Configuration

The CB Model uses a ServiceAccount with RBAC scoped to the `target-services` namespace:

**Read Operations (always allowed):**
- pods, pods/log, services, endpoints, events, configmaps
- deployments, replicasets, statefulsets, daemonsets
- Istio: virtualservices, destinationrules, gateways

**Write Operations (for remediation):**
- pods (delete for restarts)
- deployments/scale, statefulsets/scale
- configmaps (create, update, patch)

## Alert Aggregator Integration

The Alert Context Aggregator receives alerts from AlertManager, enriches them with context, and forwards to CB Model:

```
AlertManager → Alert Aggregator → gRPC Bridge → Orchestrator → MCP → K8s
                    ↓
             Context from:
             - Prometheus (metrics)
             - Loki (logs)
             - Kiali (service mesh)
             - K8s API (resources)
```

## Troubleshooting

### Pod Stuck in Pending
```bash
kubectl describe node | grep -A5 "nvidia.com/gpu"
kubectl get pods -n kube-system | grep nvidia
```

### MCP Server Not Available
```bash
# Check if mcp-server-kubernetes is installed
npx @anthropic/mcp-server-kubernetes --version

# Check Node.js
node --version
```

### Tool Calls Not Executing
- Ensure `CB_ENABLE_TOOL_CALLING=true`
- Check vLLM logs for tool parsing errors
- Verify model supports Hermes tool call format

### gRPC Connection Refused
```bash
kubectl get svc cb-model-service -n monitoring
kubectl logs deployment/cb-model-deployment -n monitoring
```

## Development

### Running Locally (without K8s)

```bash
# Start vLLM server manually
vllm serve Qwen/Qwen2.5-Coder-14B-Instruct-AWQ \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --port 8000

# In another terminal, run orchestrator
python orchestrator.py --vllm-url http://localhost:8000/v1 --alert-name "Test"
```

### Adding New MCP Tools

The MCP client uses `mcp-server-kubernetes` which provides kubectl operations. To add custom tools, modify `mcp_client.py`.

## File Structure

```
cb_model/
├── orchestrator.py          # Agentic LLM + MCP orchestration loop
├── mcp_client.py            # Async MCP client wrapper
├── grpc_bridge.py           # gRPC server (backward compat)
├── alert_context_aggregator.py  # Alert enrichment service
├── cb_model_v2.proto        # gRPC protocol definition
├── cb_model_v2_pb2.py       # Generated protobuf code
├── cb_model_v2_pb2_grpc.py  # Generated gRPC code
├── Dockerfile.toolcall      # Main Docker image
├── Dockerfile.aggregator    # Alert aggregator image
├── entrypoint.sh            # Container entrypoint
├── requirements.txt         # Python dependencies
├── requirements_aggregator.txt  # Alert aggregator dependencies
├── test_tool_calling.py     # Phase 1 test
├── test_mcp_integration.py  # Phase 2 test
├── download-model-job.yaml  # K8s job to pre-download model
└── README.md                # This file
```
