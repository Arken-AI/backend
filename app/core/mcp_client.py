"""
MCP Client Wrapper

Manages connection to MCP servers via SSE (Server-Sent Events) transport.
Supports multiple MCP servers (process server + calculation engine server).

Key Features:
- SSE HTTP transport (connect to standalone MCP server services)
- Tool discovery and execution via MCP SDK ClientSession
- Supervisor pattern with health checks
- Connection state management
- Async locking for serialized tool calls
- Multi-server registry for routing tool calls

Architecture:
┌─────────────────────┐       SSE (HTTP)              ┌──────────────────────┐
│   FastAPI Backend   │ ←──────────────────────────→   │   MCP Process Server │
│  (MCP Clients)      │    GET /sse: event stream      │   (standalone svc)   │
│                     │    POST /messages/: requests    │                      │
│                     │                                 └──────────────────────┘
│                     │       SSE (HTTP)              ┌──────────────────────┐
│                     │ ←──────────────────────────→   │  MCP Calc Engine     │
│                     │                                │   (standalone svc)   │
└─────────────────────┘                                └──────────────────────┘
"""

import asyncio
import time
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from pathlib import Path

# Load .env file before any os.getenv() calls
from dotenv import load_dotenv

# Find the .env file (look in backend directory)
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    # Fallback: try current directory
    load_dotenv()

# MCP SDK imports for SSE client
from mcp import ClientSession
from mcp.client.sse import sse_client



# =============================================================================
# Data Models
# =============================================================================

class ConnectionState(str, Enum):
    """MCP connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"
    RESTARTING = "restarting"


@dataclass
class MCPTool:
    """MCP Tool metadata"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str = ""  # Which server this tool belongs to


@dataclass
class MCPServerConfig:
    """MCP server configuration (from .env) - SSE transport"""
    name: str  # Server identifier (e.g., "process_server", "calc_engine")
    url: str   # SSE endpoint URL (e.g., "http://localhost:8080/sse")
    enabled: bool = True
    
    @classmethod
    def from_env_process_server(cls) -> "MCPServerConfig":
        """Load MCP process server config from environment variables"""
        url = os.getenv("MCP_SERVER_URL", "http://localhost:8080/sse")
        enabled = os.getenv("MCP_PROCESS_SERVER_ENABLED", "true").lower() == "true"
        
        # If no URL configured, disable the server
        if not url:
            enabled = False
        
        return cls(
            name="process_server",
            url=url,
            enabled=enabled
        )
    
    @classmethod
    def from_env_calc_engine(cls) -> "MCPServerConfig":
        """Load MCP calculation engine server config from environment variables"""
        enabled = os.getenv("MCP_CALC_ENGINE_ENABLED", "true").lower() == "true"
        url = os.getenv("MCP_CALC_ENGINE_SERVER_URL", "")
        
        # If no URL configured, disable the server
        if not url:
            enabled = False
        
        return cls(
            name="calc_engine",
            url=url,
            enabled=enabled
        )
    
    @classmethod
    def from_env(cls) -> "MCPServerConfig":
        """Legacy method - returns process server config for backward compatibility"""
        return cls.from_env_process_server()


# =============================================================================
# MCP Client
# =============================================================================

