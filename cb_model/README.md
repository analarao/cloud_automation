# CB Model (Container-Brain) - Local LLM with gRPC Interface

The CB Model is the AI Decision Engine for the Autonomous Operations Platform. It runs a local LLM via vLLM and exposes a gRPC interface for other services (AlertManager, CS Model) to request completions and MCP command execution.

## Architecture

```
┌─────────────────┐     gRPC (protobuf)     ┌─────────────────┐
│   CS Model      │ ───────────────────────▶│                 │
│   (Alerts)      │                         │    CB Model     │
└─────────────────┘                         │    (vLLM)       │
                                            │                 │
┌─────────────────┐     gRPC (protobuf)     │  ┌───────────┐  │
│  AlertManager   │ ───────────────────────▶│  │   LLM     │  │
│   (Webhooks)    │                         │  │  (GPU)    │  │
└─────────────────┘                         │  └───────────┘  │
                                            └─────────────────┘
```

## Quick Start

### 1. Prerequisites

- Kubernetes cluster with NVIDIA GPU support
- NVIDIA GPU drivers and nvidia-docker installed
- kubectl configured
- Helm 3.x installed

### 2. Download Model Locally (Optional - for faster startup)

The model will be downloaded automatically on first startup, but you can pre-download it:

```bash
# Option A: Pre-download to a PersistentVolume
# Create a job to download the model
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: download-llm-model
  namespace: monitoring
spec:
  template:
    spec:
      containers:
      - name: downloader
        image: python:3.10-slim
        command:
        - bash
        - -c
        - |
          pip install huggingface_hub
          python -c "
          from huggingface_hub import snapshot_download
          snapshot_download('TinyLlama/TinyLlama-1.1B-Chat-v1.0', 
                           cache_dir='/models')
          "
        volumeMounts:
        - name: model-cache
          mountPath: /models
      restartPolicy: Never
      volumes:
      - name: model-cache
        persistentVolumeClaim:
          claimName: cb-model-deployment-cache
  backoffLimit: 1
EOF
```

```bash
# Option B: Download locally and copy to cluster
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('TinyLlama/TinyLlama-1.1B-Chat-v1.0')"
```

### 3. Deploy with Helm

```bash
# Navigate to the helm chart directory
cd helm/monitoring-services

# Install/upgrade the chart
helm upgrade --install monitoring-services . \
  --namespace monitoring \
  --create-namespace

# Check deployment status
kubectl get pods -n monitoring -l app=cb-model
kubectl logs -f deployment/cb-model-deployment -n monitoring
```

### 4. Changing the Model

Edit `values.yaml`:

```yaml
cbModel:
  model:
    # Change this to use a different model
    name: "mistralai/Mistral-7B-Instruct-v0.2"
    maxModelLen: "4096"
```

Then upgrade the Helm release:
```bash
helm upgrade monitoring-services . -n monitoring
```

### 5. For Gated Models (e.g., Llama 2)

```bash
# Create HuggingFace token secret
kubectl create secret generic hf-token-secret \
  --from-literal=token=YOUR_HUGGINGFACE_TOKEN \
  -n monitoring

# Enable in values.yaml
cbModel:
  huggingfaceSecret:
    enabled: true
    name: "hf-token-secret"
```

## Testing the LLM

### Method 1: Port-Forward and Test Script

```bash
# Terminal 1: Port-forward the gRPC service
kubectl port-forward svc/cb-model-service 50051:50051 -n monitoring

# Terminal 2: Generate gRPC code and run tests
cd cb_model

# Generate Python gRPC code from proto
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cb_model.proto

# Run the test client
python test_cb_model.py

# Run specific tests
python test_cb_model.py --test health
python test_cb_model.py --test alert
```

### Method 2: From Within the Cluster

```bash
# Deploy a test pod
kubectl run -it grpc-test --image=python:3.10-slim -n monitoring --rm -- bash

# Inside the pod:
pip install grpcio grpcio-tools
# Copy the proto file and generate code
# Then run the test client
```

### Method 3: Quick Health Check

