# Alert Server - Dockerfile
# Receives AlertManager webhooks, enriches context, sends to Gemini client
#
# Image: chandrashekar316/capstone:alert_server

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
COPY server/alert_server.py .

# Webhook port
EXPOSE 9095

ENV PYTHONUNBUFFERED=1 \
    WEBHOOK_PORT=9095 \
    PROMETHEUS_URL="http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090" \
    ALERTMANAGER_URL="http://prometheus-kube-prometheus-alertmanager.monitoring.svc.cluster.local:9093" \
    KIALI_URL="http://kiali.istio-system.svc.cluster.local:20001" \
    LOKI_URL="http://loki.monitoring.svc.cluster.local:3100" \
    GEMINI_CLIENT_HOST="gemini-client-service.monitoring.svc.cluster.local" \
    GEMINI_CLIENT_PORT="50051"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:9095/health || exit 1

CMD ["python", "alert_server.py"]