class MCPClient:
    """
    MCP Client Wrapper - Manages connection to an MCP Server via SSE
    
    Usage:
        config = MCPServerConfig.from_env_process_server()
        client = MCPClient(config)
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("simulate_process", {...})
        await client.disconnect()
    """
    
    def __init__(
        self,
        config: MCPServerConfig,
        restart_delay: float = 2.0,
        max_retries: int = 3,
        health_check_interval: float = 30.0,
    ):
        self.config = config
        self.name = config.name
        self.restart_delay = restart_delay
        self.max_retries = max_retries
        self.health_check_interval = health_check_interval
        
        # Connection state
        self.state = ConnectionState.DISCONNECTED
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._health_task: Optional[asyncio.Task] = None
        self._tool_lock = asyncio.Lock()
        
        # Cached tools
        self._tools: Optional[List[MCPTool]] = None
        
        # Restart tracking
        self._restart_count = 0
        self._last_restart = 0.0
    
    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------
    
    async def connect(self) -> None:
        """
        Connect to MCP server via SSE transport.
        
        Raises:
            RuntimeError: If connection fails
        """
        if self.state == ConnectionState.CONNECTED:
            return
        
        self.state = ConnectionState.CONNECTING
        
        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()
            
            # Connect via SSE
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                sse_client(self.config.url)
            )
            
            # Create MCP client session
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize MCP protocol handshake
            await self._session.initialize()
            
            self.state = ConnectionState.CONNECTED
            
            # Start health checks
            self._health_task = asyncio.create_task(self._health_check_loop())
            
            print(f"INFO: MCP server '{self.name}' connected via SSE at {self.config.url}")
            
        except Exception as e:
            self.state = ConnectionState.FAILED
            print(f"ERROR: Failed to connect to MCP server '{self.name}' at {self.config.url}: {e}")
            await self._cleanup()
            raise RuntimeError(f"MCP SSE connection failed for '{self.name}': {e}")
    
    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server"""
        if self.state == ConnectionState.DISCONNECTED:
            return
        
        self.state = ConnectionState.DISCONNECTED
        await self._cleanup()
    
    async def _cleanup(self) -> None:
        """Clean up resources"""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                print(f"WARNING: Error closing MCP SSE connection: {e}")
            self._exit_stack = None
        
        self._session = None
        self._tools = None
    
    def is_connected(self) -> bool:
        """Check if MCP client is connected"""
        return self.state == ConnectionState.CONNECTED and self._session is not None
    
    # -------------------------------------------------------------------------
    # Tool Operations
    # -------------------------------------------------------------------------
    
    async def list_tools(self, force_refresh: bool = False) -> List[MCPTool]:
        """
        List available MCP tools.
        
        Args:
            force_refresh: Force refresh cached tools
        
        Returns:
            List of MCP tools
        """
        if self._tools and not force_refresh:
            return self._tools
        
        if not self.is_connected():
            raise RuntimeError(f"MCP client '{self.name}' not connected")
        
        result = await self._session.list_tools()
        
        tools = []
        for tool in result.tools:
            tools.append(MCPTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                server_name=self.name,
            ))
        
        self._tools = tools
        return tools
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call MCP tool.
        
        Args:
            name: Tool name
            arguments: Tool arguments
        
        Returns:
            Tool result
        
        Raises:
            RuntimeError: If tool call fails
        """
        if not self.is_connected():
            raise RuntimeError(f"MCP client '{self.name}' not connected")
        
        async with self._tool_lock:
            result = await self._session.call_tool(name, arguments)
            
            # Extract content
            if not result.content:
                return None
            
            # Return first text content
            for item in result.content:
                if hasattr(item, 'text'):
                    return item.text
            
            return str(result.content)
    
    # -------------------------------------------------------------------------
    # Health & Supervisor
    # -------------------------------------------------------------------------
    
    async def health_check(self) -> bool:
        """Perform health check by listing tools."""
        try:
            if not self.is_connected():
                return False
            await asyncio.wait_for(self._session.list_tools(), timeout=5.0)
            return True
        except Exception:
            return False
    
    async def _health_check_loop(self) -> None:
        """Background health check loop"""
        try:
            while self.state == ConnectionState.CONNECTED:
                await asyncio.sleep(self.health_check_interval)
                if not await self.health_check():
                    await self._handle_disconnect()
                    break
        except asyncio.CancelledError:
            pass
    
    async def _handle_disconnect(self) -> None:
        """Handle unexpected disconnect - attempt reconnect"""
        if self.state == ConnectionState.DISCONNECTED:
            return
        
        print(f"WARNING: MCP server '{self.name}' disconnected unexpectedly")
        
        now = time.time()
        if now - self._last_restart < 60:
            self._restart_count += 1
        else:
            self._restart_count = 1
        
        if self._restart_count > self.max_retries:
            self.state = ConnectionState.FAILED
            print(f"ERROR: Max reconnect attempts reached for '{self.name}' ({self.max_retries})")
            await self._cleanup()
            return
        
        self.state = ConnectionState.RESTARTING
        await self._cleanup()
        await asyncio.sleep(self.restart_delay)
        
        try:
            self._last_restart = now
            await self.connect()
            # Reset retry counter on successful reconnect
            self._restart_count = 0
            print(f"INFO: Successfully reconnected MCP server '{self.name}'")
        except Exception as e:
            self.state = ConnectionState.FAILED
            print(f"ERROR: Failed to reconnect MCP server '{self.name}': {e}")


# =============================================================================
# MCP Client Registry - Manages Multiple MCP Servers
# =============================================================================

