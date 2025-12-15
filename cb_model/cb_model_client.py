# cb_model/cb_model_client.py
"""
CB Model gRPC Client
Helper module for services (AlertManager, CS Model, etc.) to communicate with
the CB (Container-Brain) LLM service via gRPC.

Usage:
    from cb_model_client import CBModelClient
    
    client = CBModelClient()
    response = client.generate_completion(
        prompt="Analyze this alert and suggest remediation...",
        system_prompt="You are an SRE expert...",
        source="cs_model"
    )
    print(response.completion)
"""

import os
import logging
import grpc
from typing import Optional, Iterator

# These will be generated from cb_model.proto
import cb_model_pb2
import cb_model_pb2_grpc

logger = logging.getLogger(__name__)

# Default configuration from environment
DEFAULT_GRPC_HOST = os.environ.get("CB_MODEL_GRPC_HOST", "cb-model-service")
DEFAULT_GRPC_PORT = os.environ.get("CB_MODEL_GRPC_PORT", "50051")


class CBModelClient:
    """
    gRPC client for communicating with the CB Model (Container-Brain) LLM service.
    
    This client can be used by:
    - CS Model Service: For alert analysis and MCP command generation
    - AlertManager webhooks: For automated remediation decisions
    - Any service needing LLM completions
    """
    
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[str] = None,
        timeout: float = 60.0
    ):
        """
        Initialize the CB Model gRPC client.
        
        Args:
            host: gRPC server hostname (default: from CB_MODEL_GRPC_HOST env)
            port: gRPC server port (default: from CB_MODEL_GRPC_PORT env)
            timeout: Default timeout for RPC calls in seconds
        """
        self.host = host or DEFAULT_GRPC_HOST
        self.port = port or DEFAULT_GRPC_PORT
        self.timeout = timeout
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[cb_model_pb2_grpc.CBModelServiceStub] = None
    
    @property
    def address(self) -> str:
        """Get the full gRPC address."""
        return f"{self.host}:{self.port}"
    
    def _ensure_connected(self):
        """Ensure we have an active gRPC channel and stub."""
        if self._channel is None:
            logger.info(f"Connecting to CB Model at {self.address}")
            self._channel = grpc.insecure_channel(self.address)
            self._stub = cb_model_pb2_grpc.CBModelServiceStub(self._channel)
    
    def close(self):
        """Close the gRPC channel."""
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._stub = None
    
    def __enter__(self):
        self._ensure_connected()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def generate_completion(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        request_id: str = "",
        source: str = "",
        metadata: Optional[dict] = None,
        timeout: Optional[float] = None
    ) -> cb_model_pb2.CompletionResponse:
        """
        Generate a completion from the CB Model LLM.
        
        Args:
            prompt: The prompt text to send to the LLM
            system_prompt: Optional system prompt to set context
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-1.0)
            top_p: Top-p nucleus sampling
            request_id: Optional request identifier for tracking
            source: Source of the request (e.g., "cs_model", "alertmanager")
            metadata: Optional additional metadata as key-value pairs
            timeout: Override default timeout for this request
        
        Returns:
            CompletionResponse with the generated text and metadata
        
        Raises:
            grpc.RpcError: If the gRPC call fails
        """
        self._ensure_connected()
        
        request = cb_model_pb2.CompletionRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            request_id=request_id,
            source=source,
            metadata=metadata or {}
        )
        
        try:
            response = self._stub.GenerateCompletion(
                request,
                timeout=timeout or self.timeout
            )
            logger.info(
                f"[{response.request_id}] Completion generated: "
                f"{response.completion_tokens} tokens in {response.generation_time_ms}ms"
            )
            return response
        except grpc.RpcError as e:
            logger.error(f"gRPC error generating completion: {e.code()} - {e.details()}")
            raise
    
    def stream_completion(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        request_id: str = "",
        source: str = "",
        metadata: Optional[dict] = None
    ) -> Iterator[cb_model_pb2.CompletionChunk]:
        """
        Stream completion tokens from the CB Model LLM.
        
        Args:
            prompt: The prompt text to send to the LLM
            system_prompt: Optional system prompt to set context
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-1.0)
            top_p: Top-p nucleus sampling
            request_id: Optional request identifier for tracking
            source: Source of the request
            metadata: Optional additional metadata
        
        Yields:
            CompletionChunk objects with incremental text
        
        Raises:
            grpc.RpcError: If the gRPC call fails
        """
        self._ensure_connected()
        
        request = cb_model_pb2.CompletionRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            request_id=request_id,
            source=source,
            metadata=metadata or {}
        )
        
        try:
            for chunk in self._stub.StreamCompletion(request):
                yield chunk
        except grpc.RpcError as e:
            logger.error(f"gRPC error streaming completion: {e.code()} - {e.details()}")
            raise
    
    def health_check(self, timeout: float = 5.0) -> cb_model_pb2.HealthCheckResponse:
        """
        Check if the CB Model service is healthy and ready.
        
        Args:
            timeout: Timeout for the health check
        
        Returns:
            HealthCheckResponse with health status
        """
        self._ensure_connected()
        
        try:
            return self._stub.HealthCheck(
                cb_model_pb2.HealthCheckRequest(),
                timeout=timeout
            )
        except grpc.RpcError as e:
            logger.error(f"Health check failed: {e.code()} - {e.details()}")
            raise
    
    def get_model_info(self, timeout: float = 5.0) -> cb_model_pb2.ModelInfoResponse:
        """
        Get information about the loaded model.
        
        Args:
            timeout: Timeout for the request
        
        Returns:
            ModelInfoResponse with model details
        """
        self._ensure_connected()
        
        try:
            return self._stub.GetModelInfo(
                cb_model_pb2.ModelInfoRequest(),
                timeout=timeout
            )
        except grpc.RpcError as e:
            logger.error(f"Get model info failed: {e.code()} - {e.details()}")
            raise
    
    def is_ready(self) -> bool:
        """
        Quick check if the service is ready.
        
        Returns:
            True if the service is healthy and model is loaded
        """
        try:
            response = self.health_check(timeout=3.0)
            return response.healthy and response.model_loaded
        except:
            return False


# Convenience function for quick one-off completions
def generate_completion(
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 512,
    temperature: float = 0.7,
    source: str = "unknown"
) -> str:
    """
    Quick helper function to generate a completion.
    
    Args:
        prompt: The prompt text
        system_prompt: Optional system prompt
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        source: Source identifier
    
    Returns:
        The generated completion text
    """
    with CBModelClient() as client:
        response = client.generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            source=source
        )
        return response.completion


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test connection
    client = CBModelClient()
    
    print("Checking CB Model health...")
    try:
        health = client.health_check()
        print(f"Healthy: {health.healthy}")
        print(f"Model loaded: {health.model_loaded}")
        print(f"Status: {health.status}")
    except Exception as e:
        print(f"Health check failed: {e}")
    
    print("\nGetting model info...")
    try:
        info = client.get_model_info()
        print(f"Model: {info.model_name}")
        print(f"Max context: {info.max_context_length}")
        print(f"GPU: {info.gpu_info}")
    except Exception as e:
        print(f"Get model info failed: {e}")
    
    print("\nGenerating test completion...")
    try:
        response = client.generate_completion(
            prompt="What is Kubernetes? Explain in one sentence.",
            system_prompt="You are a helpful DevOps assistant.",
            max_tokens=100,
            source="test_client"
        )
        print(f"Response: {response.completion}")
        print(f"Tokens: {response.total_tokens}")
        print(f"Time: {response.generation_time_ms}ms")
    except Exception as e:
        print(f"Completion failed: {e}")
    
    client.close()
