#!/usr/bin/env python3
"""
Demo Issue Trigger - Creates Real Issues in Kubernetes for CB Model Demo
=========================================================================

This script creates REAL problems in the Kubernetes cluster that will:
1. Trigger Prometheus alerts
2. AlertManager sends to CB Model webhook
3. CB Model aggregates context
4. LLM reasons and remediates

Usage:
    # Trigger a crash loop issue
    python demo_trigger_issues.py --issue crash-loop
    
    # Trigger memory pressure
    python demo_trigger_issues.py --issue memory-pressure
    
    # Trigger service down
    python demo_trigger_issues.py --issue service-down
    
    # List available issues
    python demo_trigger_issues.py --list
    
    # Clean up all demo issues
    python demo_trigger_issues.py --cleanup

WARNING: This creates REAL issues in your cluster. Use with caution!
"""

import os
import sys
import time
import json
import argparse
import subprocess
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class DemoIssue:
    """Definition of a demo issue to trigger."""
    name: str
    description: str
    trigger_commands: List[str]
    cleanup_commands: List[str]
    expected_alert: str
    estimated_trigger_time: int  # seconds


class DemoIssueTrigger:
    """Triggers real issues in Kubernetes for demo purposes."""
    
    def __init__(self, namespace: str = "target-services", dry_run: bool = False):
        self.namespace = namespace
        self.dry_run = dry_run
        self.issues = self._define_issues()
    
    def _define_issues(self) -> Dict[str, DemoIssue]:
        """Define available demo issues."""
        return {
            # =====================================================================
            # Issue 1: Crash Loop - Bad command causes container to keep restarting
            # =====================================================================
            "crash-loop": DemoIssue(
                name="Crash Loop (Ratings Service)",
                description="""
                Patches the ratings-v1 deployment with a bad command that causes 
                the container to exit immediately, creating a CrashLoopBackOff.
                The LLM should detect the bad command and restore the original config.
                """,
                trigger_commands=[
                    # Save original deployment spec
                    f'kubectl get deployment ratings-v1 -n {self.namespace} -o yaml > /tmp/ratings-v1-backup.yaml',
                    # Patch with bad command that exits immediately
                    f'''kubectl patch deployment ratings-v1 -n {self.namespace} --type='json' -p='[
                        {{"op": "add", "path": "/spec/template/spec/containers/0/command", "value": ["/bin/sh", "-c", "echo Simulated crash && exit 1"]}}
                    ]' ''',
                ],
                cleanup_commands=[
                    # Remove the command override to restore original behavior
                    f'''kubectl patch deployment ratings-v1 -n {self.namespace} --type='json' -p='[
                        {{"op": "remove", "path": "/spec/template/spec/containers/0/command"}}
                    ]' ''',
                ],
                expected_alert="DemoPodCrashLooping",
                estimated_trigger_time=60,
            ),
            
            # =====================================================================
            # Issue 2: Scale to Zero - Service becomes unavailable
            # =====================================================================
            "service-down": DemoIssue(
                name="Service Down (Details Service)",
                description="""
                Scales the details-v1 deployment to 0 replicas, making the service
                unavailable. This triggers endpoint down alerts.
                The LLM should scale the deployment back up.
                """,
                trigger_commands=[
                    # Save current replica count
                    f'kubectl get deployment details-v1 -n {self.namespace} -o jsonpath="{{.spec.replicas}}" > /tmp/details-v1-replicas.txt',
                    # Scale to zero
                    f'kubectl scale deployment details-v1 -n {self.namespace} --replicas=0',
                ],
                cleanup_commands=[
                    # Scale back to 1
                    f'kubectl scale deployment details-v1 -n {self.namespace} --replicas=1',
                ],
                expected_alert="DemoServiceEndpointsDown",
                estimated_trigger_time=45,
            ),
            
            # =====================================================================
            # Issue 3: Resource Exhaustion - Memory stress
            # =====================================================================
            "memory-stress": DemoIssue(
                name="Memory Stress (Productpage)",
                description="""
                Deploys a sidecar container that consumes memory, pushing the pod
                towards its memory limit. This triggers memory pressure alerts.
                The LLM should identify the rogue container and remove it.
                """,
                trigger_commands=[
                    # Create a memory stress job that runs in the same namespace
                    f'''kubectl run memory-stress -n {self.namespace} --image=polinux/stress --restart=Never -- stress --vm 1 --vm-bytes 256M --timeout 300s''',
                ],
                cleanup_commands=[
                    # Delete the stress pod
                    f'kubectl delete pod memory-stress -n {self.namespace} --ignore-not-found=true',
                ],
                expected_alert="DemoHighMemoryUsage",
                estimated_trigger_time=30,
            ),
            
            # =====================================================================
            # Issue 4: Bad Configuration - Environment variable breaks app
            # =====================================================================
            "bad-config": DemoIssue(
                name="Bad Configuration (Reviews Service)",
                description="""
                Patches reviews-v1 with an invalid environment variable that
                causes the application to fail on startup.
                The LLM should identify the bad config and restore it.
                """,
                trigger_commands=[
                    # Add a bad environment variable
                    f'''kubectl set env deployment/reviews-v1 -n {self.namespace} RATINGS_SERVICE_HOST=invalid-host-that-does-not-exist''',
                ],
                cleanup_commands=[
                    # Remove the bad environment variable
                    f'''kubectl set env deployment/reviews-v1 -n {self.namespace} RATINGS_SERVICE_HOST-''',
                ],
                expected_alert="DemoPodCrashLooping",
                estimated_trigger_time=90,
            ),
            
            # =====================================================================
            # Issue 5: Replica Shortage - Deployment partially unavailable
            # =====================================================================
            "replica-shortage": DemoIssue(
                name="Replica Shortage (Reviews Service)",
                description="""
                Increases desired replicas but with resource requests that can't
                be satisfied, causing pending pods.
                The LLM should adjust resource requests or scale appropriately.
                """,
                trigger_commands=[
                    # Scale up reviews-v1 to more replicas than can be scheduled
                    f'kubectl scale deployment reviews-v1 -n {self.namespace} --replicas=5',
                    # Add high resource request that prevents scheduling
                    f'''kubectl patch deployment reviews-v1 -n {self.namespace} --type='json' -p='[
                        {{"op": "replace", "path": "/spec/template/spec/containers/0/resources/requests/memory", "value": "2Gi"}}
                    ]' ''',
                ],
                cleanup_commands=[
                    # Scale back down
                    f'kubectl scale deployment reviews-v1 -n {self.namespace} --replicas=1',
                    # Remove resource override
                    f'''kubectl patch deployment reviews-v1 -n {self.namespace} --type='json' -p='[
                        {{"op": "replace", "path": "/spec/template/spec/containers/0/resources/requests/memory", "value": "64Mi"}}
                    ]' ''',
                ],
                expected_alert="DemoDeploymentReplicasMismatch",
                estimated_trigger_time=120,
            ),
        }
    
    def run_command(self, cmd: str) -> tuple:
        """Run a shell command and return (success, output)."""
        print(f"  $ {cmd}")
        
        if self.dry_run:
            print("    [DRY RUN - not executed]")
            return True, ""
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                print(f"    ⚠ Warning: {result.stderr.strip()}")
                return False, result.stderr
            print(f"    ✓ Success")
            return True, result.stdout
        except subprocess.TimeoutExpired:
            print(f"    ✗ Command timed out")
            return False, "timeout"
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return False, str(e)
    
    def trigger_issue(self, issue_name: str) -> bool:
        """Trigger a specific demo issue."""
        if issue_name not in self.issues:
            print(f"✗ Unknown issue: {issue_name}")
            print(f"  Available: {', '.join(self.issues.keys())}")
            return False
        
        issue = self.issues[issue_name]
        
        print("\n" + "=" * 70)
        print(f"TRIGGERING DEMO ISSUE: {issue.name}")
        print("=" * 70)
        print(f"\nDescription:{issue.description}")
        print(f"\nExpected Alert: {issue.expected_alert}")
        print(f"Estimated time to trigger: {issue.estimated_trigger_time}s")
        print("\n" + "-" * 70)
        print("Executing commands:")
        print("-" * 70)
        
        all_success = True
        for cmd in issue.trigger_commands:
            success, _ = self.run_command(cmd)
            if not success:
                all_success = False
        
        if all_success:
            print("\n" + "=" * 70)
            print(f"✓ Issue triggered successfully!")
            print(f"  Watch for alert: {issue.expected_alert}")
            print(f"  Expected in ~{issue.estimated_trigger_time} seconds")
            print("=" * 70)
            
            print("\nTo monitor alerts:")
            print("  kubectl get prometheusrules -n monitoring")
            print("  # Check AlertManager: http://localhost:9093/#/alerts")
            print("  # Check Prometheus: http://localhost:9090/alerts")
            
            print(f"\nTo cleanup this issue:")
            print(f"  python demo_trigger_issues.py --cleanup --issue {issue_name}")
        else:
            print("\n⚠ Some commands failed - issue may be partially triggered")
        
        return all_success
    
    def cleanup_issue(self, issue_name: str) -> bool:
        """Cleanup a specific demo issue."""
        if issue_name not in self.issues:
            print(f"✗ Unknown issue: {issue_name}")
            return False
        
        issue = self.issues[issue_name]
        
        print("\n" + "=" * 70)
        print(f"CLEANING UP: {issue.name}")
        print("=" * 70)
        
        all_success = True
        for cmd in issue.cleanup_commands:
            success, _ = self.run_command(cmd)
            if not success:
                all_success = False
        
        if all_success:
            print("\n✓ Cleanup completed successfully")
        else:
            print("\n⚠ Some cleanup commands failed")
        
        return all_success
    
    def cleanup_all(self) -> bool:
        """Cleanup all demo issues."""
        print("\n" + "=" * 70)
        print("CLEANING UP ALL DEMO ISSUES")
        print("=" * 70)
        
        all_success = True
        for issue_name in self.issues:
            print(f"\n--- Cleaning: {issue_name} ---")
            if not self.cleanup_issue(issue_name):
                all_success = False
        
        return all_success
    
    def list_issues(self):
        """List all available demo issues."""
        print("\n" + "=" * 70)
        print("AVAILABLE DEMO ISSUES")
        print("=" * 70)
        
        for name, issue in self.issues.items():
            print(f"\n{name}:")
            print(f"  Name: {issue.name}")
            print(f"  Alert: {issue.expected_alert}")
            print(f"  Trigger time: ~{issue.estimated_trigger_time}s")
            print(f"  Description:{issue.description.strip()}")
    
    def watch_alerts(self, timeout: int = 300):
        """Watch for alerts to fire."""
        print("\n" + "=" * 70)
        print("WATCHING FOR ALERTS")
        print("=" * 70)
        print(f"Watching for {timeout} seconds... (Ctrl+C to stop)")
        print("Checking AlertManager API...\n")
        
        import requests
        
        alertmanager_url = os.environ.get("ALERTMANAGER_URL", "http://localhost:9093")
        start_time = time.time()
        seen_alerts = set()
        
        try:
            while time.time() - start_time < timeout:
                try:
                    response = requests.get(f"{alertmanager_url}/api/v2/alerts", timeout=5)
                    if response.status_code == 200:
                        alerts = response.json()
                        for alert in alerts:
                            fingerprint = alert.get("fingerprint", "")
                            if fingerprint not in seen_alerts:
                                seen_alerts.add(fingerprint)
                                labels = alert.get("labels", {})
                                alert_name = labels.get("alertname", "Unknown")
                                severity = labels.get("severity", "unknown")
                                namespace = labels.get("namespace", "unknown")
                                
                                print(f"🚨 NEW ALERT: {alert_name}")
                                print(f"   Severity: {severity}")
                                print(f"   Namespace: {namespace}")
                                print(f"   Status: {alert.get('status', {}).get('state', 'unknown')}")
                                print()
                except requests.exceptions.RequestException:
                    pass  # Silently continue if AlertManager is not accessible
                
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nStopped watching.")
        
        print(f"\nTotal alerts seen: {len(seen_alerts)}")


