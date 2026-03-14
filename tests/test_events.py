"""
Unit tests for Event Models

Tests all event types, serialization, validation, and helper methods.
"""

import pytest
import json
from datetime import datetime
from app.models.events import (
    BaseEvent,
    ThinkingStartEvent,
    ThinkingEndEvent,
    ToolStartEvent,
    ToolEndEvent,
    RunProgressEvent,
    MessageDeltaEvent,
    MessageFinalEvent,
    AppErrorEvent,
    AgentTextEvent,
    EventType,
    ToolStatus,
    ErrorType,
    parse_event,
    get_all_event_types,
    EVENT_TYPE_MAP,
)


class TestBaseEvent:
    """Test BaseEvent class and common functionality"""
    
    def test_base_event_creation(self):
        """Test basic event creation with required fields"""
        event = BaseEvent(
            request_id="req_123",
            event_type=EventType.THINKING_START
        )
        
        assert event.request_id == "req_123"
        assert event.event_type == EventType.THINKING_START
        assert isinstance(event.timestamp, datetime)
        assert event.sequence is None  # Not assigned yet
    
    def test_base_event_with_sequence(self):
        """Test event with sequence number assigned"""
        event = BaseEvent(
            request_id="req_123",
            event_type=EventType.THINKING_START,
            sequence=42
        )
        
        assert event.sequence == 42
    
    def test_to_sse_format(self):
        """Test SSE format conversion"""
        event = ThinkingStartEvent(
            request_id="req_123",
            sequence=5
        )
        
        sse = event.to_sse_format()
        
        assert "id: 5" in sse
        assert "event: thinking_start" in sse
        assert "data: " in sse
        # Accept both with and without spaces after colons
        assert '"request_id": "req_123"' in sse or '"request_id":"req_123"' in sse
    
    def test_to_sse_format_without_sequence(self):
        """Test SSE format when sequence is None"""
        event = ThinkingStartEvent(request_id="req_123")
        
        sse = event.to_sse_format()
        
        assert "id:" not in sse  # No id line
        assert "event: thinking_start" in sse
        assert "data: " in sse
    
    def test_to_redis_dict(self):
        """Test Redis dictionary conversion"""
        event = ThinkingStartEvent(
            request_id="req_123",
            sequence=5
        )
        
        redis_dict = event.to_redis_dict()
        
        assert "event_type" in redis_dict
        assert "data" in redis_dict
        assert redis_dict["event_type"] == "thinking_start"
        
        # Data should be valid JSON
        data = json.loads(redis_dict["data"])
        assert data["request_id"] == "req_123"
        assert data["sequence"] == 5
    
    def test_from_redis_dict(self):
        """Test deserialization from Redis"""
        original = ThinkingStartEvent(
            request_id="req_123",
            sequence=5
        )
        
        redis_dict = original.to_redis_dict()
        restored = BaseEvent.from_redis_dict(redis_dict)
        
        assert isinstance(restored, ThinkingStartEvent)
        assert restored.request_id == original.request_id
        assert restored.sequence == original.sequence
    
    def test_json_serialization(self):
        """Test JSON serialization includes all fields"""
        event = ThinkingStartEvent(
            request_id="req_123",
            sequence=5
        )
        
        # Use to_sse_format which adds the Z
        sse = event.to_sse_format()
        # Extract data line
        data_line = [line for line in sse.split('\n') if line.startswith('data: ')][0]
        json_str = data_line[6:]  # Remove 'data: ' prefix
        data = json.loads(json_str)
        
        assert data["request_id"] == "req_123"
        assert data["event_type"] == "thinking_start"
        assert data["sequence"] == 5
        assert "timestamp" in data
        # Timestamp should end with 'Z' (UTC indicator) in SSE format
        assert data["timestamp"].endswith("Z")


