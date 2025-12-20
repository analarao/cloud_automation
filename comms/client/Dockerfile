# Gemini Client - Dockerfile
# Receives alerts via gRPC, calls Gemini API with MCP-style tool calling
#
# Image: chandrashekar316/capstone:gemini_client

FROM python:3.11-slim

WORKDIR /app

# Install kubectl for Kubernetes operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/ \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

# Copy proto file and compile
COPY alert.proto .
RUN python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    alert.proto

# Copy application
COPY client/gemini_client.py .

# gRPC port
EXPOSE 50051

ENV PYTHONUNBUFFERED=1 \
    GRPC_PORT=50051 \
    GEMINI_MODEL="gemini-1.5-pro" \
    GEMINI_TEMPERATURE=0.1 \
    TARGET_NAMESPACE="target-services" \
    MCP_NON_DESTRUCTIVE="false" \
    MAX_ITERATIONS=10

# Health check via gRPC
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import grpc; ch = grpc.insecure_channel('localhost:50051'); grpc.channel_ready_future(ch).result(timeout=5)" || exit 1

CMD ["python", "gemini_client.py"]
