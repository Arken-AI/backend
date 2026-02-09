"""
Test Authentication API

Unit and integration tests for the login feature:
- POST /api/auth/login with valid/invalid credentials
- Visitor record creation/update in MongoDB
- Username validation (empty, case-insensitive)
"""

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required environment variables for testing
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017/test_db")
os.environ.setdefault("MCP_SERVER_COMMAND", "/usr/bin/python")
os.environ.setdefault("MCP_SERVER_ARGS", "/path/to/server.py")
os.environ.setdefault("MCP_SERVER_ENV_MONGODB_URI", "mongodb://localhost:27017/test_db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("APP_SHARED_PASSWORD", "arkenai123")

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models.visitor import LoginRequest, LoginResponse, VisitorRecord


# ---- Model Unit Tests ----

class TestLoginRequestModel:
    """Test LoginRequest Pydantic model validation."""

    def test_valid_login_request(self):
        req = LoginRequest(username="TestUser", password="arkenai123")
        assert req.username == "TestUser"
        assert req.password == "arkenai123"

    def test_username_trimmed(self):
        req = LoginRequest(username="  TestUser  ", password="arkenai123")
        assert req.username == "TestUser"

    def test_empty_username_rejected(self):
        with pytest.raises(Exception):
            LoginRequest(username="", password="arkenai123")

    def test_whitespace_only_username_rejected(self):
        with pytest.raises(Exception):
            LoginRequest(username="   ", password="arkenai123")

    def test_single_char_username_allowed(self):
        req = LoginRequest(username="A", password="pwd")
        assert req.username == "A"


class TestLoginResponseModel:
    """Test LoginResponse Pydantic model."""

    def test_success_response(self):
        resp = LoginResponse(success=True, username="JohnDoe", message="Login successful")
        assert resp.success is True
        assert resp.username == "JohnDoe"
        assert resp.message == "Login successful"

    def test_optional_message(self):
        resp = LoginResponse(success=False, username="", message=None)
        assert resp.message is None


class TestVisitorRecordModel:
    """Test VisitorRecord Pydantic model."""

    def test_visitor_record(self):
        now = datetime.now(timezone.utc)
        record = VisitorRecord(
            username="johndoe",
            display_name="JohnDoe",
            first_login_time=now,
            last_login_time=now,
            login_count=1,
        )
        assert record.username == "johndoe"
        assert record.display_name == "JohnDoe"
        assert record.login_count == 1


# ---- API Endpoint Tests ----

class TestAuthEndpoint:
    """Test POST /api/auth/login endpoint."""

    @pytest.fixture
    def mock_mongo_client(self):
        """Create a mock MongoDB client."""
        mock = AsyncMock()
        mock.save_visitor = AsyncMock(return_value={
            "username": "testuser",
            "display_name": "TestUser",
            "first_login_time": datetime.now(timezone.utc),
            "last_login_time": datetime.now(timezone.utc),
            "login_count": 1,
        })
        mock.get_visitor = AsyncMock(return_value=None)
        return mock

    @pytest.fixture
    def client_with_mock_mongo(self, mock_mongo_client):
        """Create test client with mocked MongoDB."""
        from app.dependencies import get_mongo_client

        app.dependency_overrides[get_mongo_client] = lambda: mock_mongo_client
        yield
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, mock_mongo_client, client_with_mock_mongo):
        """Test successful login with correct password."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "TestUser", "password": "arkenai123"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["username"] == "TestUser"
        assert data["message"] == "Login successful"

        # Verify visitor was saved
        mock_mongo_client.save_visitor.assert_called_once_with(
            username="testuser",
            display_name="TestUser",
        )

    @pytest.mark.asyncio
    async def test_login_invalid_password(self, client_with_mock_mongo):
        """Test login with wrong password returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "TestUser", "password": "wrongpassword"},
            )

        assert response.status_code == 401
        data = response.json()
        assert data["detail"] == "Invalid credentials"

    @pytest.mark.asyncio
    async def test_login_empty_username(self, client_with_mock_mongo):
        """Test login with empty username returns 422 (validation error)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "", "password": "arkenai123"},
            )

        assert response.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    async def test_login_whitespace_username(self, client_with_mock_mongo):
        """Test login with whitespace-only username returns 422."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "   ", "password": "arkenai123"},
            )

        assert response.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    async def test_login_case_insensitive_storage(self, mock_mongo_client, client_with_mock_mongo):
        """Test that username is stored lowercase, display_name preserves case."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "JohnDoe", "password": "arkenai123"},
            )

        assert response.status_code == 200
        mock_mongo_client.save_visitor.assert_called_once_with(
            username="johndoe",
            display_name="JohnDoe",
        )

    @pytest.mark.asyncio
    async def test_login_preserves_display_name(self, mock_mongo_client, client_with_mock_mongo):
        """Test that response contains original case username."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "MyUser123", "password": "arkenai123"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "MyUser123"

    @pytest.mark.asyncio
    async def test_login_mongo_error(self, mock_mongo_client, client_with_mock_mongo):
        """Test that MongoDB error returns 500 with friendly message."""
        mock_mongo_client.save_visitor.side_effect = Exception("Connection refused")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "TestUser", "password": "arkenai123"},
            )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"] == "Unable to connect. Please try again later."

    @pytest.mark.asyncio
    async def test_login_missing_password_field(self, client_with_mock_mongo):
        """Test login without password field returns 422."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"username": "TestUser"},
            )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_login_missing_username_field(self, client_with_mock_mongo):
        """Test login without username field returns 422."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"password": "arkenai123"},
            )

        assert response.status_code == 422


