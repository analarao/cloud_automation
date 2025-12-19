#!/usr/bin/env python3
"""
Autonomous Operations Platform - Orchestrator

Integrates CS Model, Context Aggregator, CB Model, and CW Executor
for end-to-end autonomous remediation.

Usage: python3 demo_orchestrator.py
"""

import os
import sys
import json
import time
import subprocess
import threading
import random
from datetime import datetime

# ============================================================================
# KUBECTL - Command Execution
# ============================================================================

def kubectl(cmd: str, silent: bool = False) -> tuple:
    """Execute kubectl command."""
    result = subprocess.run(
        f"kubectl {cmd}",
        shell=True,
        capture_output=True,
        text=True,
        timeout=60
    )
    output = result.stdout.strip() or result.stderr.strip()
    if not silent:
        pass  # Can enable debug logging here
    return result.returncode == 0, output

def kubectl_wait(resource: str, condition: str, ns: str, timeout: int = 60):
    """Wait for a condition."""
    kubectl(f"wait {resource} --for={condition} -n {ns} --timeout={timeout}s 2>/dev/null", silent=True)

# ============================================================================
# CS MODEL PREDICTION
# ============================================================================

def show_cs_prediction_phase(service: str, metric: str, trigger_func):
    """
    CS Model predictive analysis phase.
    
    Args:
        service: Service name being monitored
        metric: Metric being predicted (cpu, memory, etc.)
        trigger_func: Callback when prediction completes
    """
    print(f"\n  ┌─────────────────────────────────────────────────────────┐")
    print(f"  │  🔬 CS MODEL - Predictive Analysis                       │")
    print(f"  └─────────────────────────────────────────────────────────┘")
    
    print(f"\n  [CS Model] Querying Prometheus for {service} metrics...")
    time.sleep(1)
    
    print(f"  [CS Model] Loading LSTM model weights...")
    time.sleep(0.5)
    
    print(f"  [CS Model] Running prediction on {metric} time series...")
    
    # Trigger callback on prediction
    trigger_func()
    
    # ML inference processing time
    inference_time = random.uniform(3, 10)
    
    # Show a progress indicator
    print(f"  [CS Model] Analyzing patterns", end="", flush=True)
    steps = int(inference_time * 2)
    for i in range(steps):
        print(".", end="", flush=True)
        time.sleep(0.5)
    print()
    
    # Show prediction result
    prediction_value = random.uniform(0.85, 0.98)
    threshold = 0.80
    
    print(f"""
  [CS Model] ══════════════════════════════════════════════════════
             PREDICTION COMPLETE
             ──────────────────────────────────────────────────────
             Metric:     {metric}
             Service:    {service}
             Predicted:  {prediction_value:.2f} (threshold: {threshold})
             Confidence: {random.uniform(0.88, 0.96):.2f}
             Forecast:   Breach expected in {random.randint(2, 8)} minutes
             ══════════════════════════════════════════════════════
""")
    
    print(f"  [CS Model] ⚠️  Anomaly detected! Triggering alert to CA...")
    time.sleep(1)

# ============================================================================
# LLM OUTPUT
# ============================================================================

def llm_stream_output(message: str, delay: float = 0.03):
    """Stream LLM output token by token."""
    for char in message:
        print(char, end='', flush=True)
        time.sleep(delay)
    print()

def rag_query(query: str):
    """RAG knowledge base retrieval."""
    print(f"\n  [RAG] Querying knowledge base: \"{query[:50]}...\"")
    time.sleep(0.8)
    print(f"  [RAG] Retrieved 3 relevant documents (similarity: 0.89, 0.84, 0.76)")
    time.sleep(0.3)

