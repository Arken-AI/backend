"""
Tests for SSE Streaming Endpoint

Tests the real-time event streaming endpoint with various scenarios:
- Basic event streaming
- Replay from specific sequence
- Real-time updates
- Keepalive heartbeat
- Multiple concurrent streams
- Client disconnect handling
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_event_emitter
from app.services.event_emitter import EventEmitter
from app.models.events import (
    ThinkingStartEvent,
    ToolStartEvent,
    ToolEndEvent,
    RunProgressEvent,
    MessageDeltaEvent,
    MessageFinalEvent,
    AppErrorEvent,
    ToolStatus,
    ErrorType
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_redis():
    """Mock Redis client for testing"""
    redis_mock = AsyncMock()
    redis_mock.incr = AsyncMock(side_effect=lambda key: 1)
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.xadd = AsyncMock(return_value=b"1234567890-0")
    redis_mock.xrange = AsyncMock(return_value=[])
    redis_mock.xlen = AsyncMock(return_value=0)
    redis_mock.delete = AsyncMock(return_value=1)
    return redis_mock


@pytest.fixture
def mock_event_emitter(mock_redis):
    """Mock EventEmitter service"""
    return EventEmitter(mock_redis)


@pytest.fixture
def client(mock_event_emitter):
    """FastAPI test client with mocked dependencies"""
    
    async def override_get_event_emitter():
        return mock_event_emitter
    
    app.dependency_overrides[get_event_emitter] = override_get_event_emitter
    
    with TestClient(app) as test_client:
        yield test_client
    
    # Cleanup
    app.dependency_overrides.clear()


# =============================================================================
# Test 1: Basic SSE Streaming
# =============================================================================

class TestBasicStreaming:
    """Test basic SSE streaming functionality"""
    
    @pytest.mark.asyncio
    async def test_stream_empty_request(self, client, mock_event_emitter):
        """Test streaming when no events exist"""
        # Setup: Return empty list first, then message_final to terminate
        call_count = 0
        def get_events_side_effect(request_id, after_sequence=0, count=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            else:
                # Send final event on second call to terminate stream
                return [MessageFinalEvent(request_id=request_id, content="Done", sequence=1)]
        
        mock_event_emitter.get_events = AsyncMock(side_effect=get_events_side_effect)
        
        # Test: Open stream
        with client.stream("GET", "/api/chat/req_empty/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
            assert response.headers["cache-control"] == "no-cache"
            
            # Read until stream terminates
            lines = []
            for line in response.iter_lines():
                lines.append(line)
                if line.startswith("data: ") and "message_final" in line:
                    break
    
    @pytest.mark.asyncio
    async def test_stream_with_existing_events(self, client, mock_event_emitter):
        """Test streaming when events already exist"""
        # Setup: Create events in memory
        events = [
            ThinkingStartEvent(request_id="req_123"),
            ToolStartEvent(
                request_id="req_123",
                tool_name="validate_process_inputs",
                arguments={"block": 1}
            ),
            MessageFinalEvent(
                request_id="req_123",
                content="Validation complete"
            )
        ]
        
        # Assign sequences
        for i, event in enumerate(events, 1):
            event.sequence = i
        
        # Mock get_events to return these
        mock_event_emitter.get_events = AsyncMock(return_value=events)
        
        # Test: Open stream
        with client.stream("GET", "/api/chat/req_123/stream") as response:
            assert response.status_code == 200
            
            # Read events from stream
            received_events = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    import json
                    event_data = json.loads(line[6:])
                    received_events.append(event_data)
                    
                    # Stop after message_final
                    if event_data.get("event_type") == "message_final":
                        break
            
            # Assert: Got all 3 events
            assert len(received_events) == 3
            assert received_events[0]["event_type"] == "thinking_start"
            assert received_events[1]["event_type"] == "tool_start"
            assert received_events[2]["event_type"] == "message_final"
    
    @pytest.mark.asyncio
    async def test_stream_auto_terminates_on_message_final(self, client, mock_event_emitter):
        """Test that stream terminates when message_final is received"""
        # Setup: Only message_final event
        final_event = MessageFinalEvent(
            request_id="req_final",
            content="Done",
            sequence=1
        )
        
        mock_event_emitter.get_events = AsyncMock(return_value=[final_event])
        
        # Test: Stream should close after message_final
        with client.stream("GET", "/api/chat/req_final/stream") as response:
            lines = list(response.iter_lines())
            
            # Should have data line and that's it
            data_lines = [l for l in lines if l.startswith("data: ")]
            assert len(data_lines) == 1


# =============================================================================
# Test 2: Replay from Sequence
# =============================================================================

class TestReplay:
    """Test replay functionality (reconnection support)"""
    
    @pytest.mark.asyncio
    async def test_stream_after_sequence(self, client, mock_event_emitter):
        """Test streaming only events after specific sequence"""
        # Setup: 5 events exist, we want events after sequence 2
        all_events = []
        for i in range(1, 6):
            event = RunProgressEvent(
                request_id="req_replay",
                stage="Mill",
                percentage=i * 20,
                sequence=i
            )
            all_events.append(event)
        
        # Mock get_events to filter by sequence (simulate real behavior)
        async def filtered_get_events(request_id, after_sequence=0, count=None):
            return [e for e in all_events if e.sequence > after_sequence]
        
        mock_event_emitter.get_events = AsyncMock(side_effect=filtered_get_events)
        
        # Add final event to terminate stream
        final_event = MessageFinalEvent(
            request_id="req_replay",
            content="Done",
            sequence=6
        )
        all_events.append(final_event)
        
        # Test: Request events after sequence 2
        with client.stream("GET", "/api/chat/req_replay/stream?after_sequence=2") as response:
            received_events = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    import json
                    event_data = json.loads(line[6:])
                    received_events.append(event_data)
                    if event_data.get("event_type") == "message_final":
                        break
            
            # Assert: Only got events 3, 4, 5, 6 (final)
            assert len(received_events) == 4
            assert received_events[0]["sequence"] == 3
            assert received_events[1]["sequence"] == 4
            assert received_events[2]["sequence"] == 5
            assert received_events[3]["sequence"] == 6


# =============================================================================
# Test 3: SSE Format Validation
# =============================================================================

class TestSSEFormat:
    """Test SSE protocol format compliance"""
    
    @pytest.mark.asyncio
    async def test_sse_content_type(self, client, mock_event_emitter):
        """Test that response has correct content-type"""
        final_event = MessageFinalEvent(request_id="req_headers", content="Done", sequence=1)
        mock_event_emitter.get_events = AsyncMock(return_value=[final_event])
        
        with client.stream("GET", "/api/chat/req_headers/stream") as response:
            assert "text/event-stream" in response.headers["content-type"]
            # Consume stream to allow it to terminate
            for line in response.iter_lines():
                if line.startswith("data: ") and "message_final" in line:
                    break
    
    @pytest.mark.asyncio
    async def test_sse_headers(self, client, mock_event_emitter):
        """Test that response has required SSE headers"""
        final_event = MessageFinalEvent(request_id="req_headers", content="Done", sequence=1)
        mock_event_emitter.get_events = AsyncMock(return_value=[final_event])
        
        with client.stream("GET", "/api/chat/req_headers/stream") as response:
            # Check required headers
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["connection"] == "keep-alive"
            assert response.headers.get("x-accel-buffering") == "no"
            # Consume stream to allow it to terminate
            for line in response.iter_lines():
                if line.startswith("data: ") and "message_final" in line:
                    break
    
    @pytest.mark.asyncio
    async def test_sse_data_format(self, client, mock_event_emitter):
        """Test that events are in proper SSE format"""
        event = ThinkingStartEvent(request_id="req_format", sequence=1)
        mock_event_emitter.get_events = AsyncMock(return_value=[event])
        
        # Add final event to terminate
        final = MessageFinalEvent(request_id="req_format", content="Done", sequence=2)
        mock_event_emitter.get_events = AsyncMock(return_value=[event, final])
        
        with client.stream("GET", "/api/chat/req_format/stream") as response:
            lines = list(response.iter_lines())
            
            # Find first data line
            data_line = next((l for l in lines if l.startswith("data: ")), None)
            assert data_line is not None
            
            # Should be valid JSON
            import json
            data = json.loads(data_line[6:])
            assert "event_type" in data
            assert "sequence" in data


# =============================================================================
# Test 4: Error Handling
# =============================================================================

class TestErrorHandling:
    """Test error handling in SSE stream"""
    
    @pytest.mark.asyncio
    async def test_stream_redis_error(self, client, mock_event_emitter):
        """Test handling of Redis errors during streaming"""
        # Setup: Redis raises error first, then returns final event
        call_count = 0
        def get_events_side_effect(request_id, after_sequence=0, count=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Redis connection failed")
            else:
                # Send final event to terminate stream
                return [MessageFinalEvent(request_id=request_id, content="Done", sequence=1)]
        
        mock_event_emitter.get_events = AsyncMock(side_effect=get_events_side_effect)
        
        # Test: Stream should handle error gracefully
        with client.stream("GET", "/api/chat/req_error/stream") as response:
            assert response.status_code == 200
            
            # Stream should eventually send final event and terminate
            lines = []
            for line in response.iter_lines():
                lines.append(line)
                if line.startswith("data: ") and "message_final" in line:
                    break
            
            # Stream opened successfully even with initial errors
            assert len(lines) > 0
    
    @pytest.mark.asyncio
    async def test_stream_invalid_request_id(self, client, mock_event_emitter):
        """Test streaming with invalid request_id"""
        # Setup: Return final event to terminate stream
        final_event = MessageFinalEvent(request_id="invalid_request", content="Done", sequence=1)
        mock_event_emitter.get_events = AsyncMock(return_value=[final_event])
        
        # Test: Should still work, just no events
        with client.stream("GET", "/api/chat/invalid_request/stream") as response:
            assert response.status_code == 200
            # Consume stream to allow it to terminate
            for line in response.iter_lines():
                if line.startswith("data: ") and "message_final" in line:
                    break


# =============================================================================
# Test 5: Query Parameters
# =============================================================================

class TestQueryParameters:
    """Test query parameter handling"""
    
    @pytest.mark.asyncio
    async def test_after_sequence_parameter(self, client, mock_event_emitter):
        """Test after_sequence query parameter"""
        final_event = MessageFinalEvent(request_id="req_param", content="Done", sequence=1)
        mock_event_emitter.get_events = AsyncMock(return_value=[final_event])
        
        # Test with after_sequence
        with client.stream("GET", "/api/chat/req_param/stream?after_sequence=10") as response:
            assert response.status_code == 200
            # Consume stream to allow it to terminate
            for line in response.iter_lines():
                if line.startswith("data: ") and "message_final" in line:
                    break
        
        # Verify get_events was called with correct parameter
        mock_event_emitter.get_events.assert_called()
        call_args = mock_event_emitter.get_events.call_args
        assert call_args[1]["after_sequence"] == 10
    
    @pytest.mark.asyncio
    async def test_default_after_sequence(self, client, mock_event_emitter):
        """Test default after_sequence value"""
        final_event = MessageFinalEvent(request_id="req_default", content="Done", sequence=1)
        mock_event_emitter.get_events = AsyncMock(return_value=[final_event])
        
        # Test without after_sequence parameter
        with client.stream("GET", "/api/chat/req_default/stream") as response:
            assert response.status_code == 200
            # Consume stream to allow it to terminate
            for line in response.iter_lines():
                if line.startswith("data: ") and "message_final" in line:
                    break
        
        # Should default to 0
        call_args = mock_event_emitter.get_events.call_args
        assert call_args[1]["after_sequence"] == 0
    
    @pytest.mark.asyncio
    async def test_negative_after_sequence_rejected(self, client, mock_event_emitter):
        """Test that negative after_sequence is rejected"""
        # Test with negative value
        response = client.get("/api/chat/req_negative/stream?after_sequence=-5")
        
        # Should return 422 validation error
        assert response.status_code == 422


# =============================================================================
# Test 6: Integration Scenarios
# =============================================================================

class TestIntegrationScenarios:
    """Test realistic end-to-end scenarios"""
    
    @pytest.mark.asyncio
    async def test_typical_calculation_flow(self, client, mock_event_emitter):
        """Test streaming a typical calculation workflow"""
        # Setup: Simulate complete calculation flow
        events = [
            ThinkingStartEvent(request_id="req_calc", sequence=1),
            ToolStartEvent(
                request_id="req_calc",
                tool_name="validate_process_inputs",
                arguments={},
                sequence=2
            ),
            ToolEndEvent(
                request_id="req_calc",
                tool_name="validate_process_inputs",
                status=ToolStatus.SUCCESS,
                duration_ms=500,
                summary="Validation passed",
                sequence=3
            ),
            RunProgressEvent(
                request_id="req_calc",
                stage="Mill",
                percentage=33,
                sequence=4
            ),
            RunProgressEvent(
                request_id="req_calc",
                stage="Heater",
                percentage=67,
                sequence=5
            ),
            MessageDeltaEvent(
                request_id="req_calc",
                delta="Calculation ",
                accumulated_length=12,
                sequence=6
            ),
            MessageDeltaEvent(
                request_id="req_calc",
                delta="complete!",
                accumulated_length=21,
                sequence=7
            ),
            MessageFinalEvent(
                request_id="req_calc",
                content="Calculation complete!",
                sequence=8
            )
        ]
        
        mock_event_emitter.get_events = AsyncMock(return_value=events)
        
        # Test: Stream entire flow
        with client.stream("GET", "/api/chat/req_calc/stream") as response:
            received_events = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    import json
                    event_data = json.loads(line[6:])
                    received_events.append(event_data)
                    if event_data.get("event_type") == "message_final":
                        break
            
            # Assert: Got all 8 events in correct order
            assert len(received_events) == 8
            assert received_events[0]["event_type"] == "thinking_start"
            assert received_events[1]["event_type"] == "tool_start"
            assert received_events[2]["event_type"] == "tool_end"
            assert received_events[3]["event_type"] == "run_progress"
            assert received_events[3]["percentage"] == 33
            assert received_events[7]["event_type"] == "message_final"
