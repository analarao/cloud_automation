#!/usr/bin/env python3
"""
CB Model gRPC Bridge

This service provides a gRPC interface for the CB Model.
It receives alert analysis requests and uses the Orchestrator
to process them through the LLM with MCP tool execution.

Architecture:
    Alert Context Aggregator (gRPC client)
        |
        v
    CB Model gRPC Bridge (this service, port 50051)
        |
        v
    CB Orchestrator
        |
        +---> vLLM OpenAI API (port 8000)
        |
        +---> MCP Kubernetes Client (subprocess)

This replaces the old cb_model_server.py which used vLLM Python API directly.
Now we use vLLM's OpenAI-compatible server for tool calling support.
"""

import os
import sys
import json
import logging
import signal
import threading
from concurrent import futures
from typing import Optional, Dict, Any

import grpc

# gRPC generated code - use v2 proto
import cb_model_v2_pb2 as pb2
import cb_model_v2_pb2_grpc as pb2_grpc

from orchestrator import CBOrchestrator, Alert, RemediationResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CBModelGRPCService(pb2_grpc.CBModelServiceServicer):
    """
    gRPC service implementation for CB Model.
    Uses the Orchestrator for LLM-based alert analysis and remediation.
    """
    
    def __init__(
        self,
        vllm_url: str = None,
        target_namespace: str = "target-services",
        max_iterations: int = 10,
    ):
        """
        Initialize the gRPC service.
        
        Args:
            vllm_url: URL of the vLLM OpenAI API
            target_namespace: Kubernetes namespace to operate on
            max_iterations: Max LLM reasoning iterations
        """
        self.vllm_url = vllm_url or os.getenv(
            "CB_MODEL_OPENAI_API_URL",
            "http://localhost:8000/v1"
        )
        self.target_namespace = target_namespace
        self.max_iterations = max_iterations
        
        # Initialize orchestrator
        self.orchestrator = CBOrchestrator(
            vllm_base_url=self.vllm_url,
            target_namespace=self.target_namespace,
            max_iterations=self.max_iterations
        )
        
        # Model info cache
        self._model_name: Optional[str] = None
        self._model_loaded = False
        
        logger.info(f"CB Model gRPC Service initialized")
        logger.info(f"  vLLM URL: {self.vllm_url}")
        logger.info(f"  Target Namespace: {self.target_namespace}")
    
    def _extract_alert_from_request(self, request: pb2.AlertAnalysisRequest) -> Alert:
        """Convert protobuf AlertAnalysisRequest to Alert dataclass."""
        alert_info = request.alert
        
        return Alert(
            alert_name=alert_info.alert_name,
            severity=alert_info.severity,
            namespace=request.kubernetes.namespace or self.target_namespace,
            pod_name=request.container.pod_name,
            deployment_name=request.kubernetes.deployment_name,
            message=alert_info.description or alert_info.summary,
            labels=dict(alert_info.labels),
            annotations=dict(alert_info.annotations),
            value=alert_info.current_value,
            threshold=alert_info.threshold_value
        )
    
    def _build_remediation_response(
        self,
        request: pb2.AlertAnalysisRequest,
        result: RemediationResult
    ) -> pb2.AlertAnalysisResponse:
        """Build protobuf response from RemediationResult."""
        
        response = pb2.AlertAnalysisResponse(
            request_id=request.request_id,
            raw_response=result.final_response,
            confidence=0.8 if result.success else 0.3,
            completion_tokens=result.iterations * 100,  # Estimate
        )
        
        # Build root cause analysis from the response
        response.root_cause.primary_cause = "See raw_response for details"
        response.root_cause.category = "automated_analysis"
        
        # Convert actions to remediation actions
        for i, action in enumerate(result.actions_taken):
            rem_action = pb2.RemediationAction(
                priority=i + 1,
                action_type="mcp",
                description=f"Executed {action['tool']}",
                risk_level="medium",
                requires_approval=False
            )
            
            # Set MCP command details
            rem_action.mcp_command.toolkit = "kubernetes"
            rem_action.mcp_command.action = action['tool']
            rem_action.mcp_command.namespace = self.target_namespace
            
            # Add parameters
            for key, value in action.get('arguments', {}).items():
                rem_action.mcp_command.parameters[key] = str(value)
            
            response.remediation_actions.append(rem_action)
        
        # Set impact assessment
        if result.success:
            response.impact.severity_after = "resolved"
            response.impact.summary = "Alert remediated successfully"
        else:
            response.impact.severity_after = "unchanged"
            response.impact.summary = f"Remediation failed: {result.error or 'Unknown error'}"
        
        return response
    
    def AnalyzeAlert(
        self,
        request: pb2.AlertAnalysisRequest,
        context: grpc.ServicerContext
    ) -> pb2.AlertAnalysisResponse:
        """
        Analyze an alert and provide remediation recommendations.
        Uses the orchestrator to process through LLM with MCP tools.
        """
        logger.info(f"[{request.request_id}] Received AnalyzeAlert request")
        logger.info(f"[{request.request_id}] Alert: {request.alert.alert_name}")
        
        try:
            # Convert request to Alert
            alert = self._extract_alert_from_request(request)
            
            # Start orchestrator if not running
            if not hasattr(self, '_orchestrator_started'):
                self.orchestrator.start()
                self._orchestrator_started = True
            
            # Process through orchestrator
            result = self.orchestrator.process_alert(alert)
            
            # Build response
            response = self._build_remediation_response(request, result)
            
            logger.info(f"[{request.request_id}] Analysis complete. Success: {result.success}")
            return response
            
        except Exception as e:
            logger.error(f"[{request.request_id}] Error analyzing alert: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            
            # Return error response
            return pb2.AlertAnalysisResponse(
                request_id=request.request_id,
                raw_response=f"Error: {e}",
                confidence=0.0
            )
    
    def GenerateCompletion(
        self,
        request: pb2.CompletionRequest,
        context: grpc.ServicerContext
    ) -> pb2.CompletionResponse:
        """
        Generate a simple completion (backward compatible).
        Uses vLLM directly without MCP tools.
        """
        logger.info("Received GenerateCompletion request")
        
        try:
            from openai import OpenAI
            
            client = OpenAI(
                base_url=self.vllm_url,
                api_key="EMPTY"
            )
            
            # Get model name
            if not self._model_name:
                models = client.models.list()
                if models.data:
                    self._model_name = models.data[0].id
            
            # Build messages
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.append({"role": "user", "content": request.prompt})
            
            # Generate completion
            response = client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                max_tokens=request.max_tokens or 1024,
                temperature=request.temperature or 0.7,
            )
            
            completion = response.choices[0].message.content
            
            return pb2.CompletionResponse(
                completion=completion,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
            
        except Exception as e:
            logger.error(f"Error generating completion: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.CompletionResponse(completion=f"Error: {e}")
    
    def StreamCompletion(
        self,
        request: pb2.CompletionRequest,
        context: grpc.ServicerContext
    ):
        """Stream completion tokens (not implemented yet)."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Streaming not implemented")
        return
    
    def HealthCheck(
        self,
        request: pb2.HealthCheckRequest,
        context: grpc.ServicerContext
    ) -> pb2.HealthCheckResponse:
        """Check if the service is healthy."""
        try:
            import httpx
            
            # Check vLLM health
            response = httpx.get(f"{self.vllm_url.rstrip('/v1')}/health", timeout=5)
            vllm_healthy = response.status_code == 200
            
            if vllm_healthy and not self._model_loaded:
                self._model_loaded = True
                # Get model name
                from openai import OpenAI
                client = OpenAI(base_url=self.vllm_url, api_key="EMPTY")
                models = client.models.list()
                if models.data:
                    self._model_name = models.data[0].id
            
            return pb2.HealthCheckResponse(
                healthy=vllm_healthy,
                model_loaded=self._model_loaded,
                model_name=self._model_name or "unknown",
            )
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return pb2.HealthCheckResponse(
                healthy=False,
                model_loaded=False,
                model_name="error: " + str(e),
            )
    
    def GetModelInfo(
        self,
        request: pb2.ModelInfoRequest,
        context: grpc.ServicerContext
    ) -> pb2.ModelInfoResponse:
        """Get information about the loaded model."""
        try:
            from openai import OpenAI
            
            client = OpenAI(base_url=self.vllm_url, api_key="EMPTY")
            models = client.models.list()
            
            if models.data:
                model = models.data[0]
                return pb2.ModelInfoResponse(
                    model_name=model.id,
                    model_type="vllm-openai",
                    capabilities=["chat", "tool_calling"],
                )
            else:
                return pb2.ModelInfoResponse(
                    model_name="unknown",
                    model_type="unknown",
                )
                
        except Exception as e:
            logger.error(f"Error getting model info: {e}")
            return pb2.ModelInfoResponse(
                model_name=f"error: {e}",
                model_type="error",
            )
    
    def shutdown(self):
        """Shutdown the service."""
        logger.info("Shutting down CB Model gRPC Service...")
        if hasattr(self, '_orchestrator_started') and self._orchestrator_started:
            self.orchestrator.stop()
        logger.info("Shutdown complete")


def serve(port: int = 50051, max_workers: int = 10):
    """
    Start the gRPC server.
    
    Args:
        port: Port to listen on
        max_workers: Maximum number of worker threads
    """
    # Create service
    service = CBModelGRPCService(
        vllm_url=os.getenv("CB_MODEL_OPENAI_API_URL", "http://localhost:8000/v1"),
        target_namespace=os.getenv("MCP_TARGET_NAMESPACE", "target-services"),
        max_iterations=int(os.getenv("CB_MAX_ITERATIONS", "10")),
    )
    
    # Create server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb2_grpc.add_CBModelServiceServicer_to_server(service, server)
    
    # Bind to port
    address = f"[::]:{port}"
    server.add_insecure_port(address)
    
    # Signal handler for graceful shutdown
    stop_event = threading.Event()
    
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        service.shutdown()
        server.stop(grace=5)
        stop_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start server
    server.start()
    logger.info("=" * 60)
    logger.info("CB Model gRPC Bridge Started")
    logger.info("=" * 60)
    logger.info(f"  Listening on: {address}")
    logger.info(f"  vLLM URL: {service.vllm_url}")
    logger.info(f"  Target Namespace: {service.target_namespace}")
    logger.info("=" * 60)
    
    # Wait for shutdown
    stop_event.wait()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="CB Model gRPC Bridge")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CB_GRPC_PORT", "50051")),
        help="gRPC port (default: 50051)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("CB_GRPC_WORKERS", "10")),
        help="Max worker threads (default: 10)"
    )
    
    args = parser.parse_args()
    
    serve(port=args.port, max_workers=args.workers)
