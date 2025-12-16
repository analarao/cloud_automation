#!/usr/bin/env python3
"""
CB Model Configuration Validator
=================================

This script validates the CB Model server configuration without requiring
vLLM or GPU. Use this to catch configuration issues before building/deploying.

Usage:
    # Validate with current environment:
    python validate_config.py

    # Validate with simulated Kubernetes environment:
    python validate_config.py --simulate-k8s

    # Test specific environment variables:
    CB_MAX_MODEL_LEN=2048 python validate_config.py

    # Validate Helm values.yaml matches expected config:
    python validate_config.py --helm-values ../helm/monitoring-services/values.yaml

    # Full validation (config + proto + imports):
    python validate_config.py --full
"""

import argparse
import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Color output for terminal
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def color(text: str, color_code: str) -> str:
    """Apply color to text if stdout is a tty."""
    if sys.stdout.isatty():
        return f"{color_code}{text}{Colors.END}"
    return text


# Known model max_position_embeddings (to warn about config mismatches)
KNOWN_MODEL_MAX_LENGTHS = {
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0": 2048,
    "microsoft/phi-2": 2048,
    "Qwen/Qwen2-1.5B-Instruct": 32768,
    "mistralai/Mistral-7B-Instruct-v0.2": 32768,
    "meta-llama/Llama-2-7b-chat-hf": 4096,
    "meta-llama/Llama-2-13b-chat-hf": 4096,
    "codellama/CodeLlama-7b-Instruct-hf": 16384,
    "codellama/CodeLlama-13b-Instruct-hf": 16384,
}


# Expected configuration schema with types and validation
# NOTE: Default CB_MAX_MODEL_LEN is 2048 to match TinyLlama's max_position_embeddings
CONFIG_SCHEMA = {
    "CB_MODEL_NAME": {
        "type": str,
        "default": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        "description": "HuggingFace model name or path",
        "validation": lambda x: "/" in x or Path(x).exists(),
        "validation_msg": "Should be HuggingFace model ID (owner/model) or local path"
    },
    "CB_MAX_MODEL_LEN": {
        "type": int,
        "default": 2048,
        "description": "Maximum model context length in tokens",
        "validation": lambda x: 512 <= x <= 131072,
        "validation_msg": "Should be between 512 and 131072"
    },
    "CB_GPU_MEMORY_UTILIZATION": {
        "type": float,
        "default": 0.85,  # Leave headroom for CUDA context
        "description": "GPU memory utilization (0.0-1.0)",
        "validation": lambda x: 0.1 <= x <= 1.0,
        "validation_msg": "Should be between 0.1 and 1.0"
    },
    "CB_TENSOR_PARALLEL_SIZE": {
        "type": int,
        "default": 1,
        "description": "Number of GPUs for tensor parallelism",
        "validation": lambda x: 1 <= x <= 8,
        "validation_msg": "Should be between 1 and 8"
    },
    "CB_DTYPE": {
        "type": str,
        "default": "auto",
        "description": "Model data type",
        "validation": lambda x: x in ["auto", "half", "float16", "bfloat16", "float32"],
        "validation_msg": "Should be one of: auto, half, float16, bfloat16, float32"
    },
    "CB_QUANTIZATION": {
        "type": str,
        "default": "",
        "description": "Quantization method (optional)",
        "validation": lambda x: x in ["", "awq", "gptq", "squeezellm"],
        "validation_msg": "Should be empty or one of: awq, gptq, squeezellm"
    },
    "CB_GRPC_PORT": {
        "type": int,
        "default": 50051,
        "description": "gRPC server port",
        "validation": lambda x: 1024 <= x <= 65535,
        "validation_msg": "Should be between 1024 and 65535"
    },
    "CB_MAX_WORKERS": {
        "type": int,
        "default": 10,
        "description": "Maximum gRPC worker threads",
        "validation": lambda x: 1 <= x <= 100,
        "validation_msg": "Should be between 1 and 100"
    },
    "CB_SYSTEM_PROMPT": {
        "type": str,
        "default": "You are CB (Container-Brain)...",
        "description": "Default system prompt for LLM",
        "validation": lambda x: len(x) > 10,
        "validation_msg": "Should be a non-empty prompt"
    }
}


def get_config_value(env_name: str) -> Tuple[Any, str]:
    """Get config value and its source (ENV or DEFAULT)."""
    schema = CONFIG_SCHEMA[env_name]
    raw_value = os.environ.get(env_name)
    
    if raw_value is not None and raw_value.strip():
        source = "ENV"
        str_value = raw_value.strip()
    else:
        source = "DEFAULT"
        str_value = str(schema["default"])
    
    # Cast to type
    try:
        if schema["type"] == int:
            value = int(str_value)
        elif schema["type"] == float:
            value = float(str_value)
        else:
            value = str_value
    except (ValueError, TypeError):
        value = None
    
    return value, source


