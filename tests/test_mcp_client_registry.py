"""
Test MCP Client Registry - Verify multi-server connection management

Tests for the MCPClientRegistry that manages connections to multiple MCP servers:
- Process Server (mcp_process_server)
- Calculation Engine Server (mcp_calculation_engine_server)

Run with: pytest tests/test_mcp_client_registry.py -v
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.mcp_client import MCPClient, MCPServerConfig, MCPClientRegistry


class TestMCPServerConfig:
    """Tests for MCPServerConfig dataclass."""
    
    def test_from_env_with_defaults(self):
        """Test config creation with default values."""
        with patch.dict('os.environ', {
            'MCP_SERVER_COMMAND': '/usr/bin/python',
            'MCP_SERVER_ARGS': 'server.py',
        }, clear=True):
            config = MCPServerConfig.from_env()
            assert config.command == '/usr/bin/python'
            assert config.args == ['server.py']
            assert config.cwd is None
            assert config.enabled is True  # Default to True
    
    def test_from_env_with_all_fields(self):
        """Test config creation with all fields specified."""
        with patch.dict('os.environ', {
            'MCP_SERVER_COMMAND': '/usr/bin/python',
            'MCP_SERVER_ARGS': 'server.py,--debug',  # Comma-separated, not space
            'MCP_SERVER_ENV_CALC_ENGINE_URL': 'http://localhost:8000',
        }, clear=True):
            config = MCPServerConfig.from_env()
            assert config.command == '/usr/bin/python'
            assert config.args == ['server.py', '--debug']  # Split by comma
            assert config.enabled is True  # Always True for process server
            assert config.env.get('CALC_ENGINE_URL') == 'http://localhost:8000'
    
    def test_disabled_config_calc_engine(self):
        """Test calc engine config with enabled=False."""
        with patch.dict('os.environ', {
            'MCP_CALC_ENGINE_COMMAND': '/usr/bin/python',
            'MCP_CALC_ENGINE_ARGS': 'server.py',
            'MCP_CALC_ENGINE_ENABLED': 'false',
        }, clear=True):
            config = MCPServerConfig.from_env_calc_engine()
            assert config.enabled is False


class TestMCPClientRegistry:
    """Tests for MCPClientRegistry multi-server management."""
    
    def test_registry_initialization_empty(self):
        """Test registry starts with no clients."""
        registry = MCPClientRegistry()
        assert len(registry._clients) == 0
        assert not registry.is_initialized
    
    def test_get_client_returns_none_when_empty(self):
        """Test getting a specific client when registry is empty."""
        registry = MCPClientRegistry()
        
        client = registry.get_client("process_server")
        assert client is None
        
        client = registry.get_client("calc_engine")
        assert client is None
        
        # Non-existent client
        client = registry.get_client("non_existent")
        assert client is None
    
    def test_connected_servers_empty(self):
        """Test getting connected server names when empty."""
        registry = MCPClientRegistry()
        servers = registry.connected_servers
        assert isinstance(servers, list)
        assert len(servers) == 0


class TestToolRouting:
    """Tests for tool routing logic."""
    
    @pytest.fixture
    def registry(self) -> MCPClientRegistry:
        """Create empty registry for routing tests."""
        return MCPClientRegistry()
    
    def test_route_process_server_tools(self, registry):
        """Test routing for process server tools."""
        # Process server tools
        process_tools = [
            "list_industries",
            "list_processes",
            "validate_process_inputs",
            "simulate_process",
            "list_process_runs"
        ]
        
        for tool in process_tools:
            client = registry.get_client_for_tool(tool)
            # Client may be None in unit test since not connected, but check routing works
            # By checking the TOOL_ROUTING dict directly
            if tool in registry.TOOL_ROUTING:
                assert registry.TOOL_ROUTING[tool] == "process_server", f"Tool '{tool}' should route to process_server"
    
    def test_route_calc_engine_tools(self, registry):
        """Test routing for calc engine tools."""
        # Calc engine tools (with calc_ prefix) - these go to default server
        calc_tools = [
            "calc_list_processes",
            "calc_get_process",
            "calc_simulate_process",
            "calc_get_run",
            "calc_list_runs"
        ]
        
        for tool in calc_tools:
            # These are not in TOOL_ROUTING, so they go to DEFAULT_SERVER
            assert tool not in registry.TOOL_ROUTING, f"Tool '{tool}' should not be in process routing"
            assert registry.DEFAULT_SERVER == "calc_engine"
    
    def test_route_parameter_tools(self, registry):
        """Test routing for parameter editing tools (calc engine)."""
        param_tools = [
            "get_editable_parameters",
            "validate_parameters",
            "edit_parameters",
            "get_user_parameters",
            "switch_parameter_version",
            "compare_parameters"
        ]
        
        for tool in param_tools:
            # These are not in TOOL_ROUTING, so they go to DEFAULT_SERVER (calc_engine)
            assert tool not in registry.TOOL_ROUTING, f"Tool '{tool}' should not be in process routing"
        assert registry.DEFAULT_SERVER == "calc_engine"
    
    def test_default_routing(self, registry):
        """Test default routing for unknown tools."""
        # Unknown tools should default to calc_engine (as per requirement)
        unknown_tools = ["unknown_tool", "new_feature", "custom_tool"]
        
        for tool in unknown_tools:
            # Unknown tools are not in TOOL_ROUTING, so DEFAULT_SERVER is used
            assert tool not in registry.TOOL_ROUTING
        assert registry.DEFAULT_SERVER == "calc_engine"


class TestMCPClientRegistryUnit:
    """Unit tests for MCPClientRegistry methods (without actual connections)."""
    
    def test_tool_routing_dict_exists(self):
        """Test TOOL_ROUTING dict is properly configured."""
        registry = MCPClientRegistry()
        
        # Check key process server tools are routed
        assert registry.TOOL_ROUTING.get("list_industries") == "process_server"
        assert registry.TOOL_ROUTING.get("simulate_process") == "process_server"
        assert registry.TOOL_ROUTING.get("validate_process_inputs") == "process_server"
    
    def test_default_server_is_calc_engine(self):
        """Test default server is calc_engine."""
        registry = MCPClientRegistry()
        assert registry.DEFAULT_SERVER == "calc_engine"
    
    def test_not_initialized_by_default(self):
        """Test registry is not initialized by default."""
        registry = MCPClientRegistry()
        assert not registry.is_initialized
        assert len(registry._clients) == 0
    
    def test_get_client_empty_registry(self):
        """Test get_client returns None when registry is empty."""
        registry = MCPClientRegistry()
        
        assert registry.get_client("process_server") is None
        assert registry.get_client("calc_engine") is None
    
    def test_get_client_for_tool_empty_registry(self):
        """Test get_client_for_tool returns None when registry is empty."""
        registry = MCPClientRegistry()
        
        # Even with correct tool name, returns None if no client
        assert registry.get_client_for_tool("list_industries") is None
        assert registry.get_client_for_tool("calc_simulate_process") is None
    
    def test_connected_servers_empty(self):
        """Test connected_servers is empty initially."""
        registry = MCPClientRegistry()
        assert registry.connected_servers == []
    
    def test_get_health_status_empty(self):
        """Test health status returns empty dict for empty registry."""
        registry = MCPClientRegistry()
        status = registry.get_health_status()
        assert status == {}


class TestMCPClientRegistryWithMocks:
    """Tests with mocked clients for integration testing."""
    
    @pytest.mark.asyncio
    async def test_list_all_tools_with_mocked_clients(self):
        """Test listing tools when clients are mocked."""
        registry = MCPClientRegistry()
        
        # Create mock clients and inject into registry
        mock_process_client = MagicMock()
        mock_process_client.is_connected = MagicMock(return_value=True)
        mock_process_client.list_tools = AsyncMock(return_value=[
            MCPTool(name="list_industries", description="List industries", input_schema={}, server_name="process_server"),
            MCPTool(name="simulate_process", description="Run simulation", input_schema={}, server_name="process_server")
        ])
        
        mock_calc_client = MagicMock()
        mock_calc_client.is_connected = MagicMock(return_value=True)
        mock_calc_client.list_tools = AsyncMock(return_value=[
            MCPTool(name="calc_simulate_process", description="Run calc simulation", input_schema={}, server_name="calc_engine"),
            MCPTool(name="get_editable_parameters", description="Get params", input_schema={}, server_name="calc_engine")
        ])
        
        # Inject mocked clients
        registry._clients["process_server"] = mock_process_client
        registry._clients["calc_engine"] = mock_calc_client
        
        all_tools = await registry.list_all_tools()
        
        assert len(all_tools) == 4
    
    @pytest.mark.asyncio
    async def test_call_tool_routes_to_process_server(self):
        """Test call_tool routes process server tools correctly."""
        registry = MCPClientRegistry()
        
        # Create and inject mock client
        mock_process_client = MagicMock()
        mock_process_client.is_connected = MagicMock(return_value=True)
        mock_process_client.call_tool = AsyncMock(return_value={"result": "process_result"})
        
        registry._clients["process_server"] = mock_process_client
        
        result = await registry.call_tool("list_industries", {})
        
        assert result == {"result": "process_result"}
        mock_process_client.call_tool.assert_called_once_with("list_industries", {})
    
    @pytest.mark.asyncio
    async def test_call_tool_routes_to_calc_engine(self):
        """Test call_tool routes calc engine tools correctly."""
        registry = MCPClientRegistry()
        
        # Create and inject mock client
        mock_calc_client = MagicMock()
        mock_calc_client.is_connected = MagicMock(return_value=True)
        mock_calc_client.call_tool = AsyncMock(return_value={"result": "calc_result"})
        
        registry._clients["calc_engine"] = mock_calc_client
        
        result = await registry.call_tool("calc_simulate_process", {"params": {}})
        
        assert result == {"result": "calc_result"}
        mock_calc_client.call_tool.assert_called_once_with("calc_simulate_process", {"params": {}})
    
    @pytest.mark.asyncio
    async def test_shutdown_disconnects_all(self):
        """Test shutdown disconnects all clients."""
        registry = MCPClientRegistry()
        
        # Create and inject mock clients
        mock_process_client = MagicMock()
        mock_process_client.disconnect = AsyncMock()
        
        mock_calc_client = MagicMock()
        mock_calc_client.disconnect = AsyncMock()
        
        registry._clients["process_server"] = mock_process_client
        registry._clients["calc_engine"] = mock_calc_client
        registry._initialized = True
        
        await registry.shutdown()
        
        mock_process_client.disconnect.assert_called_once()
        mock_calc_client.disconnect.assert_called_once()
        assert not registry._initialized
        assert len(registry._clients) == 0


# Import MCPTool for mocking
from app.core.mcp_client import MCPTool


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
