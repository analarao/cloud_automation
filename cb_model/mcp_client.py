#!/usr/bin/env python3
"""
MCP Client for CB Model
=======================

This module provides a Python interface to the mcp-server-kubernetes MCP server.
It handles:
1. Starting the MCP server as a subprocess
2. Communicating via stdio (MCP protocol)
3. Converting between OpenAI tool call format and MCP format
4. Executing tools and returning results

Phase 2: MCP Server Integration

Usage:
    from mcp_client import MCPKubernetesClient
    
    async with MCPKubernetesClient() as client:
        # List available tools
        tools = await client.list_tools()
        
        # Execute a tool
        result = await client.call_tool("kubectl_get", {
            "resource_type": "pods",
            "namespace": "target-services"
        })
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

# MCP SDK imports
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("Warning: MCP SDK not available. Install with: pip install mcp")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("mcp_client")


@dataclass
class MCPTool:
    """Represents an MCP tool definition."""
    name: str
    description: str
    parameters: Dict[str, Any]
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


@dataclass
class ToolCallResult:
    """Result of executing a tool."""
    tool_name: str
    success: bool
    result: Any
    error: Optional[str] = None


class MCPKubernetesClient:
    """
    Client for mcp-server-kubernetes.
    
    Manages the MCP server subprocess and provides async methods
    for tool discovery and execution.
    """
    
    def __init__(
        self,
        target_namespace: str = None,
        non_destructive: bool = None
    ):
        """
        Initialize MCP client.
        
        Args:
            target_namespace: Kubernetes namespace to target (default from env)
            non_destructive: If True, disable destructive operations
        """
        self.target_namespace = target_namespace or os.getenv("MCP_TARGET_NAMESPACE", "target-services")
        self.non_destructive = non_destructive if non_destructive is not None else \
            os.getenv("MCP_NON_DESTRUCTIVE", "false").lower() == "true"
        
        self._session: Optional[ClientSession] = None
        self._tools: List[MCPTool] = []
        self._initialized = False
        
        logger.info(f"MCP Client initialized")
        logger.info(f"  Target namespace: {self.target_namespace}")
        logger.info(f"  Non-destructive mode: {self.non_destructive}")
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
    
    async def connect(self):
        """Connect to the MCP server."""
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP SDK not available. Install with: pip install mcp")
        
        logger.info("Connecting to mcp-server-kubernetes...")
        
        # Build environment for MCP server
        env = os.environ.copy()
        if self.non_destructive:
            env["ALLOW_ONLY_NON_DESTRUCTIVE_TOOLS"] = "true"
        
        # Server parameters for mcp-server-kubernetes
        server_params = StdioServerParameters(
            command="npx",
            args=["mcp-server-kubernetes"],
            env=env
        )
        
        # Connect to the server
        # Note: The actual connection is managed by the context manager in execute methods
        self._server_params = server_params
        self._initialized = True
        logger.info("MCP Client ready")
    
    async def disconnect(self):
        """Disconnect from the MCP server."""
        self._session = None
        self._initialized = False
        logger.info("MCP Client disconnected")
    
    async def list_tools(self) -> List[MCPTool]:
        """
        List available tools from the MCP server.
        
        Returns:
            List of MCPTool objects
        """
        if not self._initialized:
            await self.connect()
        
        tools = []
        
        async with stdio_client(self._server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # Get tools from server
                tools_response = await session.list_tools()
                
                for tool in tools_response.tools:
                    mcp_tool = MCPTool(
                        name=tool.name,
                        description=tool.description or "",
                        parameters=tool.inputSchema if hasattr(tool, 'inputSchema') else {}
                    )
                    tools.append(mcp_tool)
        
        self._tools = tools
        logger.info(f"Discovered {len(tools)} MCP tools")
        return tools
    
    async def get_tools_openai_format(self) -> List[Dict[str, Any]]:
        """
        Get tools in OpenAI function calling format.
        
        Returns:
            List of tool definitions in OpenAI format
        """
        if not self._tools:
            await self.list_tools()
        
        return [tool.to_openai_format() for tool in self._tools]
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolCallResult:
        """
        Execute a tool on the MCP server.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool
            
        Returns:
            ToolCallResult with the outcome
        """
        if not self._initialized:
            await self.connect()
        
        # Auto-inject namespace if not provided (critical for kubectl_scale and other tools)
        if self.target_namespace and "namespace" not in arguments:
            arguments["namespace"] = self.target_namespace
            logger.debug(f"Auto-injected namespace: {self.target_namespace}")
        
        logger.info(f"Calling tool: {tool_name}")
        logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")
        
        try:
            async with stdio_client(self._server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    
                    # Call the tool
                    result = await session.call_tool(tool_name, arguments)
                    
                    # Extract content from result
                    if hasattr(result, 'content') and result.content:
                        # MCP returns content as a list of content blocks
                        content_text = ""
                        for block in result.content:
                            if hasattr(block, 'text'):
                                content_text += block.text
                        
                        return ToolCallResult(
                            tool_name=tool_name,
                            success=True,
                            result=content_text
                        )
                    else:
                        return ToolCallResult(
                            tool_name=tool_name,
                            success=True,
                            result=str(result)
                        )
        
        except ExceptionGroup as eg:
            # Handle TaskGroup/ExceptionGroup errors (Python 3.11+)
            error_messages = []
            for exc in eg.exceptions:
                error_messages.append(f"{type(exc).__name__}: {exc}")
            full_error = "; ".join(error_messages)
            logger.error(f"Tool call failed (ExceptionGroup): {full_error}")
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                result=None,
                error=full_error
            )
                        
        except Exception as e:
            logger.error(f"Tool call failed: {e}")
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                result=None,
                error=str(e)
            )
    
    async def execute_openai_tool_call(self, tool_call) -> Dict[str, Any]:
        """
        Execute a tool call from OpenAI format.
        
        Args:
            tool_call: OpenAI tool call object with:
                - function.name: Tool name
                - function.arguments: JSON string of arguments
                - id: Tool call ID
        
        Returns:
            Dict with tool result for sending back to LLM
        """
        tool_name = tool_call.function.name
        
        # Parse arguments (OpenAI sends them as JSON string)
        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            arguments = {}
        
        # Execute the tool
        result = await self.call_tool(tool_name, arguments)
        
        # Format for OpenAI
        if result.success:
            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result.result if isinstance(result.result, str) else json.dumps(result.result)
            }
        else:
            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": f"Error: {result.error}"
            }


# Synchronous wrapper for non-async code
class MCPKubernetesClientSync:
    """Synchronous wrapper for MCPKubernetesClient."""
    
    def __init__(self, **kwargs):
        self._async_client = MCPKubernetesClient(**kwargs)
        self._loop = None
    
    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop
    
    def connect(self):
        return self._get_loop().run_until_complete(self._async_client.connect())
    
    def disconnect(self):
        return self._get_loop().run_until_complete(self._async_client.disconnect())
    
    def list_tools(self) -> List[MCPTool]:
        return self._get_loop().run_until_complete(self._async_client.list_tools())
    
    def get_tools_openai_format(self) -> List[Dict[str, Any]]:
        return self._get_loop().run_until_complete(self._async_client.get_tools_openai_format())
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolCallResult:
        return self._get_loop().run_until_complete(self._async_client.call_tool(tool_name, arguments))
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


# Test function
async def test_mcp_client():
    """Test the MCP client."""
    print("=" * 60)
    print("MCP Kubernetes Client Test")
    print("=" * 60)
    
    async with MCPKubernetesClient() as client:
        # List tools
        print("\n1. Listing available tools...")
        tools = await client.list_tools()
        print(f"   Found {len(tools)} tools:")
        for tool in tools[:10]:  # Show first 10
            print(f"   - {tool.name}: {tool.description[:50]}...")
        
        # Get OpenAI format
        print("\n2. Converting to OpenAI format...")
        openai_tools = await client.get_tools_openai_format()
        print(f"   Generated {len(openai_tools)} OpenAI tool definitions")
        
        # Test a simple tool call
        print("\n3. Testing kubectl_get...")
        result = await client.call_tool("kubectl_get", {
            "resource_type": "pods",
            "namespace": os.getenv("MCP_TARGET_NAMESPACE", "default")
        })
        
        if result.success:
            print(f"   ✓ Success!")
            print(f"   Result preview: {str(result.result)[:200]}...")
        else:
            print(f"   ✗ Failed: {result.error}")
    
    print("\n" + "=" * 60)
    print("Test complete!")


if __name__ == "__main__":
    if not MCP_AVAILABLE:
        print("MCP SDK not installed. Run: pip install mcp")
        sys.exit(1)
    
    asyncio.run(test_mcp_client())