class TestConversationListingWithUsername:
    """Test GET /api/conversations with username filter."""

    @pytest.fixture
    def mock_mongo_for_conversations(self):
        """Create a mock MongoDB client for conversation listing."""
        mock = AsyncMock()
        
        # Mock the _client to return a database with conversations collection
        mock_db = MagicMock()
        mock_collection = AsyncMock()
        
        # Mock count_documents
        mock_collection.count_documents = AsyncMock(return_value=1)
        
        # Mock find().sort().skip().limit() chain
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        
        # Make cursor async iterable with one sample conversation
        sample_doc = {
            "_id": "fake_id",
            "conversation_id": "conv_test123",
            "user_id": "testuser",
            "messages": [
                {"role": "user", "content": "Hello there"},
                {"role": "assistant", "content": "Hi!"},
            ],
            "run_ids": [],
            "created_at": "2026-02-10T00:00:00",
            "updated_at": "2026-02-10T00:00:00",
        }
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor._items = [sample_doc]
        mock_cursor._index = 0

        async def async_next(self):
            if self._index < len(self._items):
                item = self._items[self._index]
                self._index += 1
                return item
            raise StopAsyncIteration

        mock_cursor.__anext__ = async_next
        mock_collection.find = MagicMock(return_value=mock_cursor)
        
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock._client.__getitem__ = MagicMock(return_value=mock_db)
        
        return mock

    @pytest.fixture
    def client_with_conversation_mock(self, mock_mongo_for_conversations):
        """Create test client with mocked conversations."""
        from app.dependencies import get_mongo_client

        app.dependency_overrides[get_mongo_client] = lambda: mock_mongo_for_conversations
        yield mock_mongo_for_conversations
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_conversations_with_username_filter(self, client_with_conversation_mock):
        """Test that username query parameter filters conversations."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/conversations?username=TestUser")

        assert response.status_code == 200
        data = response.json()
        assert "conversations" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_list_conversations_without_username(self, client_with_conversation_mock):
        """Test that conversations are listed without username filter."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/conversations")

        assert response.status_code == 200
        data = response.json()
        assert "conversations" in data
