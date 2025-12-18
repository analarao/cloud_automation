#!/usr/bin/env python3
"""
Advanced Remediation Test Scenarios
====================================

This module provides test scenarios to verify the enhanced MCP capabilities:
1. Pod exec (shell commands inside containers)
2. Scale operations
3. Delete/restart pods
4. Network diagnostics
5. Port issues diagnosis
6. Ingress troubleshooting

Usage:
    # Run all tests
    python test_advanced_remediation.py
    
    # Run specific test
    python test_advanced_remediation.py --test exec
    python test_advanced_remediation.py --test scale
    python test_advanced_remediation.py --test network
    python test_advanced_remediation.py --test ingress
"""

import asyncio
import os
import sys
import json
import argparse
from typing import Dict, Any, List

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator import CBOrchestrator, Alert


# =============================================================================
# Test Scenarios
# =============================================================================

class TestScenarios:
    """Collection of test scenarios for advanced remediation."""
    
    @staticmethod
    def high_cpu_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: High CPU usage on a deployment."""
        return Alert(
            alert_name="HighCPUUsage",
            severity="warning",
            namespace=namespace,
            deployment_name="web-service",
            message="CPU usage has exceeded 90% for more than 5 minutes",
            value=92.5,
            threshold=90.0,
            labels={"app": "web-service", "tier": "frontend"}
        )
    
    @staticmethod
    def pod_crashloop_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Pod in CrashLoopBackOff state."""
        return Alert(
            alert_name="PodCrashLooping",
            severity="critical",
            namespace=namespace,
            pod_name="api-service-7d8f9b6c4d-x2k9p",
            deployment_name="api-service",
            message="Pod has restarted 5 times in the last 10 minutes with CrashLoopBackOff",
            labels={"app": "api-service", "tier": "backend"}
        )
    
    @staticmethod
    def service_unreachable_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Service endpoint not responding."""
        return Alert(
            alert_name="ServiceUnreachable",
            severity="critical",
            namespace=namespace,
            deployment_name="backend-service",
            message="Health check failed: Service backend-service:8080/health returned 503",
            annotations={
                "description": "The backend service health endpoint is not responding correctly",
                "runbook_url": "https://wiki.example.com/runbooks/service-unreachable"
            }
        )
    
    @staticmethod
    def network_connectivity_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Network connectivity issues between services."""
        return Alert(
            alert_name="NetworkConnectivityIssue",
            severity="critical",
            namespace=namespace,
            message="""Network connectivity test failed:
- Source: web-service pod
- Target: api-service:8080
- Error: Connection timed out after 30s
This may indicate a NetworkPolicy blocking traffic or service misconfiguration.""",
            labels={"source": "web-service", "target": "api-service"}
        )
    
    @staticmethod
    def ingress_misconfigured_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Ingress returning 502 errors."""
        return Alert(
            alert_name="IngressBadGateway",
            severity="critical",
            namespace=namespace,
            message="""Ingress 'main-ingress' returning 502 Bad Gateway errors:
- URL: https://app.example.com/api
- Backend: api-service:8080
- Rate: 45% of requests failing with 502

Possible causes:
1. Backend pods not ready
2. Service selector mismatch
3. Port mismatch between Ingress and Service
4. Health check misconfiguration""",
            annotations={"ingress": "main-ingress", "host": "app.example.com"}
        )
    
    @staticmethod
    def port_blocked_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Port appears to be blocked by network policy."""
        return Alert(
            alert_name="PortBlockedByPolicy",
            severity="warning",
            namespace=namespace,
            message="""Suspected NetworkPolicy blocking traffic:
- Affected port: 5432 (PostgreSQL)
- Source namespace: target-services
- Target: database-service.databases:5432
- Symptoms: Connection refused despite service being healthy

Investigate NetworkPolicies and ensure proper ingress/egress rules.""",
            labels={"port": "5432", "protocol": "TCP", "service": "database-service"}
        )
    
    @staticmethod
    def memory_pressure_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Memory pressure requiring scale up."""
        return Alert(
            alert_name="MemoryPressure",
            severity="warning",
            namespace=namespace,
            deployment_name="memory-intensive-service",
            message="Memory usage at 95%, approaching OOM threshold. Consider scaling out.",
            value=95.2,
            threshold=90.0
        )
    
    @staticmethod
    def exec_test_alert(namespace: str = "target-services") -> Alert:
        """Test scenario: Requires kubectl exec to diagnose."""
        return Alert(
            alert_name="ApplicationError",
            severity="warning",
            namespace=namespace,
            pod_name="debug-pod",
            message="""Application reporting internal errors. Need to:
1. Check application config file inside container
2. Verify environment variables
3. Test internal connectivity to dependencies
4. Check disk space and file permissions