class MCPClientRegistry:
    """
    Registry for managing multiple MCP client connections.
    
    Provides:
    - Centralized initialization of all MCP servers
    - Tool routing based on tool name prefix
    - Merged tool list for LLM
    - Health status for all servers
    
    Usage:
        registry = MCPClientRegistry()
        await registry.initialize()
        
        # Get merged tools from all servers
        tools = await registry.list_all_tools()
        
        # Call a tool (automatically routed to correct server)
        result = await registry.call_tool("calc_simulate_process", {...})
        
        await registry.shutdown()
    """
    
    # Tool prefix to server name mapping
    TOOL_ROUTING = {
        # Process server tools (sugar industry specific)
        "list_industries": "process_server",
        "list_processes": "process_server",
        "get_process": "process_server",
        "get_equipment_types": "process_server",
        "get_process_equipment_schema": "process_server",
        "get_stream_schema": "process_server",
        "validate_process_inputs": "process_server",
        "validate_process_connections": "process_server",
        "validate_equipment_inputs": "process_server",
        "simulate_process": "process_server",
        "simulate_equipment": "process_server",
        "get_process_run": "process_server",
        "compare_process_runs": "process_server",
        "list_process_runs": "process_server",
        # All other tools (including calc_* prefix) go to calc_engine (default)
    }
    
    DEFAULT_SERVER = "calc_engine"  # Default server for unmatched tools
    
    def __init__(self):
        """Initialize empty registry"""
        self._clients: Dict[str, MCPClient] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """
        Initialize all configured MCP servers.
        
        Loads configuration from environment and connects to enabled servers.
        """
        if self._initialized:
            return
        
        # Load process server config
        process_config = MCPServerConfig.from_env_process_server()
        if process_config.enabled and process_config.url:
            client = MCPClient(process_config)
            try:
                await client.connect()
                self._clients["process_server"] = client
                print(f"INFO: Process server connected with {len(await client.list_tools())} tools")
            except Exception as e:
                print(f"ERROR: Failed to connect process server: {e}")
        
        # Load calc engine config
        calc_config = MCPServerConfig.from_env_calc_engine()
        if calc_config.enabled and calc_config.url:
            client = MCPClient(calc_config)
            try:
                await client.connect()
                self._clients["calc_engine"] = client
                print(f"INFO: Calc engine server connected with {len(await client.list_tools())} tools")
            except Exception as e:
                print(f"WARNING: Failed to connect calc engine server: {e}")
                # Calc engine is optional, don't fail startup
        
        self._initialized = True
        print(f"INFO: MCP Registry initialized with {len(self._clients)} server(s)")
    
    async def shutdown(self) -> None:
        """Disconnect all MCP servers"""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
                print(f"INFO: Disconnected MCP server '{name}'")
            except Exception as e:
                print(f"WARNING: Error disconnecting '{name}': {e}")
        
        self._clients.clear()
        self._initialized = False
    
    def get_client(self, name: str) -> Optional[MCPClient]:
        """Get a specific MCP client by name"""
        return self._clients.get(name)
    
    def get_client_for_tool(self, tool_name: str) -> Optional[MCPClient]:
        """
        Get the appropriate MCP client for a tool based on naming convention.
        
        Routing rules:
        - Explicit process_server tools → process_server
        - All other tools (including calc_* prefix) → calc_engine (default)
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            MCPClient for the tool, or None if no matching server
        """
        # Check explicit tool routing
        if tool_name in self.TOOL_ROUTING:
            server_name = self.TOOL_ROUTING[tool_name]
            return self._clients.get(server_name)
        
        # Default to calc_engine server
        return self._clients.get(self.DEFAULT_SERVER)
    
    async def list_all_tools(self, force_refresh: bool = False) -> List[MCPTool]:
        """
        List tools from all connected servers.
        
        Returns merged list with server_name tagged on each tool.
        """
        all_tools = []
        
        for name, client in self._clients.items():
            if client.is_connected():
                try:
                    tools = await client.list_tools(force_refresh)
                    all_tools.extend(tools)
                except Exception as e:
                    print(f"WARNING: Failed to list tools from '{name}': {e}")
        
        return all_tools
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call a tool, automatically routing to the correct server.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            
        Returns:
            Tool result
            
        Raises:
            RuntimeError: If no server available for the tool
        """
        client = self.get_client_for_tool(tool_name)
        
        if not client:
            raise RuntimeError(f"No MCP server available for tool: {tool_name}")
        
        if not client.is_connected():
            raise RuntimeError(f"MCP server '{client.name}' is not connected")
        
        return await client.call_tool(tool_name, arguments)
    
    def get_health_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get health status of all registered servers.
        
        Returns:
            Dict mapping server name to status info
        """
        status = {}
        for name, client in self._clients.items():
            status[name] = {
                "connected": client.is_connected(),
                "state": client.state.value,
                "tools_count": len(client._tools) if client._tools else 0
            }
        return status
    
    @property
    def is_initialized(self) -> bool:
        """Check if registry has been initialized"""
        return self._initialized
    
    @property
    def connected_servers(self) -> List[str]:
        """List of connected server names"""
        return [name for name, client in self._clients.items() if client.is_connected()]
