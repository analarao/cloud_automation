# CB Model (Container-Brain) - Agentic LLM with MCP Integration

The CB Model is the AI Decision Engine for the Autonomous Operations Platform. It runs a vLLM-based LLM with OpenAI-compatible API, integrated with MCP (Model Context Protocol) for **full Kubernetes operations** including exec, port-forwarding, and network debugging.

## Architecture (Phase 1-4 Complete)

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
│  │  - Advanced diagnostic capabilities                                   │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                      ▲                                       │
│                                      │ JSON-RPC (stdio)                      │
│                                      ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  MCP Server (mcp-server-kubernetes v3.x)                               │ │
│  │  BASIC: kubectl_get, kubectl_describe, kubectl_logs                   │ │
│  │  WRITE: kubectl_apply, kubectl_delete, kubectl_scale, kubectl_patch   │ │
│  │  ADVANCED: kubectl_generic (exec, port-forward, any kubectl command)  │ │
│  │  NETWORKING: port_forward, stop_port_forward                          │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  gRPC Bridge (:50051) - Optional backward compatibility               │ │
│  │  - Receives AlertAnalysisRequest                                      │ │
│  │  - Routes to Orchestrator                                             │ │
│  │  - Returns AlertAnalysisResponse                                      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ServiceAccount: cb-model-sa (RBAC: full target-services + cluster read)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Enhanced Capabilities (Phase 4)

### Advanced MCP Operations

The CB Model now supports the **full** `mcp-server-kubernetes` toolset including:

| Tool | Description |
|------|-------------|
| `kubectl_get` | Get/list resources (pods, deployments, services, ingresses, networkpolicies) |
| `kubectl_describe` | Get detailed resource info including events and conditions |
| `kubectl_logs` | Get pod logs (supports --previous for crashed containers) |
| `kubectl_scale` | Scale deployments/statefulsets up or down |
| `kubectl_delete` | Delete pods to restart them, or delete stuck resources |
| `kubectl_patch` | Patch resources to update configurations |
| `kubectl_rollout` | Manage deployment rollouts (restart, status, history, undo) |
| `kubectl_apply` | Apply YAML manifests for configuration changes |
| **`kubectl_generic`** | **Execute ANY kubectl command** (exec, port-forward, top, etc.) |
| `port_forward` | Start port forwarding to pods or services |
| `stop_port_forward` | Stop port forwarding sessions |

### Shell Access via kubectl_generic

The LLM can now **exec into pods** and run shell commands for advanced diagnostics:

```bash
# Example: Check application config inside container
kubectl exec -it <pod-name> -n target-services -- cat /etc/config/app.yaml

# Example: Test connectivity from within a pod
kubectl exec <pod> -- curl -s http://backend-service:8080/health

# Example: DNS debugging
kubectl exec <pod> -- nslookup kubernetes.default

# Example: Check network connections
kubectl exec <pod> -- netstat -tlnp
```

### Network Debugging

Built-in network debugging tools in the container:
- `nslookup`, `dig` - DNS resolution
- `nc` (netcat) - Port testing
- `ping` - ICMP connectivity
- `curl`, `wget` - HTTP testing
- `netstat` - Network connections

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

### 3. Test Advanced Remediation (Phase 4)

```bash
# Port-forward to the vLLM server
kubectl port-forward svc/cb-model-service 8000:8000 -n monitoring &

# Run advanced test scenarios
python test_advanced_remediation.py --test all
python test_advanced_remediation.py --test exec      # Test exec capabilities
python test_advanced_remediation.py --test network   # Test network diagnosis
python test_advanced_remediation.py --test ingress   # Test ingress troubleshooting
```
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

The CB Model uses a ServiceAccount with **expanded RBAC** for full operations:

### Target Namespace Permissions (target-services)

**Core Resources - Full Access:**
- `pods`, `pods/log`, `pods/exec`, `pods/portforward`, `pods/attach`
- `services`, `endpoints`
- `events` (read), `configmaps`, `secrets`
- `persistentvolumeclaims` (read)

**Apps - Full CRUD:**
- `deployments`, `replicasets`, `statefulsets`, `daemonsets`
- `deployments/scale`, `statefulsets/scale`, `replicasets/scale`

**Batch - Full CRUD:**
- `jobs`, `cronjobs`

**Networking - Full CRUD:**
- `ingresses`, `networkpolicies`
- `ingressclasses` (read)

**Istio (if enabled):**
- `virtualservices`, `destinationrules`, `gateways`, `serviceentries`

**Autoscaling & Policy:**
- `horizontalpodautoscalers` (full)
- `poddisruptionbudgets` (read)

### Cluster-Wide Permissions (Read Only)

- `nodes` - For resource diagnosis
- `namespaces` - For discovery
- `events` - Cluster-wide events
- `storageclasses`, `persistentvolumes` - Storage issues
- `ingressclasses` - Ingress troubleshooting

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
├── orchestrator.py          # Agentic LLM + MCP orchestration loop (Phase 3-4)
├── mcp_client.py            # Async MCP client wrapper (Phase 2)
├── grpc_bridge.py           # gRPC server (backward compat)
├── alert_context_aggregator.py  # Alert enrichment service
├── cb_model_v2.proto        # gRPC protocol definition
├── cb_model_v2_pb2.py       # Generated protobuf code
├── cb_model_v2_pb2_grpc.py  # Generated gRPC code
├── Dockerfile.toolcall      # Main Docker image (vLLM + MCP + network tools)
├── Dockerfile.aggregator    # Alert aggregator image
├── entrypoint.sh            # Container entrypoint
├── requirements.txt         # Python dependencies
├── requirements_aggregator.txt  # Alert aggregator dependencies
├── test_tool_calling.py     # Phase 1 test - vLLM tool calling
├── test_mcp_integration.py  # Phase 2 test - MCP integration
├── test_advanced_remediation.py  # Phase 4 test - Full remediation scenarios
├── download-model-job.yaml  # K8s job to pre-download model
└── README.md                # This file
```

## Common Issues and Solutions

### High CPU / Memory Issues
```bash
# LLM can scale deployments
kubectl_scale("deployment/web-service", replicas=5, namespace="target-services")

# Or restart problematic pods
kubectl_delete("pod/web-service-xxx", namespace="target-services")
```

### CrashLoopBackOff
```bash
# Check previous container logs
kubectl_logs("pod/api-service-xxx", namespace="target-services", previous=True)

# Check events
kubectl_describe("pod/api-service-xxx", namespace="target-services")
```

### Service Unreachable
```bash
# Test connectivity from within the cluster
kubectl_generic("kubectl exec web-pod -- curl -s http://api-service:8080/health")

# Check endpoints
kubectl_get("endpoints", name="api-service", namespace="target-services")
```

### Network Policy Blocking Traffic
```bash
# List network policies
kubectl_get("networkpolicies", namespace="target-services")

# Test DNS resolution
kubectl_generic("kubectl exec web-pod -- nslookup api-service")
```

### Ingress 502 Errors
```bash
# Check ingress configuration
kubectl_describe("ingress/main-ingress", namespace="target-services")

# Verify backend endpoints
kubectl_get("endpoints", namespace="target-services")

# Test backend directly
kubectl_generic("kubectl exec debug-pod -- curl http://backend-service:8080/health")
```