def validate_config() -> Tuple[bool, List[Dict[str, Any]]]:
    """Validate all configuration values."""
    results = []
    all_valid = True
    
    for env_name, schema in CONFIG_SCHEMA.items():
        value, source = get_config_value(env_name)
        
        result = {
            "name": env_name,
            "value": value,
            "source": source,
            "type": schema["type"].__name__,
            "description": schema["description"],
            "valid": True,
            "error": None,
            "warning": None
        }
        
        # Check if value could be parsed
        if value is None:
            result["valid"] = False
            result["error"] = f"Could not parse as {schema['type'].__name__}"
            all_valid = False
        # Run validation function
        elif "validation" in schema:
            try:
                if not schema["validation"](value):
                    result["valid"] = False
                    result["error"] = schema.get("validation_msg", "Validation failed")
                    all_valid = False
            except Exception as e:
                result["valid"] = False
                result["error"] = f"Validation error: {e}"
                all_valid = False
        
        results.append(result)
    
    return all_valid, results


def validate_model_max_len() -> Tuple[bool, str, str]:
    """
    Check if CB_MAX_MODEL_LEN is compatible with the selected model.
    Returns (is_valid, message, severity)
    """
    model_name, _ = get_config_value("CB_MODEL_NAME")
    max_len, _ = get_config_value("CB_MAX_MODEL_LEN")
    
    if model_name in KNOWN_MODEL_MAX_LENGTHS:
        model_max = KNOWN_MODEL_MAX_LENGTHS[model_name]
        if max_len > model_max:
            return (
                False,
                f"CB_MAX_MODEL_LEN ({max_len}) exceeds model's max_position_embeddings ({model_max}). "
                f"This will cause vLLM to fail! Set CB_MAX_MODEL_LEN <= {model_max}",
                "error"
            )
        elif max_len == model_max:
            return (True, f"CB_MAX_MODEL_LEN matches model's max ({model_max})", "ok")
        else:
            return (True, f"CB_MAX_MODEL_LEN ({max_len}) is within model's max ({model_max})", "ok")
    else:
        return (
            True,
            f"Model '{model_name}' not in known models list. Cannot verify max_model_len compatibility.",
            "warning"
        )


def validate_proto_files() -> Tuple[bool, str]:
    """Check if proto files exist and can be imported."""
    try:
        script_dir = Path(__file__).parent
        
        # Check proto file exists
        proto_file = script_dir / "cb_model.proto"
        if not proto_file.exists():
            return False, f"Proto file not found: {proto_file}"
        
        # Check generated files exist
        pb2_file = script_dir / "cb_model_pb2.py"
        pb2_grpc_file = script_dir / "cb_model_pb2_grpc.py"
        
        if not pb2_file.exists():
            return False, f"Generated proto file not found: {pb2_file}. Run: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cb_model.proto"
        
        if not pb2_grpc_file.exists():
            return False, f"Generated gRPC file not found: {pb2_grpc_file}"
        
        # Try to import
        sys.path.insert(0, str(script_dir))
        import cb_model_pb2
        import cb_model_pb2_grpc
        
        return True, "Proto files OK"
    except Exception as e:
        return False, f"Failed to import proto modules: {e}"


def validate_imports() -> Tuple[bool, List[str]]:
    """Check if all required imports are available."""
    required = [
        ("grpc", "grpcio"),
        ("google.protobuf", "protobuf"),
    ]
    
    optional = [
        ("vllm", "vllm - required for actual model serving"),
    ]
    
    errors = []
    
    for module, package in required:
        try:
            __import__(module)
        except ImportError:
            errors.append(f"Missing required package: {package} (pip install {package})")
    
    for module, desc in optional:
        try:
            __import__(module)
        except ImportError:
            pass  # Optional, don't report as error
    
    return len(errors) == 0, errors


def parse_helm_values(values_path: str) -> Dict[str, Any]:
    """Parse Helm values.yaml and extract cbModel config."""
    try:
        import yaml
    except ImportError:
        print(color("Warning: PyYAML not installed, cannot parse Helm values", Colors.YELLOW))
        return {}
    
    with open(values_path) as f:
        values = yaml.safe_load(f)
    
    cb_model = values.get("cbModel", {})
    model = cb_model.get("model", {})
    gpu = cb_model.get("gpu", {})
    service = cb_model.get("service", {})
    
    # Map to environment variable names
    helm_config = {
        "CB_MODEL_NAME": model.get("name"),
        "CB_MAX_MODEL_LEN": model.get("maxModelLen"),
        "CB_DTYPE": model.get("dtype"),
        "CB_QUANTIZATION": model.get("quantization"),
        "CB_GPU_MEMORY_UTILIZATION": gpu.get("memoryUtilization"),
        "CB_TENSOR_PARALLEL_SIZE": gpu.get("tensorParallelSize"),
        "CB_GRPC_PORT": service.get("grpcPort"),
    }
    
    return {k: v for k, v in helm_config.items() if v is not None}


