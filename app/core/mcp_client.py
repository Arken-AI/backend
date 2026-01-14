"""
MCP Client Wrapper

Manages connection to MCP Process Server via stdio transport.
Replicates Claude Desktop's MCP client behavior.

Key Features:
- Subprocess management (launch, monitor, restart)
- Stdio transport (JSON-RPC over stdin/stdout)
- Tool discovery and execution
- Supervisor pattern with health checks
- Connection state management
- Async locking for serialized tool calls

Architecture:
┌─────────────────────┐         stdio (JSON-RPC)        ┌──────────────────────┐
│   FastAPI Backend   │ ←──────────────────────────────→ │   MCP Process Server │
│  (This Client)      │    stdin: send requests         │   (subprocess)        │
│                     │    stdout: receive responses    │                      │
└─────────────────────┘                                  └──────────────────────┘
"""

import asyncio
import json
import traceback
import os
import time
from asyncio import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional



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


@dataclass
class MCPServerConfig:
    """MCP server configuration (from .env)"""
    command: str
    args: List[str]
    env: Dict[str, str]
    
    @classmethod
    def from_env(cls) -> "MCPServerConfig":
        """Load MCP server config from environment variables"""
        command = os.getenv("MCP_SERVER_COMMAND")
        args_str = os.getenv("MCP_SERVER_ARGS", "")
        
        # Parse args (single path or comma-separated)
        args = [args_str] if args_str and "," not in args_str else args_str.split(",")
        
        # Parse environment variables (MCP_SERVER_ENV_*)
        env = {}
        for key, value in os.environ.items():
            if key.startswith("MCP_SERVER_ENV_"):
                env_key = key.replace("MCP_SERVER_ENV_", "")
                env[env_key] = value
        
        return cls(command=command, args=args, env=env)


# =============================================================================
# MCP Client
# =============================================================================

