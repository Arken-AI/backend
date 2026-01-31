"""
Test Context Manager - Dual Server Support

Tests for Context Manager with multi-server run tracking:
- Process Server runs (mcp_process_server)  
- Calculation Engine runs (mcp_calculation_engine_server)

Run with: pytest tests/test_context_manager_dual_server.py -v
"""
import pytest
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.context_manager import ContextManager


class MockRedis:
    """Mock Redis client for testing."""
    
    def __init__(self):
        self._store = {}
    
    async def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value
    
    async def get(self, key: str):
        return self._store.get(key)
    
    async def delete(self, key: str):
        self._store.pop(key, None)


class MockMongoCollection:
    """Mock MongoDB collection for testing."""
    
    def __init__(self):
        self._store = {}
    
    async def find_one(self, filter_dict: Dict):
        conv_id = filter_dict.get("conversation_id")
        doc = self._store.get(conv_id)
        if doc:
            return {**doc, "_id": "mock_id"}
        return None
    
    async def update_one(self, filter_dict: Dict, update: Dict, upsert: bool = False):
        conv_id = filter_dict.get("conversation_id")
        if "$set" in update:
            self._store[conv_id] = update["$set"]
    
    async def delete_one(self, filter_dict: Dict):
        conv_id = filter_dict.get("conversation_id")
        self._store.pop(conv_id, None)


class MockMongoClient:
    """Mock MongoDB client for testing."""
    
    def __init__(self):
        self._collections = {}
    
    def __getitem__(self, db_name: str):
        return self
    
    def __getattr__(self, collection_name: str):
        if collection_name not in self._collections:
            self._collections[collection_name] = MockMongoCollection()
        return self._collections[collection_name]


@pytest.fixture
def context_manager():
    """Create a ContextManager with mocked dependencies."""
    redis_client = MockRedis()
    mongo_client = MockMongoClient()
    return ContextManager(redis_client, mongo_client)


class TestDualServerContextCreation:
    """Test context creation with dual-server support."""
    
    @pytest.mark.asyncio
    async def test_create_context_has_runs_by_server(self, context_manager):
        """Test that new context includes runs_by_server structure."""
        context = await context_manager.create_context("conv_001", user_id="user_123")
        
        assert "runs_by_server" in context
        assert context["runs_by_server"] == {
            "process_server": [],
            "calc_engine": []
        }
    
    @pytest.mark.asyncio
    async def test_create_context_has_active_server(self, context_manager):
        """Test that new context includes active_server field."""
        context = await context_manager.create_context("conv_002", user_id="user_123")
        
        assert "active_server" in context
        assert context["active_server"] is None  # Default to None
    
    @pytest.mark.asyncio
    async def test_create_context_backward_compat_run_ids(self, context_manager):
        """Test that new context maintains backward-compatible run_ids."""
        context = await context_manager.create_context("conv_003", user_id="user_123")
        
        # Legacy field should still exist
        assert "run_ids" in context
        assert context["run_ids"] == []


class TestActiveServerTracking:
    """Test active server tracking functionality."""
    
    @pytest.mark.asyncio
    async def test_update_mcp_server_process(self, context_manager):
        """Test setting active server to process_server."""
        await context_manager.create_context("conv_010", user_id="user_123")
        
        await context_manager.update_mcp_server("conv_010", "process_server")
        
        context = await context_manager.get_context("conv_010")
        assert context["active_server"] == "process_server"
    
    @pytest.mark.asyncio
    async def test_update_mcp_server_calc_engine(self, context_manager):
        """Test setting active server to calc_engine."""
        await context_manager.create_context("conv_011", user_id="user_123")
        
        await context_manager.update_mcp_server("conv_011", "calc_engine")
        
        context = await context_manager.get_context("conv_011")
        assert context["active_server"] == "calc_engine"
    
    @pytest.mark.asyncio
    async def test_update_mcp_server_legacy_process(self, context_manager):
        """Test legacy 'process' value normalizes to 'process_server'."""
        await context_manager.create_context("conv_012", user_id="user_123")
        
        await context_manager.update_mcp_server("conv_012", "process")
        
        context = await context_manager.get_context("conv_012")
        assert context["active_server"] == "process_server"
    
    @pytest.mark.asyncio
    async def test_update_mcp_server_invalid_raises(self, context_manager):
        """Test that invalid server name raises ValueError."""
        await context_manager.create_context("conv_013", user_id="user_123")
        
        with pytest.raises(ValueError) as excinfo:
            await context_manager.update_mcp_server("conv_013", "invalid_server")
        
        assert "Invalid MCP server" in str(excinfo.value)