def print_results(results: List[Dict[str, Any]]):
    """Pretty print validation results."""
    print("\n" + "=" * 70)
    print(color("CB Model Configuration Validation", Colors.BOLD))
    print("=" * 70 + "\n")
    
    # Print as table
    name_width = max(len(r["name"]) for r in results)
    
    for r in results:
        status = color("✓", Colors.GREEN) if r["valid"] else color("✗", Colors.RED)
        source_color = Colors.BLUE if r["source"] == "ENV" else Colors.YELLOW
        source = color(f"[{r['source']}]", source_color)
        
        # Truncate long values
        value_str = str(r["value"])
        if len(value_str) > 40:
            value_str = value_str[:37] + "..."
        
        print(f"{status} {r['name']:<{name_width}} = {value_str:<42} {source}")
        
        if not r["valid"]:
            print(f"   {color('└── ' + r['error'], Colors.RED)}")
    
    print()


def simulate_k8s_env():
    """Set environment variables to simulate Kubernetes deployment."""
    # These would be set by the Kubernetes deployment
    k8s_env = {
        "CB_MODEL_NAME": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        "CB_MAX_MODEL_LEN": "2048",  # Common override for smaller instances
        "CB_GPU_MEMORY_UTILIZATION": "0.90",
        "CB_TENSOR_PARALLEL_SIZE": "1",
        "CB_DTYPE": "auto",
        "CB_GRPC_PORT": "50051",
        "CB_MAX_WORKERS": "10",
    }
    
    print(color("\n🔧 Simulating Kubernetes environment variables...\n", Colors.BLUE))
    for key, value in k8s_env.items():
        os.environ[key] = value
        print(f"   export {key}={value}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Validate CB Model server configuration without vLLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validate_config.py                     # Basic validation
  python validate_config.py --full              # Full validation with imports
  python validate_config.py --simulate-k8s      # Simulate K8s environment
  python validate_config.py --json              # Output as JSON
  CB_MAX_MODEL_LEN=2048 python validate_config.py  # Test specific value
        """
    )
    parser.add_argument("--full", action="store_true", help="Run full validation including imports and proto files")
    parser.add_argument("--simulate-k8s", action="store_true", help="Simulate Kubernetes environment variables")
    parser.add_argument("--helm-values", type=str, help="Path to Helm values.yaml to compare")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--strict", action="store_true", help="Exit with error code if any validation fails")
    
    args = parser.parse_args()
    
    if args.simulate_k8s:
        simulate_k8s_env()
    
    # Run config validation
    all_valid, results = validate_config()
    
    if args.json:
        # Add model compatibility check to JSON output
        model_valid, model_msg, model_severity = validate_model_max_len()
        output = {
            "valid": all_valid and model_valid,
            "config": results,
            "model_compatibility": {
                "valid": model_valid,
                "message": model_msg,
                "severity": model_severity
            }
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print_results(results)
        
        # Always check model/max_len compatibility
        print("-" * 70)
        print(color("Model Compatibility Check:", Colors.BOLD))
        print("-" * 70 + "\n")
        
        model_valid, model_msg, model_severity = validate_model_max_len()
        if model_severity == "error":
            print(color(f"✗ {model_msg}", Colors.RED))
            all_valid = False
        elif model_severity == "warning":
            print(color(f"⚠ {model_msg}", Colors.YELLOW))
        else:
            print(color(f"✓ {model_msg}", Colors.GREEN))
        print()
        
        # Additional validations if --full
        if args.full:
            print("-" * 70)
            print(color("Additional Validations:", Colors.BOLD))
            print("-" * 70 + "\n")
            
            # Proto files
            proto_ok, proto_msg = validate_proto_files()
            status = color("✓", Colors.GREEN) if proto_ok else color("✗", Colors.RED)
            print(f"{status} Proto files: {proto_msg}")
            all_valid = all_valid and proto_ok
            
            # Imports
            imports_ok, import_errors = validate_imports()
            status = color("✓", Colors.GREEN) if imports_ok else color("✗", Colors.RED)
            if imports_ok:
                print(f"{status} Required imports: All OK")
            else:
                print(f"{status} Required imports: FAILED")
                for err in import_errors:
                    print(f"   └── {color(err, Colors.RED)}")
            all_valid = all_valid and imports_ok
            
            # Check for vLLM (optional)
            try:
                import vllm
                print(color(f"✓ vLLM: Installed (version {vllm.__version__})", Colors.GREEN))
            except ImportError:
                print(color("⚠ vLLM: Not installed (required for model serving)", Colors.YELLOW))
            
            print()
        
        # Helm values comparison
        if args.helm_values:
            print("-" * 70)
            print(color("Helm Values Comparison:", Colors.BOLD))
            print("-" * 70 + "\n")
            
            helm_config = parse_helm_values(args.helm_values)
            if helm_config:
                for env_name, helm_value in helm_config.items():
                    current_value, source = get_config_value(env_name)
                    match = str(current_value) == str(helm_value)
                    status = color("✓", Colors.GREEN) if match else color("≠", Colors.YELLOW)
                    print(f"{status} {env_name}: current={current_value}, helm={helm_value}")
            print()
        
        # Summary
        print("=" * 70)
        if all_valid:
            print(color("✓ All validations passed!", Colors.GREEN))
        else:
            print(color("✗ Some validations failed. Check the errors above.", Colors.RED))
        print("=" * 70 + "\n")
    
    sys.exit(0 if all_valid or not args.strict else 1)


if __name__ == "__main__":
    main()