def main():
    parser = argparse.ArgumentParser(
        description="Trigger real issues in Kubernetes for CB Model demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available issues
  python demo_trigger_issues.py --list
  
  # Trigger crash loop issue
  python demo_trigger_issues.py --issue crash-loop
  
  # Cleanup specific issue  
  python demo_trigger_issues.py --cleanup --issue crash-loop
  
  # Cleanup all issues
  python demo_trigger_issues.py --cleanup-all
  
  # Dry run (see commands without executing)
  python demo_trigger_issues.py --issue crash-loop --dry-run
  
  # Watch for alerts after triggering
  python demo_trigger_issues.py --issue crash-loop --watch
"""
    )
    
    parser.add_argument(
        "--issue",
        type=str,
        choices=["crash-loop", "service-down", "memory-stress", "bad-config", "replica-shortage"],
        help="Issue to trigger or cleanup"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available demo issues"
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Cleanup the specified issue instead of triggering"
    )
    parser.add_argument(
        "--cleanup-all",
        action="store_true",
        help="Cleanup all demo issues"
    )
    parser.add_argument(
        "--namespace",
        type=str,
        default="target-services",
        help="Kubernetes namespace (default: target-services)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show commands without executing"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for alerts after triggering"
    )
    parser.add_argument(
        "--watch-timeout",
        type=int,
        default=300,
        help="Alert watch timeout in seconds (default: 300)"
    )
    
    args = parser.parse_args()
    
    trigger = DemoIssueTrigger(namespace=args.namespace, dry_run=args.dry_run)
    
    if args.list:
        trigger.list_issues()
        return 0
    
    if args.cleanup_all:
        trigger.cleanup_all()
        return 0
    
    if args.cleanup:
        if not args.issue:
            print("Error: --issue required with --cleanup")
            return 1
        trigger.cleanup_issue(args.issue)
        return 0
    
    if args.issue:
        success = trigger.trigger_issue(args.issue)
        
        if success and args.watch:
            trigger.watch_alerts(timeout=args.watch_timeout)
        
        return 0 if success else 1
    
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
