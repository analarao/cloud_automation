#!/bin/bash
# Build and deploy the alert pipeline to the cluster
# 
# Usage:
#   ./deploy_alert_pipeline.sh              # Build and deploy
#   ./deploy_alert_pipeline.sh --local      # Run locally instead

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Configuration
IMAGE_NAME="chandrashekar316/capstone"
IMAGE_TAG="alert-pipeline"
NAMESPACE="monitoring"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [[ "$1" == "--local" ]]; then
    echo -e "${YELLOW}Running alert pipeline locally...${NC}"
    echo ""
    echo "Make sure you have port-forwards running:"
    echo "  kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090"
    echo "  kubectl port-forward -n monitoring svc/cb-model-service 8000:8000"
    echo ""
    
    python alert_pipeline.py \
        --prometheus-url "${PROMETHEUS_URL:-http://localhost:9090}" \
        --vllm-url "${VLLM_URL:-http://localhost:8000/v1}" \
        --namespace "${TARGET_NAMESPACE:-target-services}" \
        --port 9095
    exit 0
fi

echo -e "${GREEN}Building alert pipeline image...${NC}"

# Build the image
docker build -f Dockerfile.alert-pipeline -t ${IMAGE_NAME}:${IMAGE_TAG} .

# Push to Docker Hub
echo -e "${GREEN}Pushing to Docker Hub (${IMAGE_NAME}:${IMAGE_TAG})...${NC}"
docker push ${IMAGE_NAME}:${IMAGE_TAG}
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

echo -e "${GREEN}Deploying to Kubernetes...${NC}"

# Create/update the deployment
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: alert-pipeline
  namespace: ${NAMESPACE}
  labels:
    app: alert-pipeline
spec:
  replicas: 1
  selector:
    matchLabels:
      app: alert-pipeline
  template:
    metadata:
      labels:
        app: alert-pipeline
    spec:
      serviceAccountName: cb-model-sa
      containers:
        - name: alert-pipeline
          image: ${FULL_IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 9095
              name: webhook
          env:
            - name: PROMETHEUS_URL
              value: "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090"
            - name: CB_MODEL_OPENAI_API_URL
              value: "http://cb-model-service.monitoring.svc.cluster.local:8000/v1"
            - name: TARGET_NAMESPACE
              value: "target-services"
            - name: MAX_ITERATIONS
              value: "10"
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
          livenessProbe:
            httpGet:
              path: /health
              port: 9095
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 9095
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: alert-pipeline-service
  namespace: ${NAMESPACE}
  labels:
    app: alert-pipeline
spec:
  type: ClusterIP
  ports:
    - port: 9095
      targetPort: 9095
      protocol: TCP
      name: webhook
  selector:
    app: alert-pipeline
EOF

echo -e "${GREEN}Waiting for deployment to be ready...${NC}"
kubectl rollout status deployment/alert-pipeline -n ${NAMESPACE} --timeout=120s

echo ""
echo -e "${GREEN}✓ Alert pipeline deployed!${NC}"
echo ""
echo "View logs with:"
echo "  kubectl logs -f -n ${NAMESPACE} deployment/alert-pipeline"
echo ""
echo "Test the webhook:"
echo "  kubectl port-forward -n ${NAMESPACE} svc/alert-pipeline-service 9095:9095"
echo "  curl http://localhost:9095/health"