Use kubectl exec to run diagnostic commands inside the container."""
        )


# =============================================================================
# Test Runner
# =============================================================================

def run_test(
    test_name: str,
    alert: Alert,
    vllm_url: str,
    max_iterations: int = 10,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Run a test scenario and return results.
    
    Args:
        test_name: Name of the test
        alert: Alert to process
        vllm_url: URL of the vLLM server
        max_iterations: Maximum LLM iterations
        verbose: Print verbose output
        
    Returns:
        Test results dictionary
    """
    print(f"\n{'='*60}")
    print(f"TEST: {test_name}")
    print(f"{'='*60}")
    print(f"Alert: {alert.alert_name}")
    print(f"Severity: {alert.severity}")
    print(f"Message: {alert.message[:100]}...")
    print(f"{'='*60}\n")
    
    orchestrator = CBOrchestrator(
        vllm_base_url=vllm_url,
        target_namespace=alert.namespace,
        max_iterations=max_iterations
    )
    
    result = orchestrator.process_alert(alert)
    
    test_result = {
        "test_name": test_name,
        "alert_name": alert.alert_name,
        "success": result.success,
        "iterations": result.iterations,
        "actions_count": len(result.actions_taken),
        "final_response": result.final_response[:500],
        "error": result.error
    }
    
    if verbose:
        print(f"\n{'='*60}")
        print("TEST RESULT")
        print(f"{'='*60}")
        print(f"Success: {result.success}")
        print(f"Iterations: {result.iterations}")
        print(f"Actions Taken: {len(result.actions_taken)}")
        
        if result.actions_taken:
            print("\nActions Summary:")
            for i, action in enumerate(result.actions_taken, 1):
                tool = action['tool']
                args_preview = json.dumps(action['arguments'])[:50]
                success = "✓" if action.get('result', {}).get('success') else "✗"
                print(f"  {i}. {success} {tool}({args_preview}...)")
        
        print(f"\nFinal Response:\n{result.final_response}")
        
        if result.error:
            print(f"\nError: {result.error}")
    
    return test_result


def main():
    """Main entry point for test scenarios."""
    parser = argparse.ArgumentParser(description="Advanced Remediation Test Scenarios")
    parser.add_argument(
        "--test",
        choices=["all", "cpu", "crashloop", "service", "network", "ingress", "port", "memory", "exec"],
        default="all",
        help="Which test scenario to run"
    )
    parser.add_argument(
        "--namespace",
        default="target-services",
        help="Target namespace for tests"
    )
    parser.add_argument(
        "--vllm-url",
        default=os.getenv("CB_MODEL_OPENAI_API_URL", "http://localhost:8000/v1"),
        help="vLLM server URL"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Maximum LLM iterations per test"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity"
    )
    
    args = parser.parse_args()
    
    # Define test scenarios
    scenarios = {
        "cpu": ("High CPU Alert", TestScenarios.high_cpu_alert(args.namespace)),
        "crashloop": ("CrashLoopBackOff Alert", TestScenarios.pod_crashloop_alert(args.namespace)),
        "service": ("Service Unreachable Alert", TestScenarios.service_unreachable_alert(args.namespace)),
        "network": ("Network Connectivity Alert", TestScenarios.network_connectivity_alert(args.namespace)),
        "ingress": ("Ingress Misconfigured Alert", TestScenarios.ingress_misconfigured_alert(args.namespace)),
        "port": ("Port Blocked Alert", TestScenarios.port_blocked_alert(args.namespace)),
        "memory": ("Memory Pressure Alert", TestScenarios.memory_pressure_alert(args.namespace)),
        "exec": ("Exec Diagnostic Alert", TestScenarios.exec_test_alert(args.namespace)),
    }
    
    # Run selected tests
    if args.test == "all":
        tests_to_run = scenarios.keys()
    else:
        tests_to_run = [args.test]
    
    results = []
    for test_key in tests_to_run:
        test_name, alert = scenarios[test_key]
        try:
            result = run_test(
                test_name=test_name,
                alert=alert,
                vllm_url=args.vllm_url,
                max_iterations=args.max_iterations,
                verbose=not args.quiet
            )
            results.append(result)
        except Exception as e:
            print(f"\n❌ Test '{test_name}' failed with exception: {e}")
            results.append({
                "test_name": test_name,
                "success": False,
                "error": str(e)
            })
    
    # Summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print(f"{'='*60}")
    
    passed = sum(1 for r in results if r.get("success"))
    failed = len(results) - passed
    
    for r in results:
        status = "✓ PASS" if r.get("success") else "✗ FAIL"
        print(f"{status}: {r['test_name']}")
    
    print(f"\nTotal: {passed} passed, {failed} failed out of {len(results)} tests")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