class TestRunTracking:
    """Test run tracking by server."""
    
    @pytest.mark.asyncio
    async def test_add_process_server_run(self, context_manager):
        """Test adding a run from process server."""
        await context_manager.create_context("conv_020", user_id="user_123")
        
        # Simulate process server tool execution
        await context_manager.add_tool_execution("conv_020", "simulate_process", {
            "status": "success",
            "run_id": "run_process_001",
            "server": "arken-process-simulator"  # Process server identifier
        })
        
        context = await context_manager.get_context("conv_020")
        
        # Check runs_by_server
        assert "run_process_001" in context["runs_by_server"]["process_server"]
        assert "run_process_001" not in context["runs_by_server"]["calc_engine"]
        
        # Check legacy run_ids
        assert "run_process_001" in context["run_ids"]
        
        # Check last_run_id
        assert context["last_run_id"] == "run_process_001"
    
    @pytest.mark.asyncio
    async def test_add_calc_engine_run(self, context_manager):
        """Test adding a run from calc engine server."""
        await context_manager.create_context("conv_021", user_id="user_123")
        
        # Simulate calc engine tool execution
        await context_manager.add_tool_execution("conv_021", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_calc_001",
            "server": "arken-calculation-engine"  # Calc engine identifier
        })
        
        context = await context_manager.get_context("conv_021")
        
        # Check runs_by_server
        assert "run_calc_001" in context["runs_by_server"]["calc_engine"]
        assert "run_calc_001" not in context["runs_by_server"]["process_server"]
        
        # Check legacy run_ids
        assert "run_calc_001" in context["run_ids"]
    
    @pytest.mark.asyncio
    async def test_mixed_server_runs(self, context_manager):
        """Test tracking runs from both servers."""
        await context_manager.create_context("conv_022", user_id="user_123")
        
        # Add process server run
        await context_manager.add_tool_execution("conv_022", "simulate_process", {
            "status": "success",
            "run_id": "run_process_001",
            "server": "arken-process-simulator"
        })
        
        # Add calc engine run
        await context_manager.add_tool_execution("conv_022", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_calc_001",
            "server": "arken-calculation-engine"
        })
        
        # Add another calc engine run
        await context_manager.add_tool_execution("conv_022", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_calc_002",
            "server": "arken-calculation-engine"
        })
        
        context = await context_manager.get_context("conv_022")
        
        # Check runs_by_server
        assert len(context["runs_by_server"]["process_server"]) == 1
        assert len(context["runs_by_server"]["calc_engine"]) == 2
        
        # Check order (newest first)
        assert context["runs_by_server"]["calc_engine"][0] == "run_calc_002"
        assert context["runs_by_server"]["calc_engine"][1] == "run_calc_001"
        
        # Check legacy run_ids (all runs)
        assert len(context["run_ids"]) == 3
    
    @pytest.mark.asyncio
    async def test_run_deduplication(self, context_manager):
        """Test that duplicate run IDs are not added."""
        await context_manager.create_context("conv_023", user_id="user_123")
        
        # Add same run twice
        await context_manager.add_tool_execution("conv_023", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_calc_dup",
            "server": "arken-calculation-engine"
        })
        
        await context_manager.add_tool_execution("conv_023", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_calc_dup",  # Same run ID
            "server": "arken-calculation-engine"
        })
        
        context = await context_manager.get_context("conv_023")
        
        # Should only have one instance
        assert context["runs_by_server"]["calc_engine"].count("run_calc_dup") == 1
        assert context["run_ids"].count("run_calc_dup") == 1
    
    @pytest.mark.asyncio
    async def test_run_limit_per_server(self, context_manager):
        """Test that runs are limited to 10 per server."""
        await context_manager.create_context("conv_024", user_id="user_123")
        
        # Add 15 runs
        for i in range(15):
            await context_manager.add_tool_execution("conv_024", "calc_simulate_process", {
                "status": "success",
                "run_id": f"run_calc_{i:03d}",
                "server": "arken-calculation-engine"
            })
        
        context = await context_manager.get_context("conv_024")
        
        # Should be limited to 10
        assert len(context["runs_by_server"]["calc_engine"]) == 10
        
        # Newest runs should be first
        assert context["runs_by_server"]["calc_engine"][0] == "run_calc_014"


