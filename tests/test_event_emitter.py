"""
Unit tests for EventEmitter Service

Tests event emission, storage, retrieval, and TTL management.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.services.event_emitter import EventEmitter
from app.models.events import (
    ThinkingStartEvent,
    ThinkingEndEvent,
    ToolStartEvent,
    ToolEndEvent,
    RunProgressEvent,
    MessageDeltaEvent,
    MessageFinalEvent,
    AppErrorEvent,
    AgentTextEvent,
    ToolStatus,
    ErrorType,
)


@pytest.fixture
def mock_redis():
    """Mock async Redis client"""
    redis = AsyncMock()
    # Default returns for common operations
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.xadd = AsyncMock(return_value=b'1234567890-0')
    redis.xrange = AsyncMock(return_value=[])
    redis.xlen = AsyncMock(return_value=0)
    redis.delete = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def emitter(mock_redis):
    """Create EventEmitter instance with mocked Redis"""
    return EventEmitter(mock_redis, ttl_seconds=3600)


class TestEventEmitterBasics:
    """Test basic EventEmitter functionality"""
    
    def test_init(self, mock_redis):
        """Test EventEmitter initialization"""
        emitter = EventEmitter(mock_redis, ttl_seconds=7200, max_stream_length=5000)
        
        assert emitter.redis == mock_redis
        assert emitter.ttl_seconds == 7200
        assert emitter.max_stream_length == 5000
    
    def test_stream_key(self, emitter):
        """Test stream key generation"""
        key = emitter._stream_key("req_123")
        assert key == "events:req_123"
    
    def test_sequence_key(self, emitter):
        """Test sequence key generation"""
        key = emitter._sequence_key("req_123")
        assert key == "events:req_123:seq"


class TestSequenceManagement:
    """Test sequence number generation"""
    
    @pytest.mark.asyncio
    async def test_get_next_sequence_first_call(self, emitter, mock_redis):
        """Test getting first sequence number"""
        mock_redis.incr.return_value = 1
        
        seq = await emitter._get_next_sequence("req_123")
        
        assert seq == 1
        mock_redis.incr.assert_called_once_with("events:req_123:seq")
        mock_redis.expire.assert_called_once_with("events:req_123:seq", 3600)
    
    @pytest.mark.asyncio
    async def test_get_next_sequence_subsequent_calls(self, emitter, mock_redis):
        """Test getting subsequent sequence numbers"""
        # Simulate multiple calls
        mock_redis.incr.side_effect = [1, 2, 3, 4]
        
        seq1 = await emitter._get_next_sequence("req_123")
        seq2 = await emitter._get_next_sequence("req_123")
        seq3 = await emitter._get_next_sequence("req_123")
        
        assert seq1 == 1
        assert seq2 == 2
        assert seq3 == 3
        # Expire only called on first
        assert mock_redis.expire.call_count == 1


class TestThinkingEvents:
    """Test thinking event emission"""
    
    @pytest.mark.asyncio
    async def test_emit_thinking_start(self, emitter, mock_redis):
        """Test emitting thinking start event"""
        mock_redis.incr.return_value = 1
        
        seq = await emitter.emit_thinking_start("req_123")
        
        assert seq == 1
        mock_redis.xadd.assert_called_once()
        
        # Check xadd was called with correct stream key
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == "events:req_123"
        
        # Check event data
        event_data = call_args[0][1]
        assert event_data["event_type"] == "thinking_start"
    
    @pytest.mark.asyncio
    async def test_emit_thinking_end(self, emitter, mock_redis):
        """Test emitting thinking end event"""
        mock_redis.incr.return_value = 2
        
        seq = await emitter.emit_thinking_end("req_123", duration_ms=1500)
        
        assert seq == 2
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "thinking_end"
        assert '"duration_ms":1500' in event_data["data"] or '"duration_ms": 1500' in event_data["data"]


class TestToolEvents:
    """Test tool event emission"""
    
    @pytest.mark.asyncio
    async def test_emit_tool_start(self, emitter, mock_redis):
        """Test emitting tool start event"""
        mock_redis.incr.return_value = 3
        
        seq = await emitter.emit_tool_start(
            "req_123",
            "validate_process_inputs",
            {"cane_input": 100, "cane_pol": 14},
            estimated_duration_ms=2000
        )
        
        assert seq == 3
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "tool_start"
        assert '"tool_name":' in event_data["data"] and '"validate_process_inputs"' in event_data["data"]
    
    @pytest.mark.asyncio
    async def test_emit_tool_end_success(self, emitter, mock_redis):
        """Test emitting successful tool end event"""
        mock_redis.incr.return_value = 4
        
        seq = await emitter.emit_tool_end(
            "req_123",
            "validate_process_inputs",
            "success",
            duration_ms=2347,
            summary="Validation passed"
        )
        
        assert seq == 4
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "tool_end"
        assert '"status":' in event_data["data"] and '"success"' in event_data["data"]
    
    @pytest.mark.asyncio
    async def test_emit_tool_end_error(self, emitter, mock_redis):
        """Test emitting failed tool end event"""
        mock_redis.incr.return_value = 5
        
        seq = await emitter.emit_tool_end(
            "req_123",
            "simulate_process",
            ToolStatus.ERROR,  # Can pass enum directly
            duration_ms=500,
            summary="Simulation failed",
            error_message="ValueError: cane_pol exceeds maximum"
        )
        
        assert seq == 5
        
        # Check error message in data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert '"status":' in event_data["data"] and '"error"' in event_data["data"]
        assert '"error_message"' in event_data["data"]


class TestAgentTextEvents:
    """Test agent text event emission"""

    @pytest.mark.asyncio
    async def test_emit_agent_text(self, emitter, mock_redis):
        """Test emitting agent text event"""
        mock_redis.incr.return_value = 6

        seq = await emitter.emit_agent_text(
            "req_123",
            content="Let me check the processes",
            iteration=0
        )

        assert seq == 6
        mock_redis.xadd.assert_called_once()

        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "agent_text"
        assert '"content"' in event_data["data"]
        assert "Let me check the processes" in event_data["data"]
        assert '"iteration"' in event_data["data"]

    @pytest.mark.asyncio
    async def test_emit_agent_text_with_iteration(self, emitter, mock_redis):
        """Test emitting agent text with correct iteration number"""
        mock_redis.incr.return_value = 7

        seq = await emitter.emit_agent_text(
            "req_123",
            content="Now checking results",
            iteration=2
        )

        assert seq == 7
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert '"iteration":2' in event_data["data"] or '"iteration": 2' in event_data["data"]


class TestProgressEvents:
    """Test progress event emission"""
    
    @pytest.mark.asyncio
    async def test_emit_run_progress(self, emitter, mock_redis):
        """Test emitting progress event"""
        mock_redis.incr.return_value = 10
        
        seq = await emitter.emit_run_progress(
            "req_123",
            stage="Mill",
            percentage=33,
            message="Processing mill inputs",
            current_block=3,
            total_blocks=9
        )
        
        assert seq == 10
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "run_progress"
        assert '"stage":' in event_data["data"] and '"Mill"' in event_data["data"]
        assert '"percentage":33' in event_data["data"] or '"percentage": 33' in event_data["data"]


class TestMessageEvents:
    """Test message event emission"""
    
    @pytest.mark.asyncio
    async def test_emit_message_delta(self, emitter, mock_redis):
        """Test emitting message delta"""
        mock_redis.incr.return_value = 15
        
        seq = await emitter.emit_message_delta(
            "req_123",
            delta="The simulation ",
            accumulated_length=15
        )
        
        assert seq == 15
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "message_delta"
    
    @pytest.mark.asyncio
    async def test_emit_message_final(self, emitter, mock_redis):
        """Test emitting final message"""
        mock_redis.incr.return_value = 20
        
        seq = await emitter.emit_message_final(
            "req_123",
            content="## Results\n\nSimulation complete!",
            role="assistant",
            metadata={"tool_calls": 3, "tokens": 1234}
        )
        
        assert seq == 20
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "message_final"


class TestErrorEvents:
    """Test error event emission"""
    
    @pytest.mark.asyncio
    async def test_emit_app_error(self, emitter, mock_redis):
        """Test emitting application error"""
        mock_redis.incr.return_value = 25
        
        seq = await emitter.emit_app_error(
            "req_123",
            error_type="policy_violation",
            error_message="Must validate before simulating",
            details={"tool": "simulate_process"},
            recoverable=True
        )
        
        assert seq == 25
        mock_redis.xadd.assert_called_once()
        
        # Check event data
        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]
        assert event_data["event_type"] == "app_error"
        assert '"error_type":' in event_data["data"] and '"policy_violation"' in event_data["data"]


class TestEventRetrieval:
    """Test event retrieval"""
    
    @pytest.mark.asyncio
    async def test_get_events_empty(self, emitter, mock_redis):
        """Test getting events when none exist"""
        mock_redis.xrange.return_value = []
        
        events = await emitter.get_events("req_123")
        
        assert events == []
        mock_redis.xrange.assert_called_once_with(
            "events:req_123",
            min='-',
            max='+',
            count=None
        )
    
    @pytest.mark.asyncio
    async def test_get_events_with_data(self, emitter, mock_redis):
        """Test getting events with data"""
        # Mock Redis response with actual event data
        event1 = ThinkingStartEvent(request_id="req_123", sequence=1)
        event2 = ThinkingEndEvent(request_id="req_123", sequence=2, duration_ms=1000)
        
        mock_redis.xrange.return_value = [
            (b'1234567890-0', event1.to_redis_dict()),
            (b'1234567891-0', event2.to_redis_dict()),
        ]
        
        events = await emitter.get_events("req_123")
        
        assert len(events) == 2
        assert isinstance(events[0], ThinkingStartEvent)
        assert isinstance(events[1], ThinkingEndEvent)
        assert events[0].sequence == 1
        assert events[1].sequence == 2
    
    @pytest.mark.asyncio
    async def test_get_events_after_sequence(self, emitter, mock_redis):
        """Test getting events after specific sequence"""
        # Mock events with sequences 1, 2, 3, 4, 5
        events_data = []
        for i in range(1, 6):
            event = ThinkingStartEvent(request_id="req_123", sequence=i)
            events_data.append((f'{1234567890+i}-0'.encode(), event.to_redis_dict()))
        
        mock_redis.xrange.return_value = events_data
        
        # Get events after sequence 3
        events = await emitter.get_events("req_123", after_sequence=3)
        
        # Should only get events 4 and 5
        assert len(events) == 2
        assert events[0].sequence == 4
        assert events[1].sequence == 5
    
    @pytest.mark.asyncio
    async def test_get_event_count(self, emitter, mock_redis):
        """Test getting event count"""
        mock_redis.xlen.return_value = 42
        
        count = await emitter.get_event_count("req_123")
        
        assert count == 42
        mock_redis.xlen.assert_called_once_with("events:req_123")


class TestEventCleanup:
    """Test event cleanup and management"""
    
    @pytest.mark.asyncio
    async def test_clear_events(self, emitter, mock_redis):
        """Test clearing all events for a request"""
        mock_redis.delete.return_value = 2
        
        result = await emitter.clear_events("req_123")
        
        assert result is True
        mock_redis.delete.assert_called_once()
        
        # Check both keys were deleted
        call_args = mock_redis.delete.call_args[0]
        assert "events:req_123" in call_args
        assert "events:req_123:seq" in call_args
    
    @pytest.mark.asyncio
    async def test_ttl_set_on_first_event(self, emitter, mock_redis):
        """Test TTL is set when first event is emitted"""
        mock_redis.incr.return_value = 1  # First event
        
        await emitter.emit_thinking_start("req_123")
        
        # TTL should be set on both stream and sequence counter
        assert mock_redis.expire.call_count == 2  # seq key + stream key
        
        # Check TTL value
        expire_calls = mock_redis.expire.call_args_list
        for call in expire_calls:
            assert call[0][1] == 3600  # Default TTL


class TestErrorHandling:
    """Test error handling in EventEmitter"""
    
    @pytest.mark.asyncio
    async def test_emit_event_redis_failure(self, emitter, mock_redis):
        """Test that Redis failures don't crash (best-effort events)"""
        mock_redis.incr.side_effect = Exception("Redis connection failed")
        
        # Should not raise, returns -1
        seq = await emitter.emit_thinking_start("req_123")
        
        assert seq == -1
    
    @pytest.mark.asyncio
    async def test_get_events_redis_failure(self, emitter, mock_redis):
        """Test graceful handling of Redis failures during retrieval"""
        mock_redis.xrange.side_effect = Exception("Redis connection failed")
        
        # Should not raise, returns empty list
        events = await emitter.get_events("req_123")
        
        assert events == []


class TestMaxStreamLength:
    """Test max stream length enforcement"""
    
    @pytest.mark.asyncio
    async def test_xadd_with_maxlen(self, emitter, mock_redis):
        """Test that XADD uses MAXLEN to limit stream size"""
        mock_redis.incr.return_value = 1
        
        await emitter.emit_thinking_start("req_123")
        
        # Check MAXLEN was passed to xadd
        call_args = mock_redis.xadd.call_args
        assert call_args[1]['maxlen'] == 10000  # Default max_stream_length
        assert call_args[1]['approximate'] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
