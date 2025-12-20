# Alert Remediation Pipeline

This directory contains the **Alert Remediation Pipeline** - an AI-powered system for automatic Kubernetes incident response using Google Gemini API.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ALERT REMEDIATION FLOW                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   AlertManager ──webhook──► ALERT SERVER (alert_server.py)                   │
│                                   │                                          │
│                                   │ 1. Receive firing alert                  │
│                                   │ 2. Extract: fingerprint, namespace,      │
│                                   │    pod, container, labels                │
│                                   │ 3. Enrich context from:                  │
│                                   │    - Prometheus (metrics)                │
│                                   │    - Kiali (service mesh dependencies)   │
│                                   │    - K8s API (pod/deployment info)       │
│                                   │    - Loki (recent logs)                  │
│                                   │ 4. Build AlertRequest protobuf           │
│                                   │                                          │
│                                   ▼                                          │
│                              ┌─────────┐                                     │
│                              │  gRPC   │                                     │
│                              └────┬────┘                                     │
│                                   │                                          │
│                                   ▼                                          │
│   GEMINI CLIENT (gemini_client.py) ◄─────────────────────────────────────    │
│         │                                                                    │
│         │ 1. Deserialize AlertRequest                                       │
│         │ 2. Format as prompt for Gemini                                    │
│         │ 3. Call Gemini 1.5 Pro with function calling                      │
│         │ 4. Execute tool calls (kubectl operations)                        │
│         │ 5. Return AlertResponse with actions taken                        │
│         │                                                                    │
│         └───────► kubectl (via subprocess)                                  │
│                        │                                                     │
│                        ▼                                                     │
│                   Kubernetes API                                             │
│                   (get, describe, logs, scale, delete pod, rollout)         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Components

| Component | Description | Port |
|-----------|-------------|------|
| **Alert Server** | Receives AlertManager webhooks, enriches context, sends to Gemini client | 9095 (HTTP) |
| **Gemini Client** | Calls Gemini API with tools, executes kubectl commands | 50051 (gRPC) |

## Files

```
comms/
├── alert.proto              # Protobuf definitions for alert communication
├── requirements.txt         # Python dependencies
├── server/
│   ├── alert_server.py      # Alert Server implementation
│   └── Dockerfile           # Alert Server container
└── client/
    ├── gemini_client.py     # Gemini Client implementation
    └── Dockerfile           # Gemini Client container
```

## Quick Start

### 1. Create Gemini API Key Secret

```bash
kubectl create secret generic gemini-api-key \
  --from-literal=api-key=YOUR_GEMINI_API_KEY \
  -n monitoring
```

### 2. Build Docker Images

```bash
cd /home/scroll/Documents/cloud_automation/comms

# Build Alert Server
docker build -f server/Dockerfile -t chandrashekar316/capstone:alert_server .
docker push chandrashekar316/capstone:alert_server

# Build Gemini Client
docker build -f client/Dockerfile -t chandrashekar316/capstone:gemini_client .
docker push chandrashekar316/capstone:gemini_client
```

### 3. Deploy with Helm

```bash
cd /home/scroll/Documents/cloud_automation/helm/monitoring-services

helm upgrade --install monitoring-services . \
  --namespace monitoring \
  --create-namespace
```

### 4. Configure AlertManager Webhook

Add the webhook receiver to your AlertManager configuration:

```yaml
receivers:
  - name: 'alert-server-webhook'
    webhook_configs:
      - url: 'http://alert-server-service.monitoring.svc.cluster.local:9095/webhook'
        send_resolved: true

route:
  receiver: 'alert-server-webhook'
  routes:
    - match:
        severity: critical
      receiver: 'alert-server-webhook'
```

### 5. Test the Pipeline

```bash
# Port forward to alert server
kubectl port-forward svc/alert-server-service 9095:9095 -n monitoring &

# Send a test alert
curl -X POST http://localhost:9095/test \
  -H "Content-Type: application/json" \
  -d '{
    "alertname": "PodNotReady",
    "namespace": "target-services",
    "pod": "reviews-v1-abc123",
    "service": "reviews",
    "severity": "warning"
  }'
```

## Available Kubernetes Tools

The Gemini client can execute these kubectl operations:

| Tool | Description | Destructive |
|------|-------------|-------------|
| `kubectl_get` | List/get resources (pods, deployments, services) | No |
| `kubectl_describe` | Get detailed resource information | No |
| `kubectl_logs` | View pod logs | No |
| `kubectl_scale` | Scale deployment replicas | Yes |
| `kubectl_delete_pod` | Delete pod to force restart | Yes |
| `kubectl_rollout` | Manage rollouts (restart, undo, status) | Yes* |

*`rollout status` and `rollout history` are non-destructive.

## Configuration

### Alert Server Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_PORT` | 9095 | HTTP webhook port |
| `PROMETHEUS_URL` | (cluster internal) | Prometheus server URL |
| `KIALI_URL` | (cluster internal) | Kiali API URL |
| `LOKI_URL` | (cluster internal) | Loki API URL |
| `GEMINI_CLIENT_HOST` | gemini-client-service | Gemini client hostname |
| `GEMINI_CLIENT_PORT` | 50051 | Gemini client gRPC port |

### Gemini Client Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRPC_PORT` | 50051 | gRPC server port |
| `GEMINI_API_KEY` | (required) | Google Gemini API key |
| `GEMINI_MODEL` | gemini-1.5-pro | Gemini model to use |
| `TARGET_NAMESPACE` | target-services | Namespace for kubectl operations |
| `MCP_NON_DESTRUCTIVE` | false | Block destructive operations |
| `MAX_ITERATIONS` | 10 | Max tool call iterations |

## Security

### RBAC

The Gemini client runs with a scoped ServiceAccount that only has access to:
- **Read**: pods, logs, services, deployments, events, nodes
- **Write**: pods (delete), deployments (scale, patch)
- **Namespace**: Scoped to `target-services` namespace only

### Non-Destructive Mode

Set `MCP_NON_DESTRUCTIVE=true` to block all write operations:
- `kubectl scale` - blocked
- `kubectl delete pod` - blocked
- `kubectl rollout restart` - blocked
- `kubectl rollout undo` - blocked

## Troubleshooting

### Check Alert Server Logs
```bash
kubectl logs -f deployment/alert-server-deployment -n monitoring
```

### Check Gemini Client Logs
```bash
kubectl logs -f deployment/gemini-client-deployment -n monitoring
```

### Test Connectivity
```bash
# Alert Server health
curl http://alert-server-service.monitoring:9095/health

# Gemini Client health (via gRPC)
grpcurl -plaintext gemini-client-service.monitoring:50051 alert_remediation.AlertRemediationService/HealthCheck
```

## Comparison with vLLM Approach

| Aspect | Gemini API | Local vLLM |
|--------|------------|------------|
| **Infrastructure** | None (cloud API) | GPU node required |
| **Cost** | Pay per token | GPU compute cost |
| **Latency** | ~1-3s per call | ~0.5-1s per call |
| **Model Size** | Large (Gemini 1.5 Pro) | Limited by VRAM |
| **Tool Calling** | Native support | Requires hermes parser |
| **Deployment** | Simple container | Complex GPU setup |

This implementation uses **Gemini API** for simplicity and powerful function calling capabilities.