class MCPClient:
    """
    MCP Client Wrapper - Manages connection to MCP Process Server
    
    Usage:
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
        """
        Initialize MCP client
        
        Args:
            config: MCP server configuration
            restart_delay: Seconds to wait before restart
            max_retries: Max restart attempts
            health_check_interval: Seconds between health checks
        """
        self.config = config
        self.restart_delay = restart_delay
        self.max_retries = max_retries
        self.health_check_interval = health_check_interval
        
        # Connection state
        self.state = ConnectionState.DISCONNECTED
        self._process: Optional[subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        
        # Message handling
        self._message_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._tool_lock = asyncio.Lock()  # Serialize tool calls
        
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
        Connect to MCP server (launch subprocess)
        
        Raises:
            RuntimeError: If connection fails
        """
        if self.state == ConnectionState.CONNECTED:
            return
        
        self.state = ConnectionState.CONNECTING
        
        try:
            # Prepare environment
            env = os.environ.copy()
            env.update(self.config.env)
            
            # Launch MCP server subprocess
            self._process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            
            # Start reading stdout
            self._reader_task = asyncio.create_task(self._read_loop())
            
            # Set connected state BEFORE protocol init (so _send_request works)
            self.state = ConnectionState.CONNECTED
            
            # Initialize MCP protocol
            await self._initialize_protocol()
            
            # Start health checks
            self._health_task = asyncio.create_task(self._health_check_loop())
            
        except Exception as e:
            self.state = ConnectionState.FAILED
            print(f"ERROR: Failed to connect to MCP server: {e}")
            await self._cleanup()
            raise RuntimeError(f"MCP connection failed: {e}")
    
    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server"""
        if self.state == ConnectionState.DISCONNECTED:
            return
        
        self.state = ConnectionState.DISCONNECTED
        
        await self._cleanup()
    
    async def _cleanup(self) -> None:
        """Clean up resources"""
        # Cancel health checks
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        
        # Cancel reader
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        
        # Terminate process
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                print(f"WARNING: Error terminating MCP process: {e}")
        
        # Clear state
        self._process = None
        self._reader_task = None
        self._health_task = None
        self._tools = None
        
        # Reject pending requests
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RuntimeError("MCP connection closed"))
        self._pending_requests.clear()
    
    def is_connected(self) -> bool:
        """Check if MCP client is connected"""
        return self.state == ConnectionState.CONNECTED and self._process is not None
    
    # -------------------------------------------------------------------------
    # Protocol Operations
    # -------------------------------------------------------------------------
    
    async def _initialize_protocol(self) -> None:
        """Initialize MCP protocol (handshake)"""
        # Send initialize request
        response = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": False},
                "sampling": {}
            },
            "clientInfo": {
                "name": "arken-chat-backend",
                "version": "1.0.0"
            }
        })
        
        
        # Send initialized notification
        await self._send_notification("notifications/initialized")
    
    async def _send_request(self, method: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Send JSON-RPC request and wait for response
        
        Args:
            method: JSON-RPC method name
            params: Method parameters
        
        Returns:
            Response result
        
        Raises:
            RuntimeError: If not connected or request fails
        """
        if not self.is_connected():
            raise RuntimeError("MCP client not connected")
        
        # Generate message ID
        self._message_id += 1
        msg_id = self._message_id
        
        # Create request
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params:
            request["params"] = params
        
        # Create future for response
        future = asyncio.Future()
        self._pending_requests[msg_id] = future
        
        # Send request
        try:
            request_json = json.dumps(request) + "\n"
            self._process.stdin.write(request_json.encode())
            await self._process.stdin.drain()
            
            # Wait for response (with timeout)
            response = await asyncio.wait_for(future, timeout=300.0)
            
            # Check for error
            if "error" in response:
                error = response["error"]
                raise RuntimeError(f"MCP error: {error.get('message', 'Unknown error')}")
            
            return response.get("result", {})
            
        except asyncio.TimeoutError:
            self._pending_requests.pop(msg_id, None)
            raise RuntimeError(f"MCP request timeout: {method}")
        except Exception as e:
            self._pending_requests.pop(msg_id, None)
            raise RuntimeError(f"MCP request failed: {e}")
    
    async def _send_notification(self, method: str, params: Optional[Dict] = None) -> None:
        """Send JSON-RPC notification (no response expected)"""
        if not self.is_connected():
            raise RuntimeError("MCP client not connected")
        
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            notification["params"] = params
        
        notification_json = json.dumps(notification) + "\n"
        self._process.stdin.write(notification_json.encode())
        await self._process.stdin.drain()
    
    async def _read_loop(self) -> None:
        """Read and process messages from MCP server stdout"""
        try:
            while self._process and self._process.stdout:
                line = await self._process.stdout.readline()
                if not line:
                    break
                
                try:
                    message = json.loads(line.decode())
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    print(f"ERROR: Invalid JSON from MCP server: {e}")
                except Exception as e:
                    print(f"ERROR: Error handling MCP message: {e}")
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"ERROR: MCP reader loop error: {e}")
            if self.state == ConnectionState.CONNECTED:
                await self._handle_disconnect()
    
    async def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming message from MCP server"""
        # Response to request
        if "id" in message:
            msg_id = message["id"]
            future = self._pending_requests.pop(msg_id, None)
            if future and not future.done():
                future.set_result(message)
        
        # Notification (log progress, etc.)
        elif "method" in message:
            pass
    
    # -------------------------------------------------------------------------
    # Tool Operations
    # -------------------------------------------------------------------------
    
    async def list_tools(self, force_refresh: bool = False) -> List[MCPTool]:
        """
        List available MCP tools
        
        Args:
            force_refresh: Force refresh cached tools
        
        Returns:
            List of MCP tools
        """
        if self._tools and not force_refresh:
            return self._tools
        
        response = await self._send_request("tools/list")
        
        tools = []
        for tool_data in response.get("tools", []):
            tools.append(MCPTool(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
            ))
        
        self._tools = tools
        
        return tools
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call MCP tool
        
        Args:
            name: Tool name
            arguments: Tool arguments
        
        Returns:
            Tool result
        
        Raises:
            RuntimeError: If tool call fails
        """
        # Use lock to serialize tool calls (MCP servers typically aren't thread-safe)
        async with self._tool_lock:
            response = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments
            })
            
            # Extract content
            content = response.get("content", [])
            if not content:
                return None
            
            # Return first text content
            for item in content:
                if item.get("type") == "text":
                    return item.get("text")
            
            return content
    
    # -------------------------------------------------------------------------
    # Health & Supervisor
    # -------------------------------------------------------------------------
    
    async def health_check(self) -> bool:
        """
        Perform health check
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            # Check process alive
            if not self._process or self._process.returncode is not None:
                return False
            
            # Try to list tools (lightweight operation)
            await asyncio.wait_for(self.list_tools(), timeout=5.0)
            return True
            
        except Exception as e:
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
        """Handle unexpected disconnect - attempt restart"""
        if self.state == ConnectionState.DISCONNECTED:
            return
        
        print(f"WARNING: MCP server disconnected unexpectedly")
        # Check restart limits
        now = time.time()
        if now - self._last_restart < 60:  # Within 1 minute
            self._restart_count += 1
        else:
            self._restart_count = 1
        
        if self._restart_count > self.max_retries:
            self.state = ConnectionState.FAILED
            print(f"ERROR: Max restart attempts reached ({self.max_retries})")
            await self._cleanup()
            return
        
        # Attempt restart
        self.state = ConnectionState.RESTARTING
        
        await self._cleanup()
        await asyncio.sleep(self.restart_delay)
        
        try:
            self._last_restart = now
            await self.connect()
        except Exception as e:
            self.state = ConnectionState.FAILED
            print(f"ERROR: Failed to restart MCP server: {e}")
