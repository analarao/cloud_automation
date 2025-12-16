# CB Model (Container-Brain) Dockerfile
# chandrashekar316/capstone:cb_model
#
# This container runs the CB (Container-Brain) LLM service with vLLM
# and exposes a gRPC interface for other services to request completions.
#
# OPTIMIZED: Uses official vLLM image as base (~8GB vs ~15GB+ with full CUDA devel)
# The vLLM image already includes: Python, PyTorch, CUDA runtime, vLLM

# Use official vLLM image - already optimized for inference
FROM vllm/vllm-openai:latest

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install only gRPC dependencies (vLLM image already has everything else)
RUN pip install --no-cache-dir grpcio grpcio-tools protobuf

# Copy proto file and generate Python code
COPY cb_model.proto .
RUN python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    cb_model.proto

# Copy the server code
COPY cb_model_server.py .

# Expose gRPC port
EXPOSE 50051

# Environment variables (can be overridden)
ENV CB_GRPC_PORT=50051
ENV CB_MODEL_NAME="TinyLlama/TinyLlama-1.1B-Chat-v1.0"
# NOTE: TinyLlama only supports 2048 max_position_embeddings
ENV CB_MAX_MODEL_LEN=2048
# NOTE: 0.85 leaves headroom for CUDA context overhead
ENV CB_GPU_MEMORY_UTILIZATION=0.85
ENV CB_TENSOR_PARALLEL_SIZE=1
ENV CB_DTYPE=auto
ENV CB_MAX_WORKERS=10

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import grpc; ch = grpc.insecure_channel('localhost:50051'); grpc.channel_ready_future(ch).result(timeout=5)" || exit 1

# Override the vllm entrypoint to run our custom server
ENTRYPOINT []
CMD ["python", "cb_model_server.py"]