```bash
# Check if the pod is running
kubectl get pods -n monitoring -l app=cb-model

# Check logs for model loading
kubectl logs -f deployment/cb-model-deployment -n monitoring

# Look for: "vLLM model loaded successfully!"
```

### Method 4: grpcurl (if installed)

```bash
# Install grpcurl: https://github.com/fullstorydev/grpcurl
kubectl port-forward svc/cb-model-service 50051:50051 -n monitoring &

# Health check
grpcurl -plaintext localhost:50051 cb_model.CBModelService/HealthCheck

# Model info
grpcurl -plaintext localhost:50051 cb_model.CBModelService/GetModelInfo

# Generate completion
grpcurl -plaintext -d '{
  "prompt": "What is Kubernetes?",
  "max_tokens": 100,
  "source": "grpcurl-test"
}' localhost:50051 cb_model.CBModelService/GenerateCompletion
```

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CB_MODEL_NAME` | HuggingFace model name | `TinyLlama/TinyLlama-1.1B-Chat-v1.0` |
| `CB_MAX_MODEL_LEN` | Maximum context length | `4096` |
| `CB_GPU_MEMORY_UTILIZATION` | GPU memory usage (0.0-1.0) | `0.95` |
| `CB_TENSOR_PARALLEL_SIZE` | Number of GPUs for tensor parallelism | `1` |
| `CB_DTYPE` | Data type (auto, half, float16, bfloat16) | `auto` |
| `CB_QUANTIZATION` | Quantization method (awq, gptq, squeezellm) | `""` |
| `CB_GRPC_PORT` | gRPC server port | `50051` |
| `CB_SYSTEM_PROMPT` | Default system prompt for all requests | (see values.yaml) |
| `HUGGING_FACE_HUB_TOKEN` | HuggingFace token for gated models | `""` |

### values.yaml Key Sections

```yaml
cbModel:
  # Enable/disable the CB Model
  enabled: true
  
  # Model selection - EDIT THIS TO CHANGE MODELS
  model:
    name: "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    maxModelLen: "4096"
  
  # System prompt - EDIT THIS TO CHANGE LLM BEHAVIOR
  systemPrompt: |
    You are CB (Container-Brain), an expert SRE...
  
  # GPU settings
  gpu:
    enabled: true
    count: 1
    memoryUtilization: "0.95"
```

## Troubleshooting

### Pod Stuck in Pending

```bash
# Check for GPU availability
kubectl describe node | grep -A5 "nvidia.com/gpu"

# Check if NVIDIA device plugin is running
kubectl get pods -n kube-system | grep nvidia
```

### Model Loading Slow

- First startup downloads the model (~1-10 mins depending on size)
- Check logs: `kubectl logs -f deployment/cb-model-deployment -n monitoring`
- Consider pre-downloading (see Quick Start section)

### Out of Memory

```bash
# Reduce GPU memory utilization
cbModel:
  gpu:
    memoryUtilization: "0.8"

# Or use a smaller model
cbModel:
  model:
    name: "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    maxModelLen: "2048"
```

### gRPC Connection Refused

```bash
# Check if service is exposed
kubectl get svc cb-model-service -n monitoring

# Check if pod is ready
kubectl get pods -n monitoring -l app=cb-model

# Check pod logs for errors
kubectl logs deployment/cb-model-deployment -n monitoring
```

## Integration with Other Services

### From CS Model Service

```python
from cb_model_client import CBModelClient

client = CBModelClient()
response = client.generate_completion(
    prompt="Analyze this alert: CPU usage at 95% on cart-service",
    source="cs_model"
)
print(response.completion)
```

### From AlertManager Webhook

```python
# In your webhook handler
from cb_model_client import generate_completion

mcp_command = generate_completion(
    prompt=f"Alert: {alert_data}. Generate remediation commands.",
    source="alertmanager"
)
```

## File Structure

```
cb_model/
├── cb_model.proto          # gRPC service definition
├── cb_model_server.py      # vLLM gRPC server
├── cb_model_client.py      # Client library for other services
├── test_cb_model.py        # Test script
├── Dockerfile              # Container image definition
├── requirements.txt        # Python dependencies
└── README.md               # This file
```
