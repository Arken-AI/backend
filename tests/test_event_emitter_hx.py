"""
Tests for emit_hx_design_started and the HXDesignStartedEvent round-trip.
"""

import pytest
import json
from unittest.mock import AsyncMock

from app.services.event_emitter import EventEmitter
from app.models.events import (
    HXDesignStartedEvent,
    EventType,
    EVENT_TYPE_MAP,
    BaseEvent,
)


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=True)
    r.xadd = AsyncMock(return_value=b"1234567890-0")
    r.xrange = AsyncMock(return_value=[])
    r.xlen = AsyncMock(return_value=0)
    r.delete = AsyncMock(return_value=1)
    return r


@pytest.fixture
def emitter(mock_redis):
    return EventEmitter(mock_redis, ttl_seconds=3600)


class TestHXDesignStartedEvent:
    def test_event_type_in_enum(self):
        assert EventType.HX_DESIGN_STARTED == "hx_design_started"

    def test_event_in_type_map(self):
        assert EventType.HX_DESIGN_STARTED in EVENT_TYPE_MAP
        assert EVENT_TYPE_MAP[EventType.HX_DESIGN_STARTED] is HXDesignStartedEvent

    def test_event_fields(self):
        evt = HXDesignStartedEvent(
            request_id="req_123",
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        assert evt.session_id == "sess_abc"
        assert evt.stream_url == "/api/v1/hx/design/sess_abc/stream"
        assert evt.event_type == EventType.HX_DESIGN_STARTED

    def test_to_redis_dict_includes_all_fields(self):
        evt = HXDesignStartedEvent(
            request_id="req_123",
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        d = evt.to_redis_dict()
        data = json.loads(d["data"])
        assert data["session_id"] == "sess_abc"
        assert data["stream_url"] == "/api/v1/hx/design/sess_abc/stream"
        assert data["event_type"] == "hx_design_started"

    def test_from_redis_dict_round_trip(self):
        evt = HXDesignStartedEvent(
            request_id="req_123",
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        redis_dict = evt.to_redis_dict()
        restored = BaseEvent.from_redis_dict(redis_dict)
        assert isinstance(restored, HXDesignStartedEvent)
        assert restored.session_id == "sess_abc"
        assert restored.stream_url == "/api/v1/hx/design/sess_abc/stream"

    def test_sse_format_includes_event_type(self):
        evt = HXDesignStartedEvent(
            request_id="req_123",
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        sse = evt.to_sse_format()
        assert "event: hx_design_started" in sse
        assert "sess_abc" in sse


class TestEmitHXDesignStarted:
    async def test_emits_event(self, emitter, mock_redis):
        seq = await emitter.emit_hx_design_started(
            request_id="req_123",
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        assert seq == 1
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        stream_key = call_args[0][0]
        assert stream_key == "events:req_123"

    async def test_stored_data_includes_session_id(self, emitter, mock_redis):
        await emitter.emit_hx_design_started(
            request_id="req_123",
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        call_args = mock_redis.xadd.call_args
        redis_data = call_args[0][1]
        event_data = json.loads(redis_data["data"])
        assert event_data["session_id"] == "sess_abc"
        assert event_data["stream_url"] == "/api/v1/hx/design/sess_abc/stream"
