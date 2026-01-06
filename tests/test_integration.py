"""
Integration Tests for MCP Chat Backend

These tests verify real component interactions:
- Redis connection and event streaming
- MongoDB connection and data persistence
- MCP server subprocess lifecycle
- LLM provider API calls (with rate limiting)
- Full orchestration workflow end-to-end

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

from app.core.context_manager import ContextManager
from app.core.mcp_client import MCPClient
from app.core.llm_provider import LLMProviderFactory
from app.services.orchestration_service import OrchestrationService


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
    
    if "_test" not in db_name:
        db_name = f"{db_name}_test"
    
    client = AsyncIOMotorClient(mongodb_url)
    
    # Test connection
    try:
        await client.admin.command('ping')
        print(f"\n✓ MongoDB connected: {db_name}")
    except Exception as e:
        pytest.skip(f"MongoDB not available: {e}")
    
    db = client[db_name]
    yield db
    
    # Cleanup: drop test database
    await client.drop_database(db_name)
    client.close()


@pytest.fixture(scope="session")
async def context_manager(redis_client):
    """Real ContextManager with Redis backend"""
    manager = ContextManager(redis_client=redis_client)
    yield manager


@pytest.fixture(scope="session")
async def mcp_client():
    """Real MCP client connecting to subprocess"""
    command = os.getenv("MCP_SERVER_COMMAND", "python")
    args = os.getenv("MCP_SERVER_ARGS", "-m,mcp_process_server.server").split(",")
    cwd = os.getenv("MCP_SERVER_CWD", os.getcwd())
    
    # Pass MongoDB URI to MCP server
    env = {
        "MONGODB_URI": os.getenv("MONGODB_URL")
    }
    
    client = MCPClient(command=command, args=args, cwd=cwd, env=env)
    
    try:
        await client.start()
        print(f"\n✓ MCP server started: {command} {' '.join(args)}")
    except Exception as e:
        pytest.skip(f"MCP server not available: {e}")
    
    yield client
    
    # Cleanup: stop MCP server
    await client.stop()


@pytest.fixture(scope="session")
async def llm_provider():
    """Real LLM provider with API key"""
    provider_name = os.getenv("LLM_PROVIDER", "gemini")
    
    if provider_name == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key or api_key == "your_google_api_key_here":
            pytest.skip("GOOGLE_API_KEY not configured")
    elif provider_name == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key == "your_password":
            pytest.skip("ANTHROPIC_API_KEY not configured")
    else:
        pytest.skip(f"Unknown LLM provider: {provider_name}")
    
    provider = LLMProviderFactory.create_provider(provider_name)
    print(f"\n✓ LLM provider initialized: {provider_name}")
    
    yield provider


@pytest.fixture
async def orchestration_service(context_manager, mcp_client, llm_provider):
    """Real OrchestrationService with all components wired"""
    service = OrchestrationService(
        context_manager=context_manager,
        mcp_client=mcp_client,
        llm_provider=llm_provider
    )
    yield service


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
        context_data = {"user_input": "test query", "step": 1}
        
        await context_manager.save_context(session_id, context_data)
        retrieved = await context_manager.get_context(session_id)
        
        assert retrieved is not None
        assert retrieved["user_input"] == "test query"
        assert retrieved["step"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
class TestMongoDBIntegration:
    """Test MongoDB connection and data persistence"""
    
    async def test_mongodb_connection(self, mongodb_client):
        """Verify MongoDB is accessible"""
        collections = await mongodb_client.list_collection_names()
        assert isinstance(collections, list)
    
    async def test_mongodb_insert_find(self, mongodb_client):
        """Verify basic MongoDB operations"""
        collection = mongodb_client["test_collection"]
        
        doc = {"name": "test_doc", "value": 42}
        result = await collection.insert_one(doc)
        assert result.inserted_id is not None
        
        found = await collection.find_one({"name": "test_doc"})
        assert found is not None
        assert found["value"] == 42


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
class TestLLMProviderIntegration:
    """Test LLM provider API calls (rate limited)"""
    
    async def test_llm_provider_initialization(self, llm_provider):
        """Verify LLM provider is properly initialized"""
        assert llm_provider is not None
        assert hasattr(llm_provider, "generate")
    
    @pytest.mark.slow
    async def test_llm_simple_query(self, llm_provider):
        """Verify LLM can handle simple query (SLOW: real API call)"""
        # Simple, fast query to minimize API costs
        messages = [{"role": "user", "content": "Reply with just the word 'Hello'"}]
        
        response = await llm_provider.generate(messages, max_tokens=10)
        assert response is not None
        assert len(response) > 0
        print(f"\n  LLM response: {response}")


@pytest.mark.asyncio
@pytest.mark.integration
class TestOrchestrationWorkflow:
    """Test full orchestration workflow end-to-end"""
    
    async def test_process_query_basic(self, orchestration_service):
        """Verify orchestration can process a basic query"""
        session_id = "test_session_orchestration"
        user_query = "What tools are available?"
        
        # Execute workflow
        response = await orchestration_service.process_query(session_id, user_query)
        
        # Verify response structure
        assert response is not None
        assert "response" in response or "error" not in response
    
    @pytest.mark.slow
    async def test_process_query_with_mcp_tools(self, orchestration_service):
        """Verify orchestration can use MCP tools (SLOW: real LLM + MCP calls)"""
        session_id = "test_session_mcp_tools"
        user_query = "List all available runs in the system"
        
        # Execute workflow
        response = await orchestration_service.process_query(session_id, user_query)
        
        # Verify response
        assert response is not None
        print(f"\n  Orchestration response: {response}")
    
    async def test_context_persistence_across_queries(self, orchestration_service, context_manager):
        """Verify context persists across multiple queries in same session"""
        session_id = "test_session_context_persist"
        
        # First query
        await orchestration_service.process_query(session_id, "My name is Alice")
        
        # Check context was saved
        context = await context_manager.get_context(session_id)
        assert context is not None
        assert "My name is Alice" in str(context)
        
        # Second query - should have context from first
        response = await orchestration_service.process_query(session_id, "What is my name?")
        assert response is not None


# ============================================================================
# Test Markers and Execution Guide
# ============================================================================
"""
Test Markers:
- @pytest.mark.integration: All integration tests (require real services)
- @pytest.mark.slow: Tests that make real API calls (LLM, expensive)

Run all integration tests:
  pytest tests/test_integration.py -v -s -m integration

Run fast integration tests only (no LLM API calls):
  pytest tests/test_integration.py -v -s -m "integration and not slow"

Run specific test class:
  pytest tests/test_integration.py::TestRedisIntegration -v -s

Run specific test:
  pytest tests/test_integration.py::TestRedisIntegration::test_redis_connection -v -s
"""
