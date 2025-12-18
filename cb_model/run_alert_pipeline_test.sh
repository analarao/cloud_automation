#!/bin/bash
# run_alert_pipeline_test.sh
# Helper script to run alert pipeline tests

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}Alert Pipeline Test Runner${NC}"
echo -e "${GREEN}============================================${NC}"

# Default values
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
VLLM_URL="${VLLM_URL:-http://localhost:8000/v1}"
NAMESPACE="${NAMESPACE:-target-services}"
ALERT="${ALERT:-BookinfoReviewsDown}"

# Parse arguments
CONTEXT_ONLY=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --local)
            echo -e "${YELLOW}Setting up port forwards for local testing...${NC}"
            
            # Kill any existing port forwards
            pkill -f "kubectl port-forward.*prometheus" 2>/dev/null || true
            pkill -f "kubectl port-forward.*vllm\|cb-model" 2>/dev/null || true
            
            # Start Prometheus port forward
            echo "Starting Prometheus port forward (9090)..."
            kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090 &
            PROM_PID=$!
            
            # Start vLLM port forward (if running in cluster)
            echo "Starting vLLM port forward (8000)..."
            kubectl port-forward -n monitoring svc/cb-model-service 8000:8000 &
            VLLM_PID=$!
            
            # Wait for port forwards to be ready
            sleep 3
            
            PROMETHEUS_URL="http://localhost:9090"
            VLLM_URL="http://localhost:8000/v1"
            shift
            ;;
        --context-only)
            CONTEXT_ONLY="--context-only"
            shift
            ;;
        --alert)
            ALERT="$2"
            shift 2
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --local           Setup port forwards for local testing"
            echo "  --context-only    Only test context aggregation (no LLM)"
            echo "  --alert NAME      Alert to test (default: BookinfoReviewsDown)"
            echo "  --namespace NS    Target namespace (default: target-services)"
            echo ""
            echo "Available alerts:"
            echo "  - BookinfoReviewsDown"
            echo "  - PredictedCpuBreach"
            echo "  - CS_Memory_Exhaustion_Predicted"
            echo "  - CS_CPU_High_Anomaly_Detected"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo ""
echo "Configuration:"
echo "  Prometheus: $PROMETHEUS_URL"
echo "  vLLM:       $VLLM_URL"
echo "  Namespace:  $NAMESPACE"
echo "  Alert:      $ALERT"
echo ""

# Check Prometheus connectivity
echo -e "${YELLOW}Checking Prometheus connectivity...${NC}"
if curl -s "$PROMETHEUS_URL/api/v1/status/config" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Prometheus is reachable${NC}"
else
    echo -e "${RED}✗ Cannot reach Prometheus at $PROMETHEUS_URL${NC}"
    echo "  Make sure Prometheus is running and accessible."
    echo "  Try: kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090"
    exit 1
fi

# Check vLLM connectivity (only if not context-only)
if [ -z "$CONTEXT_ONLY" ]; then
    echo -e "${YELLOW}Checking vLLM connectivity...${NC}"
    VLLM_HEALTH_URL=$(echo $VLLM_URL | sed 's|/v1||')/health
    if curl -s "$VLLM_HEALTH_URL" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ vLLM is reachable${NC}"
    else
        echo -e "${YELLOW}⚠ Cannot reach vLLM at $VLLM_URL${NC}"
        echo "  Continuing anyway - will test context aggregation only."
        CONTEXT_ONLY="--context-only"
    fi
fi

# Run the test
echo ""
echo -e "${GREEN}Running alert pipeline test...${NC}"
echo ""

cd "$(dirname "$0")"

python test_alert_pipeline.py \
    --prometheus-url "$PROMETHEUS_URL" \
    --vllm-url "$VLLM_URL" \
    --namespace "$NAMESPACE" \
    --alert "$ALERT" \
    $CONTEXT_ONLY

# Cleanup port forwards if started
if [ ! -z "$PROM_PID" ]; then
    echo ""
    echo -e "${YELLOW}Cleaning up port forwards...${NC}"
    kill $PROM_PID 2>/dev/null || true
    kill $VLLM_PID 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}Test complete!${NC}"
