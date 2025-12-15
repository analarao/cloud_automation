# cb_model/cb_model_server.py
"""
CB (Container-Brain) gRPC Server
Wraps vLLM to provide a gRPC interface for LLM completions.
Used by AlertManager, CS Model, and other services for MCP command execution.

The prompt is received via gRPC as a protobuf message, decoded, and used
as input to the vLLM inference engine.
"""

from concurrent import futures
import logging
import os
import time
import grpc
from typing import Optional

# gRPC generated code
import cb_model_pb2
import cb_model_pb2_grpc

# vLLM imports
from vllm import LLM, SamplingParams

# --- Configure logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Configuration from Environment ---
MODEL_NAME = os.environ.get("CB_MODEL_NAME", "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
MAX_MODEL_LEN = int(os.environ.get("CB_MAX_MODEL_LEN", "4096"))
GPU_MEMORY_UTILIZATION = float(os.environ.get("CB_GPU_MEMORY_UTILIZATION", "0.95"))
TENSOR_PARALLEL_SIZE = int(os.environ.get("CB_TENSOR_PARALLEL_SIZE", "1"))
DTYPE = os.environ.get("CB_DTYPE", "auto")
QUANTIZATION = os.environ.get("CB_QUANTIZATION", None)
GRPC_PORT = int(os.environ.get("CB_GRPC_PORT", "50051"))
MAX_WORKERS = int(os.environ.get("CB_MAX_WORKERS", "10"))

# Default system prompt from environment (can be overridden per-request)
DEFAULT_SYSTEM_PROMPT = os.environ.get("CB_SYSTEM_PROMPT", """
You are CB (Container-Brain), an expert SRE and DevOps AI assistant.
Your role is to analyze alerts, diagnose issues, and generate MCP commands for remediation.
Always respond with actionable commands in JSON format when remediation is needed.
""").strip()

# Global LLM instance
llm: Optional[LLM] = None
model_loaded = False


def initialize_llm():
    """Initialize the vLLM engine with configured parameters."""
    global llm, model_loaded
    
    logger.info(f"Initializing vLLM with model: {MODEL_NAME}")
    logger.info(f"GPU Memory Utilization: {GPU_MEMORY_UTILIZATION}")
    logger.info(f"Max Model Length: {MAX_MODEL_LEN}")
    logger.info(f"Tensor Parallel Size: {TENSOR_PARALLEL_SIZE}")
    logger.info(f"Dtype: {DTYPE}")
    
    try:
        llm_kwargs = {
            "model": MODEL_NAME,
            "max_model_len": MAX_MODEL_LEN,
            "gpu_memory_utilization": GPU_MEMORY_UTILIZATION,
            "tensor_parallel_size": TENSOR_PARALLEL_SIZE,
            "dtype": DTYPE,
            "trust_remote_code": True,
        }
        
        # Add quantization if specified
        if QUANTIZATION and QUANTIZATION.strip():
            llm_kwargs["quantization"] = QUANTIZATION
            logger.info(f"Using quantization: {QUANTIZATION}")
        
        llm = LLM(**llm_kwargs)
        model_loaded = True
        logger.info("vLLM model loaded successfully!")
        
    except Exception as e:
        logger.error(f"Failed to initialize vLLM: {e}")
        raise


class CBModelServicer(cb_model_pb2_grpc.CBModelServiceServicer):
    """gRPC servicer implementation for CB Model.
    
    Receives prompts via gRPC protobuf messages, decodes them, and generates
    completions using the local vLLM instance.
    """
    
    def GenerateCompletion(self, request, context):
        """
        Generate a completion from the LLM.
        
        The prompt is received as a protobuf message (CompletionRequest),
        decoded to a string, and used as input to vLLM.
        
        If no system_prompt is provided in the request, the default
        CB_SYSTEM_PROMPT from environment is used.
        """
        start_time = time.time()
        request_id = request.request_id or f"req-{int(time.time()*1000)}"
        
        logger.info(f"[{request_id}] Received gRPC completion request from: {request.source}")
        logger.info(f"[{request_id}] Decoding protobuf prompt ({len(request.prompt)} chars)")
        
        if not model_loaded or llm is None:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Model not loaded")
            return cb_model_pb2.CompletionResponse(
                request_id=request_id,
                finish_reason="error"
            )
        
        try:
            # Decode the prompt from the protobuf message
            # The prompt field is a string that may contain:
            # - Raw text prompt
            # - Serialized alert context (JSON)
            # - MCP command request
            decoded_prompt = request.prompt
            
            # Use request's system_prompt if provided, otherwise use default
            system_prompt = request.system_prompt if request.system_prompt else DEFAULT_SYSTEM_PROMPT
            
            logger.debug(f"[{request_id}] Using system prompt: {system_prompt[:100]}...")
            
            # Format as chat-style prompt with system context
            full_prompt = f"<|system|>\n{system_prompt}\n<|user|>\n{decoded_prompt}\n<|assistant|>\n"
            
            # Configure sampling parameters
            sampling_params = SamplingParams(
                max_tokens=request.max_tokens if request.max_tokens > 0 else 512,
                temperature=request.temperature if request.temperature > 0 else 0.7,
                top_p=request.top_p if request.top_p > 0 else 0.9,
            )
            
            # Generate completion using vLLM
            logger.info(f"[{request_id}] Generating completion with vLLM...")
            outputs = llm.generate([full_prompt], sampling_params)
            output = outputs[0]
            
            generated_text = output.outputs[0].text
            prompt_tokens = len(output.prompt_token_ids)
            completion_tokens = len(output.outputs[0].token_ids)
            
            generation_time_ms = int((time.time() - start_time) * 1000)
            
            logger.info(f"[{request_id}] Generated {completion_tokens} tokens in {generation_time_ms}ms")
            
            return cb_model_pb2.CompletionResponse(
                completion=generated_text,
                request_id=request_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                generation_time_ms=generation_time_ms,
                model_name=MODEL_NAME,
                finish_reason="stop"
            )
            
        except Exception as e:
            logger.error(f"[{request_id}] Error generating completion: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return cb_model_pb2.CompletionResponse(
                request_id=request_id,
                finish_reason="error"
            )
    
    def StreamCompletion(self, request, context):
        """Stream completion tokens as they're generated."""
        request_id = request.request_id or f"req-{int(time.time()*1000)}"
        
        logger.info(f"[{request_id}] Received streaming request from: {request.source}")
        
        if not model_loaded or llm is None:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Model not loaded")
            return
        
        try:
            # Build the full prompt
            full_prompt = request.prompt
            if request.system_prompt:
                full_prompt = f"<|system|>\n{request.system_prompt}\n<|user|>\n{request.prompt}\n<|assistant|>\n"
            
            sampling_params = SamplingParams(
                max_tokens=request.max_tokens if request.max_tokens > 0 else 512,
                temperature=request.temperature if request.temperature > 0 else 0.7,
                top_p=request.top_p if request.top_p > 0 else 0.9,
            )
            
            # Note: vLLM's streaming API would be used here in production
            # For now, we generate full and chunk it
            outputs = llm.generate([full_prompt], sampling_params)
            output = outputs[0]
            generated_text = output.outputs[0].text
            
            # Stream in chunks
            chunk_size = 10  # tokens approximation
            words = generated_text.split()
            
            for i, word in enumerate(words):
                yield cb_model_pb2.CompletionChunk(
                    text=word + " ",
                    is_final=(i == len(words) - 1),
                    request_id=request_id,
                    chunk_index=i
                )
                
        except Exception as e:
            logger.error(f"[{request_id}] Error in streaming: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
    
    def HealthCheck(self, request, context):
        """Check if the model is ready to serve requests."""
        try:
            # Try to get GPU memory info if available
            gpu_memory_usage = 0.0
            try:
                import torch
                if torch.cuda.is_available():
                    gpu_memory_usage = torch.cuda.memory_allocated() / torch.cuda.max_memory_allocated() * 100
            except:
                pass
            
            return cb_model_pb2.HealthCheckResponse(
                healthy=model_loaded,
                model_loaded=model_loaded,
                status="ready" if model_loaded else "loading",
                gpu_memory_usage=gpu_memory_usage
            )
        except Exception as e:
            return cb_model_pb2.HealthCheckResponse(
                healthy=False,
                model_loaded=False,
                status=f"error: {e}",
                gpu_memory_usage=0.0
            )
    
    def GetModelInfo(self, request, context):
        """Get information about the loaded model."""
        try:
            gpu_info = "N/A"
            try:
                import torch
                if torch.cuda.is_available():
                    gpu_info = torch.cuda.get_device_name(0)
            except:
                pass
            
            return cb_model_pb2.ModelInfoResponse(
                model_name=MODEL_NAME,
                max_context_length=MAX_MODEL_LEN,
                dtype=DTYPE,
                gpu_info=gpu_info,
                quantization=QUANTIZATION or "none"
            )
        except Exception as e:
            logger.error(f"Error getting model info: {e}")
            return cb_model_pb2.ModelInfoResponse(
                model_name=MODEL_NAME,
                max_context_length=MAX_MODEL_LEN,
                dtype=DTYPE,
                gpu_info="error",
                quantization="unknown"
            )


def serve():
    """Start the gRPC server."""
    # Initialize the LLM first
    initialize_llm()
    
    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    cb_model_pb2_grpc.add_CBModelServiceServicer_to_server(CBModelServicer(), server)
    
    server_address = f"[::]:{GRPC_PORT}"
    server.add_insecure_port(server_address)
    
    server.start()
    logger.info(f"CB Model gRPC server started on port {GRPC_PORT}")
    logger.info(f"Model: {MODEL_NAME}")
    logger.info("Ready to accept connections...")
    
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
