"""
Tests for shareable link feature.

Coverage:
  POST   /api/chat/{id}/share    — create link (happy, idempotent, 404, 403)
  DELETE /api/chat/{id}/share    — revoke link (happy, idempotent, 403)
  GET    /api/share/{token}      — public read (happy, 404, revoked link)
  Model  ShareResponse / HXStep / SharedDesignResponse
"""

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Required env vars
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017/test_db")
os.environ.setdefault("MCP_SERVER_COMMAND", "/usr/bin/python")
os.environ.setdefault("MCP_SERVER_ARGS", "/path/to/server.py")
os.environ.setdefault("MCP_SERVER_ENV_MONGODB_URI", "mongodb://localhost:27017/test_db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("APP_SHARED_PASSWORD", "testpassword")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models.requests import ShareResponse, HXStep, SharedDesignResponse, MessageHistoryItem


# ── Fixtures ──────────────────────────────────────────────────────────────────

CONV_ID     = "conv_test_share_001"
TOKEN       = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
FRONTEND_URL = "http://localhost:5173"

def make_context(*, is_shared=False, share_token=None, user_id="alice"):
    return {
        "conversation_id": CONV_ID,
        "user_id": user_id,
        "messages": [
            {"role": "user", "content": "Hello", "timestamp": "2026-01-01T00:00:00", "status": "complete"},
            {"role": "assistant", "content": "Hi there", "timestamp": "2026-01-01T00:00:01", "status": "complete"},
        ],
        "hx_steps": [],
        "is_shared": is_shared,
        "share_token": share_token,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def make_mongo_mock(context: dict | None):
    """Return a MongoClient-shaped mock that returns `context` on find_one."""
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=context)
    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)

    mongo = MagicMock()
    mongo._client = db
    return mongo, collection


def make_redis_mock():
    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    return r


# ── Model tests ───────────────────────────────────────────────────────────────

class TestShareResponseModel:
    def test_valid(self):
        s = ShareResponse(share_url="http://x/share/abc", token="abc")
        assert s.share_url == "http://x/share/abc"
        assert s.token == "abc"


class TestHXStepModel:
    def test_minimal(self):
        s = HXStep(step_id="FLUID_PROPERTIES", step_number=1, status="APPROVED")
        assert s.step_id == "FLUID_PROPERTIES"
        assert s.result is None
        assert s.timestamp is None

    def test_with_result(self):
        s = HXStep(step_id="DUTY_CALC", step_number=2, status="CORRECTED",
                   result={"Q_kW": 500}, timestamp="2026-01-01T10:00:00")
        assert s.result == {"Q_kW": 500}


class TestSharedDesignResponseModel:
    def test_empty_steps(self):
        r = SharedDesignResponse(
            title="My HX",
            created_at="2026-01-01T00:00:00",
            messages=[],
            hx_steps=[],
        )
        assert r.hx_steps == []

    def test_with_steps(self):
        step = HXStep(step_id="FLUID_PROPERTIES", step_number=1, status="APPROVED")
        r = SharedDesignResponse(
            title=None,
            created_at="2026-01-01T00:00:00",
            messages=[],
            hx_steps=[step],
        )
        assert len(r.hx_steps) == 1


# ── POST /chat/{id}/share ─────────────────────────────────────────────────────

class TestShareConversation:

    @pytest.mark.asyncio
    async def test_creates_share_link(self):
        """Happy path — unshared conv → generates token, returns URL."""
        context  = make_context(is_shared=False)
        mongo, collection = make_mongo_mock(context)
        redis    = make_redis_mock()

        with patch("app.api.chat.settings") as mock_settings, \
             patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            mock_settings.frontend_url = FRONTEND_URL
            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "alice"})

            assert resp.status_code == 200
            data = resp.json()
            assert "share_url" in data
            assert data["share_url"].startswith(FRONTEND_URL + "/share/")
            assert "token" in data

    @pytest.mark.asyncio
    async def test_idempotent_returns_existing(self):
        """Already-shared conv → same token returned without DB write."""
        context = make_context(is_shared=True, share_token=TOKEN)
        mongo, collection = make_mongo_mock(context)
        redis   = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            mock_settings.frontend_url = FRONTEND_URL
            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "alice"})

            assert resp.status_code == 200
            data = resp.json()
            assert data["token"] == TOKEN
            collection.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_404_when_conversation_missing(self):
        mongo, _ = make_mongo_mock(None)
        redis     = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=None)), \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/api/chat/{CONV_ID}/share")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_403_when_no_username_and_conv_has_user_id(self):
        """Auth bypass fix: omitting X-Username must be rejected when conv has a user_id."""
        context = make_context(user_id="alice")
        mongo, _ = make_mongo_mock(context)
        redis    = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/api/chat/{CONV_ID}/share")  # no header

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_403_when_ownership_mismatch(self):
        """Conv belongs to alice, bob tries to share it."""
        context = make_context(user_id="alice")
        mongo, _ = make_mongo_mock(context)
        redis    = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "bob"})

        assert resp.status_code == 403