class TestThinkingEvents:
    """Test thinking start and end events"""
    
    def test_thinking_start_event(self):
        """Test ThinkingStartEvent creation"""
        event = ThinkingStartEvent(request_id="req_123")
        
        assert event.event_type == EventType.THINKING_START
        assert event.request_id == "req_123"
    
    def test_thinking_end_event(self):
        """Test ThinkingEndEvent with duration"""
        event = ThinkingEndEvent(
            request_id="req_123",
            duration_ms=1234
        )
        
        assert event.event_type == EventType.THINKING_END
        assert event.duration_ms == 1234
    
    def test_thinking_end_requires_duration(self):
        """Test that ThinkingEndEvent requires duration_ms"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            ThinkingEndEvent(request_id="req_123")


class TestToolEvents:
    """Test tool start and end events"""
    
    def test_tool_start_event(self):
        """Test ToolStartEvent creation"""
        event = ToolStartEvent(
            request_id="req_123",
            tool_name="validate_process_inputs",
            arguments={"cane_input": 100, "cane_pol": 14}
        )
        
        assert event.event_type == EventType.TOOL_START
        assert event.tool_name == "validate_process_inputs"
        assert event.arguments["cane_input"] == 100
        assert event.estimated_duration_ms is None
    
    def test_tool_start_with_estimated_duration(self):
        """Test ToolStartEvent with estimated duration"""
        event = ToolStartEvent(
            request_id="req_123",
            tool_name="simulate_process",
            arguments={},
            estimated_duration_ms=30000
        )
        
        assert event.estimated_duration_ms == 30000
    
    def test_tool_end_event_success(self):
        """Test successful tool execution"""
        event = ToolEndEvent(
            request_id="req_123",
            tool_name="validate_process_inputs",
            status=ToolStatus.SUCCESS,
            duration_ms=2347,
            summary="Validation passed. All inputs valid."
        )
        
        assert event.event_type == EventType.TOOL_END
        assert event.status == ToolStatus.SUCCESS
        assert event.duration_ms == 2347
        assert event.error_message is None
    
    def test_tool_end_event_error(self):
        """Test failed tool execution"""
        event = ToolEndEvent(
            request_id="req_123",
            tool_name="simulate_process",
            status=ToolStatus.ERROR,
            duration_ms=500,
            summary="Simulation failed",
            error_message="ValueError: cane_pol exceeds maximum",
            result_id="run_abc_error"
        )
        
        assert event.status == ToolStatus.ERROR
        assert event.error_message is not None
        assert "cane_pol" in event.error_message
    
    def test_tool_end_requires_fields(self):
        """Test that ToolEndEvent requires all mandatory fields"""
        with pytest.raises(Exception):
            ToolEndEvent(request_id="req_123")


class TestRunProgressEvent:
    """Test run progress events"""
    
    def test_run_progress_event(self):
        """Test RunProgressEvent creation"""
        event = RunProgressEvent(
            request_id="req_123",
            stage="Mill",
            percentage=33
        )
        
        assert event.event_type == EventType.RUN_PROGRESS
        assert event.stage == "Mill"
        assert event.percentage == 33
        assert event.message is None
    
    def test_run_progress_with_details(self):
        """Test RunProgressEvent with all optional fields"""
        event = RunProgressEvent(
            request_id="req_123",
            stage="Clarifier",
            percentage=45,
            message="Settling calculations in progress",
            current_block=3,
            total_blocks=9
        )
        
        assert event.message == "Settling calculations in progress"
        assert event.current_block == 3
        assert event.total_blocks == 9
    
    def test_percentage_validation(self):
        """Test percentage must be 0-100"""
        # Valid percentages
        event1 = RunProgressEvent(request_id="req_123", stage="Mill", percentage=0)
        assert event1.percentage == 0
        
        event2 = RunProgressEvent(request_id="req_123", stage="Mill", percentage=100)
        assert event2.percentage == 100
        
        # Invalid percentages
        with pytest.raises(Exception):  # Pydantic ValidationError
            RunProgressEvent(request_id="req_123", stage="Mill", percentage=-1)
        
        with pytest.raises(Exception):
            RunProgressEvent(request_id="req_123", stage="Mill", percentage=101)


class TestMessageEvents:
    """Test message delta and final events"""
    
    def test_message_delta_event(self):
        """Test MessageDeltaEvent for streaming"""
        event = MessageDeltaEvent(
            request_id="req_123",
            delta="The simulation ",
            accumulated_length=15
        )
        
        assert event.event_type == EventType.MESSAGE_DELTA
        assert event.delta == "The simulation "
        assert event.accumulated_length == 15
    
    def test_message_final_event(self):
        """Test MessageFinalEvent"""
        content = "## Results\n\nSimulation complete!"
        event = MessageFinalEvent(
            request_id="req_123",
            content=content
        )
        
        assert event.event_type == EventType.MESSAGE_FINAL
        assert event.content == content
        assert event.role == "assistant"  # Default
        assert event.metadata is None
    
    def test_message_final_with_metadata(self):
        """Test MessageFinalEvent with metadata"""
        event = MessageFinalEvent(
            request_id="req_123",
            content="Results here",
            role="assistant",
            metadata={"tool_calls": 3, "tokens": 1234}
        )
        
        assert event.metadata["tool_calls"] == 3
        assert event.metadata["tokens"] == 1234


class TestAppErrorEvent:
    """Test error events"""
    
    def test_app_error_policy_violation(self):
        """Test policy violation error"""
        event = AppErrorEvent(
            request_id="req_123",
            error_type=ErrorType.POLICY_VIOLATION,
            error_message="Must validate before simulating",
            recoverable=True
        )
        
        assert event.event_type == EventType.APP_ERROR
        assert event.error_type == ErrorType.POLICY_VIOLATION
        assert event.recoverable is True
    
    def test_app_error_system_error(self):
        """Test system error (not recoverable)"""
        event = AppErrorEvent(
            request_id="req_123",
            error_type=ErrorType.SYSTEM_ERROR,
            error_message="Database connection failed",
            details={
                "service": "mongodb",
                "error_code": "CONNECTION_TIMEOUT"
            },
            recoverable=False
        )
        
        assert event.error_type == ErrorType.SYSTEM_ERROR
        assert event.recoverable is False
        assert event.details["service"] == "mongodb"
    
    def test_app_error_with_technical_details(self):
        """Test error with technical details"""
        event = AppErrorEvent(
            request_id="req_123",
            error_type=ErrorType.TOOL_ERROR,
            error_message="Calculation failed",
            details={
                "tool": "simulate_process",
                "exception": "ValueError",
                "traceback": "line 123..."
            }
        )
        
        assert "tool" in event.details
        assert event.details["tool"] == "simulate_process"


class TestAgentTextEvent:
    """Test agent text events"""

    def test_agent_text_event_creation(self):
        """Test AgentTextEvent creation"""
        event = AgentTextEvent(
            request_id="req_123",
            content="Let me check the available processes",
            iteration=0
        )
        assert event.event_type == EventType.AGENT_TEXT
        assert event.content == "Let me check the available processes"
        assert event.iteration == 0

    def test_agent_text_event_requires_content(self):
        """Test that AgentTextEvent requires content"""
        with pytest.raises(Exception):
            AgentTextEvent(request_id="req_123", iteration=0)

    def test_agent_text_event_requires_iteration(self):
        """Test that AgentTextEvent requires iteration"""
        with pytest.raises(Exception):
            AgentTextEvent(request_id="req_123", content="text")

    def test_agent_text_sse_format(self):
        """Test AgentTextEvent SSE serialization"""
        event = AgentTextEvent(
            request_id="req_123",
            content="Checking processes",
            iteration=1,
            sequence=3
        )
        sse = event.to_sse_format()
        assert "id: 3" in sse
        assert "event: agent_text" in sse
        assert "Checking processes" in sse

    def test_agent_text_redis_round_trip(self):
        """Test AgentTextEvent round-trip via Redis dict"""
        original = AgentTextEvent(
            request_id="req_123",
            content="Intermediate text",
            iteration=2,
            sequence=5
        )
        redis_dict = original.to_redis_dict()
        restored = BaseEvent.from_redis_dict(redis_dict)

        assert isinstance(restored, AgentTextEvent)
        assert restored.content == "Intermediate text"
        assert restored.iteration == 2
        assert restored.sequence == 5

    def test_agent_text_in_event_type_map(self):
        """Test that agent_text is in EVENT_TYPE_MAP"""
        assert "agent_text" in EVENT_TYPE_MAP or EventType.AGENT_TEXT in EVENT_TYPE_MAP


class TestEventParsing:
    """Test event parsing and factory functions"""
    
    def test_parse_event_thinking_start(self):
        """Test parsing ThinkingStartEvent"""
        data = {
            "request_id": "req_123",
            "event_type": "thinking_start",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "sequence": 1
        }
        
        event = parse_event(data)
        
        assert isinstance(event, ThinkingStartEvent)
        assert event.request_id == "req_123"
        assert event.sequence == 1
    
    def test_parse_event_tool_end(self):
        """Test parsing ToolEndEvent"""
        data = {
            "request_id": "req_123",
            "event_type": "tool_end",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "sequence": 5,
            "tool_name": "validate_process_inputs",
            "status": "success",
            "duration_ms": 2000,
            "summary": "Validation passed"
        }
        
        event = parse_event(data)
        
        assert isinstance(event, ToolEndEvent)
        assert event.tool_name == "validate_process_inputs"
        assert event.status == ToolStatus.SUCCESS
    
    def test_parse_event_unknown_type(self):
        """Test parsing with unknown event type"""
        data = {
            "request_id": "req_123",
            "event_type": "unknown_event",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        with pytest.raises(ValueError, match="Unknown event type"):
            parse_event(data)
    
    def test_get_all_event_types(self):
        """Test getting list of all event types"""
        types = get_all_event_types()
        
        assert len(types) == 9  # We have 9 event types
        assert "thinking_start" in types
        assert "thinking_end" in types
        assert "tool_start" in types
        assert "tool_end" in types
        assert "run_progress" in types
        assert "message_delta" in types
        assert "message_final" in types
        assert "app_error" in types
    
    def test_event_type_map_completeness(self):
        """Test that EVENT_TYPE_MAP has all event types"""
        all_types = get_all_event_types()
        
        for event_type in all_types:
            assert event_type in EVENT_TYPE_MAP
            assert EVENT_TYPE_MAP[event_type] is not None


class TestEventSerialization:
    """Test round-trip serialization"""
    
    def test_thinking_start_round_trip(self):
        """Test ThinkingStartEvent serialization round-trip"""
        original = ThinkingStartEvent(
            request_id="req_123",
            sequence=1
        )
        
        # To JSON and back
        json_str = original.model_dump_json()
        data = json.loads(json_str)
        restored = parse_event(data)
        
        assert isinstance(restored, ThinkingStartEvent)
        assert restored.request_id == original.request_id
        assert restored.sequence == original.sequence
    
    def test_tool_end_round_trip(self):
        """Test ToolEndEvent serialization round-trip"""
        original = ToolEndEvent(
            request_id="req_123",
            sequence=5,
            tool_name="simulate_process",
            status=ToolStatus.SUCCESS,
            duration_ms=30000,
            summary="Simulation complete",
            result_id="run_abc_123"
        )
        
        # To JSON and back
        json_str = original.model_dump_json()
        data = json.loads(json_str)
        restored = parse_event(data)
        
        assert isinstance(restored, ToolEndEvent)
        assert restored.tool_name == original.tool_name
        assert restored.status == original.status
        assert restored.result_id == original.result_id
    
    def test_run_progress_round_trip(self):
        """Test RunProgressEvent serialization round-trip"""
        original = RunProgressEvent(
            request_id="req_123",
            sequence=10,
            stage="Clarifier",
            percentage=45,
            message="Processing",
            current_block=3,
            total_blocks=9
        )
        
        # To JSON and back
        json_str = original.model_dump_json()
        data = json.loads(json_str)
        restored = parse_event(data)
        
        assert isinstance(restored, RunProgressEvent)
        assert restored.stage == original.stage
        assert restored.percentage == original.percentage
        assert restored.current_block == original.current_block


class TestEventEnums:
    """Test enum validations"""
    
    def test_event_type_enum(self):
        """Test EventType enum values"""
        assert EventType.THINKING_START.value == "thinking_start"
        assert EventType.TOOL_END.value == "tool_end"
        assert EventType.APP_ERROR.value == "app_error"
    
    def test_tool_status_enum(self):
        """Test ToolStatus enum values"""
        assert ToolStatus.SUCCESS.value == "success"
        assert ToolStatus.ERROR.value == "error"
        assert ToolStatus.CANCELLED.value == "cancelled"
    
    def test_error_type_enum(self):
        """Test ErrorType enum values"""
        assert ErrorType.POLICY_VIOLATION.value == "policy_violation"
        assert ErrorType.TOOL_ERROR.value == "tool_error"
        assert ErrorType.SYSTEM_ERROR.value == "system_error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
