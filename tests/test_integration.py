""""
Integration Tests for MCP Chat Backend

These tests verify real component interactions:
- Redis connection and event streaming
- MongoDB connection via ContextManager (dual-storage validation)
- MCP server subprocess lifecycle
- LLM provider API calls (with rate limiting)

Setup Requirements:
1. Redis server running on configured port
2. MongoDB server running with authentication
3. Valid API keys in .env file
4. MCP process server accessible

Run with: pytest tests/test_integration.py -v -s
"""

import os
import pytest
import asyncio
import redis
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

from app.services.context_manager import ContextManager
from app.core.mcp_client import MCPClient
from app.core.llm_gemini_provider import GeminiProvider


# Load environment variables
load_dotenv()


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def redis_client():
    """Real Redis client for integration tests"""
    redis_url = os.getenv("TEST_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/15"))
    client = redis.from_url(redis_url, decode_responses=True)
    
    # Test connection
    try:
        client.ping()
        print(f"\n✓ Redis connected: {redis_url}")
    except redis.ConnectionError as e:
        pytest.skip(f"Redis not available: {e}")
    
    yield client
    
    # Cleanup: flush test database
    client.flushdb()
    client.close()


@pytest.fixture(scope="session")
async def mongodb_client():
    """Real MongoDB client for integration tests"""
    mongodb_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "arken_process_db")
    
    # Use main database (user has permissions)
    client = AsyncIOMotorClient(mongodb_url)
    
    # Test connection
    try:
        await client.admin.command('ping')
        print(f"\n✓ MongoDB connected: {db_name}")
    except Exception as e:
        pytest.skip(f"MongoDB not available: {e}")
    
    db = client[db_name]
    yield db
    
    # Cleanup: just close connection (no drop database to avoid permission issues)
    client.close()


@pytest.fixture(scope="session")
async def context_manager(redis_client, mongodb_client):
    """Real ContextManager with Redis and MongoDB backend"""
    # Get the motor client from the database object
    mongo_client = mongodb_client.client
    db_name = mongodb_client.name
    
    manager = ContextManager(
        redis_client=redis_client,
        mongo_client=mongo_client,
        mongo_db_name=db_name
    )
    yield manager


@pytest.fixture(scope="session")
async def mcp_client():
    """Real MCP client connecting to subprocess"""
    # MCPClient requires MCPServerConfig
    from app.core.mcp_client import MCPServerConfig
    
    try:
        config = MCPServerConfig.from_env()
        client = MCPClient(config)
        await client.connect()
        print(f"\n✓ MCP server started")
    except Exception as e:
        pytest.skip(f"MCP server not available: {e}")
    
    yield client
    
    # Cleanup: disconnect MCP server
    try:
        await client.disconnect()
    except:
        pass


@pytest.fixture(scope="session")
async def gemini_provider():
    """Real Gemini LLM provider with API key"""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key or api_key == "your_google_api_key_here":
        pytest.skip("GOOGLE_API_KEY not configured")
    
    provider = GeminiProvider()
    print(f"\n✓ Gemini provider initialized")
    
    yield provider


# ============================================================================
# Integration Tests
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
class TestRedisIntegration:
    """Test Redis connection and event streaming"""
    
    async def test_redis_connection(self, redis_client):
        """Verify Redis is accessible and responsive"""
        assert redis_client.ping() is True
    
    async def test_redis_set_get(self, redis_client):
        """Verify basic Redis operations"""
        redis_client.set("test_key", "test_value", ex=10)
        value = redis_client.get("test_key")
        assert value == "test_value"
    
    async def test_context_manager_storage(self, context_manager):
        """Verify ContextManager can store and retrieve context"""
        session_id = "test_session_redis"
        
        # Create context
        context = context_manager.create_context(session_id, user_id="test_user")
        assert context is not None
        assert context["conversation_id"] == session_id
        
        # Update context
        context_manager.update_context(
            session_id,
            updates={
                "current_industry": "sugar",
                "simulation_params": {"test_param": "test_value"}
            }
        )
        
        # Retrieve and verify
        retrieved = context_manager.get_context(session_id)
        assert retrieved is not None
        assert retrieved["conversation_id"] == session_id
        assert retrieved["current_industry"] == "sugar"
        assert retrieved["simulation_params"]["test_param"] == "test_value"


@pytest.mark.asyncio
@pytest.mark.integration
class TestMCPServerIntegration:
    """Test MCP server subprocess lifecycle and tool discovery"""
    
    async def test_mcp_server_lifecycle(self, mcp_client):
        """Verify MCP server can start and stop"""
        # Already started in fixture
        assert mcp_client._session is not None
        assert mcp_client._read_stream is not None
        assert mcp_client._write_stream is not None
    
    async def test_mcp_tool_discovery(self, mcp_client):
        """Verify MCP server exposes expected tools"""
        tools = await mcp_client.list_tools()
        assert len(tools) > 0
        
        # Check for key tools
        tool_names = [tool.name for tool in tools]
        expected_tools = ["list_runs", "get_run_by_id", "create_run"]
        
        for expected in expected_tools:
            assert expected in tool_names, f"Expected tool '{expected}' not found"
    
    async def test_mcp_tool_execution(self, mcp_client):
        """Verify MCP tool can be called successfully"""
        # List runs should work even if empty
        result = await mcp_client.call_tool("list_runs", {})
        assert result is not None


@pytest.mark.asyncio
@pytest.mark.integration
class TestGeminiProviderIntegration:
    """Test Gemini provider API calls (rate limited)"""
    
    async def test_gemini_provider_initialization(self, gemini_provider):
        """Verify Gemini provider is properly initialized"""
        assert gemini_provider is not None
        assert hasattr(gemini_provider, "create_message")
    
    @pytest.mark.slow
    async def test_gemini_simple_query(self, gemini_provider):
        """Verify Gemini can handle simple query (SLOW: real API call)"""
        # Simple, fast query to minimize API costs
        messages = [{"role": "user", "parts": [{"text": "Reply with just the word 'Hello'"}]}]
        
        response = await gemini_provider.create_message(messages, max_tokens=10)
        assert response is not None
        assert len(response) > 0
        print(f"\n  Gemini response: {response}")


# ============================================================================
# Test Markers and Execution Guide
# ============================================================================
"""
Test Markers:
- @pytest.mark.integration: All integration tests (require real services)
- @pytest.mark.slow: Tests that make real API calls (Gemini, expensive)

Run all integration tests:
  pytest tests/test_integration.py -v -s -m integration

Run fast integration tests only (no Gemini API calls):
  pytest tests/test_integration.py -v -s -m "integration and not slow"

Run specific test class:
  pytest tests/test_integration.py::TestRedisIntegration -v -s

Run specific test:
  pytest tests/test_integration.py::TestRedisIntegration::test_redis_connection -v -s
"""
