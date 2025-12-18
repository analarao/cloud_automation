#!/bin/bash
# =============================================================================
# CB Model Demo Runner
# =============================================================================
# This script runs the full end-to-end demo:
# 1. Apply demo alert rules to Prometheus
# 2. Start the alert pipeline locally
# 3. Trigger a demo issue
# 4. Watch the LLM remediate
#
# Prerequisites:
# - kubectl configured for your cluster
# - Port-forwards running: Prometheus (9090), AlertManager (9093), vLLM (8000)
# - Python 3.9+ with requirements installed
#
# Usage:
#   ./run_demo.sh                    # Run interactive demo
#   ./run_demo.sh --issue crash-loop # Run specific issue
#   ./run_demo.sh --setup-only       # Only setup, don't trigger
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
VLLM_URL="${VLLM_URL:-http://localhost:8000/v1}"
ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://localhost:9093}"
NAMESPACE="${NAMESPACE:-target-services}"
PIPELINE_PORT="${PIPELINE_PORT:-9095}"

echo -e "${BLUE}"
echo "============================================================"
echo "   CB MODEL - AUTONOMOUS REMEDIATION DEMO"
echo "============================================================"
echo -e "${NC}"

# Function to check prerequisites
check_prerequisites() {
    echo -e "${YELLOW}Checking prerequisites...${NC}"
    
    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        echo -e "${RED}✗ kubectl not found${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ kubectl found${NC}"
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}✗ python3 not found${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ python3 found${NC}"
    
    # Check Prometheus
    if curl -s "${PROMETHEUS_URL}/-/healthy" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Prometheus reachable at ${PROMETHEUS_URL}${NC}"
    else
        echo -e "${RED}✗ Prometheus not reachable at ${PROMETHEUS_URL}${NC}"
        echo "  Run: kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090"
        exit 1
    fi
    
    # Check vLLM
    if curl -s "${VLLM_URL}/models" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ vLLM reachable at ${VLLM_URL}${NC}"
    else
        echo -e "${YELLOW}⚠ vLLM not reachable at ${VLLM_URL} - LLM calls will fail${NC}"
    fi
    
    echo ""
}

# Function to apply demo alerts
apply_demo_alerts() {
    echo -e "${YELLOW}Applying demo alert rules...${NC}"
    
    if kubectl apply -f ../prometheus/demo-alerts.yaml -n monitoring; then
        echo -e "${GREEN}✓ Demo alerts applied${NC}"
    else
        echo -e "${RED}✗ Failed to apply demo alerts${NC}"
        exit 1
    fi
    
    echo ""
}

# Function to start the alert pipeline
start_pipeline() {
    echo -e "${YELLOW}Starting alert pipeline...${NC}"
    echo "  Prometheus: ${PROMETHEUS_URL}"
    echo "  vLLM: ${VLLM_URL}"
    echo "  Port: ${PIPELINE_PORT}"
    echo ""
    
    # Start pipeline in background
    python3 alert_pipeline.py \
        --prometheus-url "${PROMETHEUS_URL}" \
        --vllm-url "${VLLM_URL}" \
        --namespace "${NAMESPACE}" \
        --port "${PIPELINE_PORT}" &
    
    PIPELINE_PID=$!
    echo "  Pipeline PID: ${PIPELINE_PID}"
    
    # Wait for pipeline to start
    sleep 3
    
    if curl -s "http://localhost:${PIPELINE_PORT}/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Pipeline started successfully${NC}"
    else
        echo -e "${RED}✗ Pipeline failed to start${NC}"
        exit 1
    fi
    
    echo ""
}

# Function to trigger an issue
trigger_issue() {
    local issue="$1"
    
    echo -e "${YELLOW}Triggering demo issue: ${issue}${NC}"
    echo ""
    
    python3 demo_trigger_issues.py --issue "${issue}" --namespace "${NAMESPACE}"
}

# Function to show menu
show_menu() {
    echo -e "${BLUE}Select a demo issue to trigger:${NC}"
    echo ""
    echo "  1) crash-loop      - Pod keeps crashing (CrashLoopBackOff)"
    echo "  2) service-down    - Scale deployment to 0 (service unavailable)"
    echo "  3) memory-stress   - Create memory pressure"
    echo "  4) bad-config      - Invalid environment variable"
    echo "  5) replica-shortage - More replicas than can be scheduled"
    echo ""
    echo "  c) Cleanup all issues"
    echo "  q) Quit"
    echo ""
    read -p "Enter choice: " choice
    
    case $choice in
        1) trigger_issue "crash-loop" ;;
        2) trigger_issue "service-down" ;;
        3) trigger_issue "memory-stress" ;;
        4) trigger_issue "bad-config" ;;
        5) trigger_issue "replica-shortage" ;;
        c) python3 demo_trigger_issues.py --cleanup-all --namespace "${NAMESPACE}" ;;
        q) cleanup_and_exit ;;
        *) echo "Invalid choice" ;;
    esac
}

# Function to cleanup on exit
cleanup_and_exit() {
    echo ""
    echo -e "${YELLOW}Cleaning up...${NC}"
    
    if [ ! -z "$PIPELINE_PID" ]; then
        kill $PIPELINE_PID 2>/dev/null || true
    fi
    
    echo -e "${GREEN}Done.${NC}"
    exit 0
}

# Trap Ctrl+C
trap cleanup_and_exit INT

# Parse arguments
SETUP_ONLY=false
SPECIFIC_ISSUE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --setup-only)
            SETUP_ONLY=true
            shift
            ;;
        --issue)
            SPECIFIC_ISSUE="$2"
            shift 2
            ;;
        --prometheus-url)
            PROMETHEUS_URL="$2"
            shift 2
            ;;
        --vllm-url)
            VLLM_URL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Main execution
check_prerequisites
apply_demo_alerts
start_pipeline

if [ "$SETUP_ONLY" = true ]; then
    echo -e "${GREEN}Setup complete. Pipeline running on port ${PIPELINE_PORT}${NC}"
    echo "Press Ctrl+C to stop"
    wait $PIPELINE_PID
    exit 0
fi

if [ ! -z "$SPECIFIC_ISSUE" ]; then
    trigger_issue "$SPECIFIC_ISSUE"
    echo ""
    echo -e "${BLUE}Watching pipeline output (Ctrl+C to stop)...${NC}"
    wait $PIPELINE_PID
    exit 0
fi

# Interactive mode
while true; do
    show_menu
    echo ""
    read -p "Press Enter to continue..."
done
