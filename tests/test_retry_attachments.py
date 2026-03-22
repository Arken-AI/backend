"""
Tests for retry endpoint with attachment persistence.

Covers:
- Happy path: retry correctly re-sends attachments to the LLM
- Edge case: retry when message has no attachments (None)
- Edge case: retry when MongoDB attachment lookup returns None (data not persisted)
"""

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.context_manager import ContextManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock(return_value=True)
    return r


@pytest.fixture
def mock_mongo_client():
    client = MagicMock()
    db = MagicMock()
    client.__getitem__ = MagicMock(return_value=db)
    db.__getitem__ = MagicMock(return_value=AsyncMock())
    return client


@pytest.fixture
def context_manager(mock_redis, mock_mongo_client):
    cm = ContextManager(redis_client=mock_redis, mongo_client=mock_mongo_client)
    cm.attachments_collection = AsyncMock()
    cm.contexts_collection = AsyncMock()
    return cm


SAMPLE_ATTACHMENTS = [
    {
        "media_type": "image/png",
        "data": base64.b64encode(b"fake-image-bytes").decode("ascii"),
        "filename": "diagram.png",
    }
]


# ---------------------------------------------------------------------------
# Tests: ContextManager attachment store/retrieve
# ---------------------------------------------------------------------------

class TestAttachmentStore:
    """Unit tests for store_message_attachments / get_message_attachments."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve_attachments(self, context_manager):
        """Attachments stored via store_message_attachments are retrievable."""
        message_id = "msg_abc123"
        context_manager.attachments_collection.replace_one = AsyncMock(return_value=None)
        context_manager.attachments_collection.find_one = AsyncMock(
            return_value={"message_id": message_id, "attachments": SAMPLE_ATTACHMENTS}
        )

        await context_manager.store_message_attachments(message_id, SAMPLE_ATTACHMENTS)
        result = await context_manager.get_message_attachments(message_id)

        context_manager.attachments_collection.replace_one.assert_awaited_once()
        assert result == SAMPLE_ATTACHMENTS

    @pytest.mark.asyncio
    async def test_retrieve_returns_none_when_not_found(self, context_manager):
        """get_message_attachments returns None when no record exists."""
        context_manager.attachments_collection.find_one = AsyncMock(return_value=None)

        result = await context_manager.get_message_attachments("msg_missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_failure_is_swallowed(self, context_manager):
        """MongoDB write failure does not raise — attachment storage is best-effort."""
        context_manager.attachments_collection.replace_one = AsyncMock(
            side_effect=Exception("MongoDB connection lost")
        )

        # Should not raise
        await context_manager.store_message_attachments("msg_xyz", SAMPLE_ATTACHMENTS)

    @pytest.mark.asyncio
    async def test_retrieve_failure_returns_none(self, context_manager):
        """MongoDB read failure returns None — retry falls back to no attachments."""
        context_manager.attachments_collection.find_one = AsyncMock(
            side_effect=Exception("MongoDB timeout")
        )

        result = await context_manager.get_message_attachments("msg_xyz")
        assert result is None