class TestServerDetection:
    """Test automatic server detection from results."""
    
    @pytest.mark.asyncio
    async def test_detect_calc_engine_by_server_field(self, context_manager):
        """Test detection via server field."""
        await context_manager.create_context("conv_030", user_id="user_123")
        
        await context_manager.add_tool_execution("conv_030", "some_tool", {
            "status": "success",
            "run_id": "run_001",
            "server": "arken-calculation-engine"
        })
        
        context = await context_manager.get_context("conv_030")
        assert "run_001" in context["runs_by_server"]["calc_engine"]
    
    @pytest.mark.asyncio
    async def test_detect_process_server_by_server_field(self, context_manager):
        """Test detection via server field."""
        await context_manager.create_context("conv_031", user_id="user_123")
        
        await context_manager.add_tool_execution("conv_031", "some_tool", {
            "status": "success",
            "run_id": "run_001",
            "server": "arken-process-simulator"
        })
        
        context = await context_manager.get_context("conv_031")
        assert "run_001" in context["runs_by_server"]["process_server"]
    
    @pytest.mark.asyncio
    async def test_detect_by_tool_name_prefix(self, context_manager):
        """Test detection via calc_ tool name prefix."""
        await context_manager.create_context("conv_032", user_id="user_123")
        
        # Tool with calc_ prefix but no server field
        await context_manager.add_tool_execution("conv_032", "calc_custom_tool", {
            "status": "success",
            "run_id": "run_001"
        })
        
        context = await context_manager.get_context("conv_032")
        assert "run_001" in context["runs_by_server"]["calc_engine"]
    
    @pytest.mark.asyncio
    async def test_detect_by_known_calc_tool(self, context_manager):
        """Test detection via known calc engine tool names."""
        await context_manager.create_context("conv_033", user_id="user_123")
        
        # Known calc engine tool without prefix
        await context_manager.add_tool_execution("conv_033", "validate_parameters", {
            "status": "success",
            "run_id": "run_001"
        })
        
        context = await context_manager.get_context("conv_033")
        assert "run_001" in context["runs_by_server"]["calc_engine"]
    
    @pytest.mark.asyncio
    async def test_detect_default_to_process(self, context_manager):
        """Test default to process_server for unknown tools."""
        await context_manager.create_context("conv_034", user_id="user_123")
        
        # Unknown tool without server field
        await context_manager.add_tool_execution("conv_034", "unknown_tool", {
            "status": "success",
            "run_id": "run_001"
        })
        
        context = await context_manager.get_context("conv_034")
        assert "run_001" in context["runs_by_server"]["process_server"]


class TestToolExecutionTracking:
    """Test tool execution tracking with server info."""
    
    @pytest.mark.asyncio
    async def test_execution_record_includes_server(self, context_manager):
        """Test that execution record includes server field."""
        await context_manager.create_context("conv_040", user_id="user_123")
        
        await context_manager.add_tool_execution("conv_040", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_001",
            "server": "arken-calculation-engine"
        })
        
        context = await context_manager.get_context("conv_040")
        
        assert len(context["executed_tools"]) == 1
        assert context["executed_tools"][0]["server"] == "arken-calculation-engine"
    
    @pytest.mark.asyncio
    async def test_get_executed_tools_list(self, context_manager):
        """Test getting list of executed tool names."""
        await context_manager.create_context("conv_041", user_id="user_123")
        
        await context_manager.add_tool_execution("conv_041", "validate_parameters", {
            "status": "success"
        })
        await context_manager.add_tool_execution("conv_041", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_001"
        })
        
        tools = await context_manager.get_executed_tools("conv_041")
        
        assert "validate_parameters" in tools
        assert "calc_simulate_process" in tools


class TestContextPersistence:
    """Test context persistence across Redis and MongoDB."""
    
    @pytest.mark.asyncio
    async def test_context_saved_to_both_stores(self, context_manager):
        """Test that context updates are saved to both Redis and MongoDB."""
        await context_manager.create_context("conv_050", user_id="user_123")
        
        await context_manager.add_tool_execution("conv_050", "calc_simulate_process", {
            "status": "success",
            "run_id": "run_001",
            "server": "arken-calculation-engine"
        })
        
        # Check Redis
        redis_data = await context_manager.redis.get("context:conv_050")
        assert redis_data is not None
        
        redis_context = json.loads(redis_data)
        assert "run_001" in redis_context["runs_by_server"]["calc_engine"]
    
    @pytest.mark.asyncio
    async def test_clear_context(self, context_manager):
        """Test clearing context removes from both stores."""
        await context_manager.create_context("conv_051", user_id="user_123")
        
        await context_manager.clear_context("conv_051")
        
        context = await context_manager.get_context("conv_051")
        assert context is None


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
