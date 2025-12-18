#!/bin/bash
# CB Model Entrypoint Script
# Starts vLLM OpenAI server with tool calling enabled
# Optionally starts gRPC bridge for MCP integration

set -e

echo "=============================================="
echo "CB Model - vLLM with Tool Calling + MCP"
echo "=============================================="
echo ""

# Configuration from environment
MODEL_NAME="${CB_MODEL_NAME:-Qwen/Qwen2.5-Coder-14B-Instruct-AWQ}"
MAX_MODEL_LEN="${CB_MAX_MODEL_LEN:-8192}"
MAX_NUM_SEQS="${CB_MAX_NUM_SEQS:-64}"
GPU_MEMORY_UTILIZATION="${CB_GPU_MEMORY_UTILIZATION:-0.90}"
TENSOR_PARALLEL_SIZE="${CB_TENSOR_PARALLEL_SIZE:-1}"
DTYPE="${CB_DTYPE:-auto}"
QUANTIZATION="${CB_QUANTIZATION:-awq}"
ENFORCE_EAGER="${CB_ENFORCE_EAGER:-false}"
HTTP_PORT="${CB_HTTP_PORT:-8000}"
ENABLE_TOOL_CALLING="${CB_ENABLE_TOOL_CALLING:-true}"
TOOL_CALL_PARSER="${CB_TOOL_CALL_PARSER:-hermes}"

# gRPC bridge settings
ENABLE_GRPC_BRIDGE="${CB_ENABLE_GRPC_BRIDGE:-false}"
GRPC_PORT="${CB_GRPC_PORT:-50051}"

echo "Configuration:"
echo "  Model: ${MODEL_NAME}"
echo "  Max Model Length: ${MAX_MODEL_LEN}"
echo "  Max Num Sequences: ${MAX_NUM_SEQS}"
echo "  GPU Memory Utilization: ${GPU_MEMORY_UTILIZATION}"
echo "  Tensor Parallel Size: ${TENSOR_PARALLEL_SIZE}"
echo "  Dtype: ${DTYPE}"
echo "  Quantization: ${QUANTIZATION}"
echo "  Enforce Eager: ${ENFORCE_EAGER}"
echo "  HTTP Port: ${HTTP_PORT}"
echo "  Tool Calling: ${ENABLE_TOOL_CALLING}"
echo "  Tool Call Parser: ${TOOL_CALL_PARSER}"
echo "  gRPC Bridge: ${ENABLE_GRPC_BRIDGE}"
echo "  gRPC Port: ${GRPC_PORT}"
echo ""

# Build vLLM command
VLLM_ARGS=(
    "serve"
    "${MODEL_NAME}"
    "--port" "${HTTP_PORT}"
    "--max-model-len" "${MAX_MODEL_LEN}"
    "--max-num-seqs" "${MAX_NUM_SEQS}"
    "--gpu-memory-utilization" "${GPU_MEMORY_UTILIZATION}"
    "--tensor-parallel-size" "${TENSOR_PARALLEL_SIZE}"
    "--dtype" "${DTYPE}"
    "--trust-remote-code"
)

# Add quantization if specified
if [ -n "${QUANTIZATION}" ] && [ "${QUANTIZATION}" != "none" ]; then
    VLLM_ARGS+=("--quantization" "${QUANTIZATION}")
fi

# Add enforce eager if enabled
if [ "${ENFORCE_EAGER}" = "true" ]; then
    VLLM_ARGS+=("--enforce-eager")
fi

# Add tool calling arguments
if [ "${ENABLE_TOOL_CALLING}" = "true" ]; then
    echo "Enabling tool calling with parser: ${TOOL_CALL_PARSER}"
    VLLM_ARGS+=("--enable-auto-tool-choice")
    VLLM_ARGS+=("--tool-call-parser" "${TOOL_CALL_PARSER}")
fi

# Add any extra arguments from environment
if [ -n "${CB_EXTRA_ARGS}" ]; then
    echo "Extra args: ${CB_EXTRA_ARGS}"
    # shellcheck disable=SC2206
    VLLM_ARGS+=(${CB_EXTRA_ARGS})
fi

echo ""
echo "Starting vLLM server..."
echo "Command: vllm ${VLLM_ARGS[*]}"
echo "=============================================="
echo ""

# Function to cleanup background processes
cleanup() {
    echo "Shutting down..."
    if [ -n "${GRPC_PID}" ]; then
        kill ${GRPC_PID} 2>/dev/null || true
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT

# Start gRPC bridge in background if enabled
if [ "${ENABLE_GRPC_BRIDGE}" = "true" ]; then
    echo "Starting gRPC bridge on port ${GRPC_PORT}..."
    
    # Wait for vLLM to be ready before starting gRPC bridge
    # We start it in background after a delay
    (
        # Wait for vLLM to start (check health endpoint)
        echo "Waiting for vLLM to start..."
        for i in $(seq 1 60); do
            if curl -s http://localhost:${HTTP_PORT}/health > /dev/null 2>&1; then
                echo "vLLM is ready!"
                break
            fi
            sleep 5
        done
        
        # Start gRPC bridge
        echo "Starting gRPC bridge..."
        cd /app
        python grpc_bridge.py --port ${GRPC_PORT}
    ) &
    GRPC_PID=$!
fi

# Start vLLM (foreground)
exec vllm "${VLLM_ARGS[@]}"