# ── DELETE /chat/{id}/share ───────────────────────────────────────────────────

class TestRevokeShare:

    @pytest.mark.asyncio
    async def test_revokes_existing_link(self):
        context  = make_context(is_shared=True, share_token=TOKEN)
        mongo, collection = make_mongo_mock(context)
        redis    = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.delete(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "alice"})

        assert resp.status_code == 204
        # Verify $unset was used (not $set None)
        call_kwargs = collection.update_one.call_args
        update_doc = call_kwargs[0][1]
        assert "$unset" in update_doc
        assert "share_token" in update_doc["$unset"]

    @pytest.mark.asyncio
    async def test_idempotent_revoke_on_unshared(self):
        """Revoking a conv that isn't shared should still succeed (204)."""
        context = make_context(is_shared=False)
        mongo, _ = make_mongo_mock(context)
        redis   = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.delete(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "alice"})

        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_403_on_ownership_mismatch(self):
        context = make_context(is_shared=True, share_token=TOKEN, user_id="alice")
        mongo, _ = make_mongo_mock(context)
        redis   = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.delete(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "bob"})

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_redis_cache_invalidated(self):
        """Redis key must be deleted after revoke."""
        context = make_context(is_shared=True, share_token=TOKEN)
        mongo, _ = make_mongo_mock(context)
        redis   = make_redis_mock()

        with patch("app.services.context_manager.ContextManager.get_context", new=AsyncMock(return_value=context)), \
             patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo), \
             patch("app.dependencies.get_redis_client", return_value=lambda: redis):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.delete(f"/api/chat/{CONV_ID}/share", headers={"X-Username": "alice"})

        redis.delete.assert_called_once_with(f"context:{CONV_ID}")


# ── GET /share/{token} ────────────────────────────────────────────────────────

class TestGetSharedDesign:

    def make_doc(self, *, is_shared=True, share_token=TOKEN):
        return {
            "_id": "mongo_id",
            "conversation_id": CONV_ID,
            "user_id": "alice",
            "share_token": share_token,
            "is_shared": is_shared,
            "title": "My HX Design",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "messages": [
                {"role": "user",      "content": "Design a HX", "timestamp": "2026-01-01T00:00:00", "status": "complete"},
                {"role": "assistant", "content": "Sure!",        "timestamp": "2026-01-01T00:00:01", "status": "complete"},
            ],
            "hx_steps": [
                {"step_id": "FLUID_PROPERTIES", "step_number": 1, "status": "APPROVED", "result": {}, "timestamp": "2026-01-01T00:01:00"},
            ],
        }

    @pytest.mark.asyncio
    async def test_returns_design_for_valid_token(self):
        doc  = self.make_doc()
        mongo, collection = make_mongo_mock(doc)
        collection.find_one = AsyncMock(return_value=doc)

        with patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"/api/share/{TOKEN}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My HX Design"
        assert len(data["messages"]) == 2
        assert len(data["hx_steps"]) == 1
        assert data["hx_steps"][0]["step_id"] == "FLUID_PROPERTIES"

    @pytest.mark.asyncio
    async def test_strips_sensitive_metadata(self):
        """user_id and internal metadata must NOT appear in the response."""
        doc = self.make_doc()
        mongo, collection = make_mongo_mock(doc)
        collection.find_one = AsyncMock(return_value=doc)

        with patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"/api/share/{TOKEN}")

        data = resp.json()
        # No user_id at top level
        assert "user_id" not in data
        # message_id and metadata stripped
        for msg in data["messages"]:
            assert msg.get("message_id") is None
            assert msg.get("metadata") is None

    @pytest.mark.asyncio
    async def test_404_for_unknown_token(self):
        mongo, collection = make_mongo_mock(None)
        collection.find_one = AsyncMock(return_value=None)

        with patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/share/nonexistent-token-xyz")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_404_for_revoked_link(self):
        """is_shared=False documents must not be returned."""
        doc = self.make_doc(is_shared=False)
        mongo, collection = make_mongo_mock(None)  # find_one returns None (is_shared filter)
        collection.find_one = AsyncMock(return_value=None)

        with patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"/api/share/{TOKEN}")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_hx_steps_returned_as_list(self):
        """Conversations without hx_steps should return an empty list, not null."""
        doc = self.make_doc()
        doc["hx_steps"] = []
        mongo, collection = make_mongo_mock(doc)
        collection.find_one = AsyncMock(return_value=doc)

        with patch("app.api.chat.settings") as mock_settings, \
             patch("app.dependencies.get_mongo_client", return_value=lambda: mongo):

            mock_settings.mongodb_db_name = "test_db"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"/api/share/{TOKEN}")

        data = resp.json()
        assert data["hx_steps"] == []