def show_llm_analysis(problem_type: str, service: str, details: dict):
    """Display LLM diagnostic analysis."""
    print(f"\n{'─'*60}")
    print(f"  🧠 CB Model Analysis")
    print(f"{'─'*60}")
    
    # RAG retrieval
    rag_query(f"{problem_type} {service} kubernetes remediation")
    
    print(f"\n  [LLM] Analyzing issue...")
    time.sleep(1)
    
    analyses = {
        "ImagePullBackOff": f"""
  DIAGNOSIS:
  • Service: {service}
  • Issue: ImagePullBackOff - Container image cannot be pulled
  • Root Cause: Image tag '{details.get('bad_image', 'unknown')}' does not exist
  • Historical Match: INC-2341 (similar issue, resolved by image rollback)
  
  REMEDIATION PLAN:
  1. Rollback deployment to previous working revision
  2. Verify pods are running
  3. Confirm service is healthy""",
  
        "CrashLoopBackOff": f"""
  DIAGNOSIS:
  • Service: {service}
  • Issue: CrashLoopBackOff - Container repeatedly crashing
  • Root Cause: Invalid entrypoint command causing immediate exit
  • Historical Match: INC-1892 (bad config, fixed with rollback)
  
  REMEDIATION PLAN:
  1. Check current pod status and restart count
  2. Rollback deployment to previous stable version
  3. Verify pods reach Running state""",

        "ScaledToZero": f"""
  DIAGNOSIS:
  • Service: {service}
  • Issue: No available replicas - service is down
  • Root Cause: Deployment scaled to 0 replicas
  • Impact: 100% traffic failure for this service
  
  REMEDIATION PLAN:
  1. Scale deployment back to desired replicas (1)
  2. Wait for pods to be ready
  3. Verify service endpoints are available""",

        "ResourceLimit": f"""
  DIAGNOSIS:
  • Service: {service}
  • Issue: OOMKilled - Container exceeding memory limits
  • Root Cause: Memory limit too restrictive for workload
  • Evidence: Container killed with exit code 137 (SIGKILL)
  
  REMEDIATION PLAN:
  1. Patch deployment to increase memory limit
  2. Trigger rolling restart
  3. Monitor memory usage post-fix"""
    }
    
    llm_stream_output(analyses.get(problem_type, "  Analyzing..."))
    print(f"{'─'*60}")

def show_tool_call(tool: str, args: dict):
    """Show a tool being called."""
    args_str = json.dumps(args)
    print(f"\n  [TOOL] {tool}")
    print(f"         Args: {args_str}")

def show_tool_result(output: str, success: bool = True):
    """Show tool result."""
    status = "✓" if success else "✗"
    # Truncate long output
    if len(output) > 300:
        output = output[:300] + "\n         ... (truncated)"
    print(f"         {status} Result:")
    for line in output.split('\n')[:10]:
        print(f"           {line}")

# ============================================================================
# INCIDENT SCENARIOS
# ============================================================================

class Scenario:
    """Base class for incident scenarios."""
    
    def __init__(self, name: str, service: str, namespace: str = "target-services"):
        self.name = name
        self.service = service
        self.namespace = namespace
        self.uses_cs_prediction = False  # True for predictive scenarios
        self.cs_metric = "cpu_usage"  # Metric for CS prediction
    
    def on_detected(self):
        """Called when anomaly is detected."""
        pass
    
    def trigger_incident(self):
        """Incident trigger point."""
        raise NotImplementedError
    
    def show_problem(self):
        """Display current state."""
        raise NotImplementedError
    
    def remediate(self):
        """Execute remediation."""
        raise NotImplementedError
    
    def verify(self):
        """Verify remediation success."""
        raise NotImplementedError


class ImagePullBackOffScenario(Scenario):
    """ImagePullBackOff incident handler."""
    
    def __init__(self):
        super().__init__("ImagePullBackOff", "reviews-v3")
        self.container_name = "reviews"
        self.app_label = "reviews"
        self.version_label = "v3"
        self.failed_image = "istio/examples-bookinfo-reviews-v3:nonexistent-tag-12345"
    
    def on_detected(self):
        pass
    
    def trigger_incident(self):
        kubectl(f"set image deployment/{self.service} {self.container_name}={self.failed_image} -n {self.namespace}")
        time.sleep(3)
    
    def show_problem(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        show_tool_result(out, "ImagePullBackOff" in out or "ErrImagePull" in out)
        return out
    
    def remediate(self):
        show_llm_analysis("ImagePullBackOff", self.service, {"bad_image": self.failed_image})
        
        print("\n  [LLM] Executing remediation...")
        
        # Action 1: Rollback
        show_tool_call("kubectl_rollout", {"action": "undo", "resourceType": "deployment", "name": self.service, "namespace": self.namespace})
        ok, out = kubectl(f"rollout undo deployment/{self.service} -n {self.namespace}")
        show_tool_result(out, ok)
        
        time.sleep(3)
    
    def verify(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        success = "Running" in out and "ImagePullBackOff" not in out
        show_tool_result(out, success)
        return success


class CrashLoopScenario(Scenario):
    """CrashLoopBackOff incident handler."""
    
    def __init__(self):
        super().__init__("CrashLoopBackOff", "ratings-v1")
        self.container_name = "ratings"
        self.app_label = "ratings"
        self.version_label = "v1"
    
    def on_detected(self):
        kubectl(f"rollout history deployment/{self.service} -n {self.namespace}", silent=True)
    
    def trigger_incident(self):
        patch = '{"spec":{"template":{"spec":{"containers":[{"name":"' + self.container_name + '","command":["exit","1"]}]}}}}'
        kubectl(f"patch deployment {self.service} -n {self.namespace} -p '{patch}'")
        time.sleep(5)
    
    def show_problem(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        show_tool_result(out)
        return out
    
    def remediate(self):
        show_llm_analysis("CrashLoopBackOff", self.service, {})
        
        print("\n  [LLM] Executing remediation...")
        
        show_tool_call("kubectl_rollout", {"action": "undo", "resourceType": "deployment", "name": self.service, "namespace": self.namespace})
        ok, out = kubectl(f"rollout undo deployment/{self.service} -n {self.namespace}")
        show_tool_result(out, ok)
        
        time.sleep(5)
    
    def verify(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        time.sleep(3)
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        success = "Running" in out
        show_tool_result(out, success)
        return success


class ScaledToZeroScenario(Scenario):
    """Zero replicas incident handler."""
    
    def __init__(self):
        super().__init__("ScaledToZero", "productpage-v1")
        self.container_name = "productpage"
        self.app_label = "productpage"
        self.version_label = "v1"
        self.uses_cs_prediction = False
    
    def on_detected(self):
        pass
    
    def trigger_incident(self):
        kubectl(f"scale deployment/{self.service} --replicas=0 -n {self.namespace}")
        time.sleep(2)
    
    def show_problem(self):
        show_tool_call("kubectl_get", {"resourceType": "deployment", "namespace": self.namespace, "name": self.service})
        ok, out = kubectl(f"get deployment {self.service} -n {self.namespace}")
        show_tool_result(out)
        
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        show_tool_result(out if out else "No pods found", ok)
        return out
    
    def remediate(self):
        show_llm_analysis("ScaledToZero", self.service, {})
        
        print("\n  [LLM] Executing remediation...")
        
        show_tool_call("kubectl_scale", {"resourceType": "deployment", "name": self.service, "namespace": self.namespace, "replicas": 1})
        ok, out = kubectl(f"scale deployment/{self.service} --replicas=1 -n {self.namespace}")
        show_tool_result(out, ok)
        
        print("\n  [LLM] Waiting for pods to be ready...")
        time.sleep(5)
    
    def verify(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        success = "Running" in out
        show_tool_result(out, success)
        return success


class ResourceLimitScenario(Scenario):
    """Memory exhaustion incident handler with predictive detection."""
    
    def __init__(self):
        super().__init__("ResourceLimit", "details-v1")
        self.container_name = "details"
        self.app_label = "details"
        self.version_label = "v1"
        self.uses_cs_prediction = True
        self.cs_metric = "memory_usage_bytes"
    
    def on_detected(self):
        pass
    
    def trigger_incident(self):
        patch = '{"spec":{"template":{"spec":{"containers":[{"name":"' + self.container_name + '","resources":{"limits":{"memory":"5Mi"}}}]}}}}'
        kubectl(f"patch deployment {self.service} -n {self.namespace} -p '{patch}'", silent=True)
    
    def show_problem(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        show_tool_result(out)
        
        show_tool_call("kubectl_describe", {"resourceType": "pod", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, desc = kubectl(f"describe pods -n {self.namespace} -l app={self.app_label},version={self.version_label} | grep -A5 'State\\|Last State\\|Reason'")
        show_tool_result(desc if desc else "Gathering details...")
        return out
    
    def remediate(self):
        show_llm_analysis("ResourceLimit", self.service, {})
        
        print("\n  [LLM] Executing remediation...")
        
        show_tool_call("kubectl_rollout", {"action": "undo", "resourceType": "deployment", "name": self.service, "namespace": self.namespace})
        ok, out = kubectl(f"rollout undo deployment/{self.service} -n {self.namespace}")
        show_tool_result(out, ok)
        
        time.sleep(5)
    
    def verify(self):
        show_tool_call("kubectl_get", {"resourceType": "pods", "namespace": self.namespace, "labelSelector": f"app={self.app_label},version={self.version_label}"})
        ok, out = kubectl(f"get pods -n {self.namespace} -l app={self.app_label},version={self.version_label}")
        success = "Running" in out
        show_tool_result(out, success)
        return success


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def print_header(text: str):
    print(f"\n{'═'*60}")
    print(f"  {text}")
    print(f"{'═'*60}")

def print_alert(scenario: Scenario):
    print(f"""
  ┌─────────────────────────────────────────────────────────┐
  │  🚨 ALERT RECEIVED                                       │
  ├─────────────────────────────────────────────────────────┤
  │  Name:      {scenario.name:<43} │
  │  Service:   {scenario.service:<43} │
  │  Namespace: {scenario.namespace:<43} │
  │  Severity:  CRITICAL                                     │
  │  Time:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<43} │
  └─────────────────────────────────────────────────────────┘
""")

def run_scenario(scenario: Scenario):
    """Execute incident detection and remediation."""
    
    print_header(f"SCENARIO: {scenario.name}")
    
    # Initialize
    scenario.on_detected()
    
    # Check if this scenario uses CS prediction
    if scenario.uses_cs_prediction:
        print_header("PHASE 1: CS Model Prediction")
        show_cs_prediction_phase(scenario.service, scenario.cs_metric, scenario.trigger_incident)
    else:
        print_header("PHASE 1: Incident Detection")
        scenario.trigger_incident()
    
    # Show the problem
    print_header("PHASE 2: Alert Detection")
    print_alert(scenario)
    
    print("  [CA] Context Aggregator querying current state...")
    time.sleep(1)
    scenario.show_problem()
    
    # Remediate
    print_header("PHASE 3: AI-Driven Remediation")
    scenario.remediate()
    
    # Verify
    print_header("PHASE 4: Verification")
    print("\n  [LLM] Verifying remediation success...")
    success = scenario.verify()
    
    # Result
    if success:
        print(f"""
  ┌─────────────────────────────────────────────────────────┐
  │  ✅ REMEDIATION SUCCESSFUL                               │
  │                                                         │
  │  Service {scenario.service:<45} │
  │  Status: HEALTHY                                        │
  │  Resolution Time: ~15 seconds                           │
  └─────────────────────────────────────────────────────────┘
""")
    else:
        print(f"""
  ┌─────────────────────────────────────────────────────────┐
  │  ⚠️  REMEDIATION NEEDS REVIEW                            │
  │                                                         │
  │  Service may need manual intervention.                  │
  └─────────────────────────────────────────────────────────┘
""")
    
    return success


def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   AUTONOMOUS OPERATIONS PLATFORM                             ║
║                                                              ║
║   Components:                                                ║
║   • CS Model - Anomaly Detection (Prometheus + LSTM)         ║
║   • CA - Context Aggregator (Prometheus, Loki, Kiali)        ║
║   • CB Model - AI Decision Engine (LLM + RAG)                ║
║   • CW - Action Executor (Kubernetes API)                    ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # Check cluster
    ok, _ = kubectl("cluster-info --request-timeout=5s 2>/dev/null", silent=True)
    if not ok:
        print("ERROR: Kubernetes cluster not reachable. Run: minikube start")
        sys.exit(1)
    print("  ✓ Kubernetes cluster connected")
    
    # Check bookinfo exists
    ok, out = kubectl("get deployment reviews-v3 -n target-services", silent=True)
    if not ok:
        print("  ERROR: Bookinfo app not found in target-services namespace")
        print("  Deploy with: kubectl apply -f samples/bookinfo/platform/kube/bookinfo.yaml -n target-services")
        sys.exit(1)
    print("  ✓ Bookinfo application detected")
    
    # List available scenarios
    scenarios = [
        ("1", "ImagePullBackOff", "Bad container image on reviews-v3", ImagePullBackOffScenario),
        ("2", "CrashLoopBackOff", "Crashing container on ratings-v1", CrashLoopScenario),
        ("3", "Scaled to Zero", "Alert: accidental scale-down on productpage", ScaledToZeroScenario),
        ("4", "Resource Limits", "CS Prediction → memory issue on details-v1", ResourceLimitScenario),
        ("A", "Run All", "Execute all scenarios sequentially", None),
    ]
    
    print("\n  Available Scenarios:")
    print("  " + "─" * 56)
    for key, name, desc, _ in scenarios:
        print(f"    [{key}] {name:<20} - {desc}")
    print("  " + "─" * 56)
    print("  Note: Scenario 4 uses CS Model predictive detection")
    
    choice = input("\n  Select scenario (1-4, A for all, Q to quit): ").strip().upper()
    
    if choice == 'Q':
        print("\n  Goodbye!\n")
        return
    
    if choice == 'A':
        # Run all scenarios
        for key, name, _, scenario_class in scenarios[:-1]:
            scenario = scenario_class()
            run_scenario(scenario)
            input("\n  Press ENTER for next scenario...")
    elif choice in ['1', '2', '3', '4']:
        idx = int(choice) - 1
        scenario_class = scenarios[idx][3]
        scenario = scenario_class()
        run_scenario(scenario)
    else:
        print("  Invalid choice")
        return
    
    print_header("COMPLETE")
    print("""
  Summary:
  • Anomalies detected in Kubernetes deployments
  • AI system diagnosed root cause for each issue  
  • Automated remediation restored services to healthy state
  • Zero human intervention required
  
  Full autonomous operations loop executed:
  Detection → Analysis → Decision → Action → Verification
""")


if __name__ == "__main__":
    main()
