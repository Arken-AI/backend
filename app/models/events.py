"""
Event Models for Real-Time SSE Streaming

Defines all event types that can be emitted during calculation execution.
Events are stored in Redis Streams and sent to frontend via Server-Sent Events (SSE).

Event Flow:
1. Orchestration service emits events during execution
2. Events stored in Redis Stream: events:{request_id}
3. SSE endpoint streams events to frontend
4. Frontend updates UI based on event type

Event Types:
- ThinkingStartEvent / ThinkingEndEvent: LLM thinking phase
- ToolStartEvent / ToolEndEvent: Tool execution (validate, simulate)
- RunProgressEvent: Progress updates during long simulations
- MessageDeltaEvent / MessageFinalEvent: Streaming LLM responses
- AppErrorEvent: Error handling and reporting
"""

from datetime import datetime
from typing import Optional, Dict, Any, Union, Type, List, Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict
from enum import Enum


class EventType(str, Enum):
    """All possible event types for type safety and validation"""
    THINKING_START = "thinking_start"
    THINKING_END = "thinking_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    RUN_PROGRESS = "run_progress"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_FINAL = "message_final"
    APP_ERROR = "app_error"


class ToolStatus(str, Enum):
    """Tool execution status"""
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class ErrorType(str, Enum):
    """Error categories for AppErrorEvent"""
    POLICY_VIOLATION = "policy_violation"
    TOOL_ERROR = "tool_error"
    LLM_ERROR = "llm_error"
    VALIDATION_ERROR = "validation_error"
    TIMEOUT_ERROR = "timeout_error"
    SYSTEM_ERROR = "system_error"
    INTERNAL_ERROR = "internal_error"


class BaseEvent(BaseModel):
    """
    Base class for all events.
    
    All events inherit from this and get:
    - request_id: Links event to specific calculation request
    - event_type: Discriminator for event type
    - timestamp: When the event occurred (ISO 8601)
    - sequence: Monotonic counter for guaranteed ordering (assigned by EventEmitter)
    
    Sequence is NOT set in __init__ - it's assigned by EventEmitter when storing to Redis.
    """
    request_id: str = Field(..., description="Unique identifier for the calculation request")
    event_type: EventType = Field(..., description="Type of event")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="When event occurred")
    sequence: Optional[int] = Field(None, description="Sequential event number (assigned by EventEmitter)")
    
    model_config = ConfigDict(
        validate_assignment=True,
        use_enum_values=True
    )
    
    def to_sse_format(self) -> str:
        """
        Convert event to SSE (Server-Sent Events) format.
        
        SSE format:
            id: {sequence}
            event: {event_type}
            data: {json}
            
        Returns:
            Formatted SSE string ready to send to client
        """
        lines = []
        
        # ID line (sequence number for replay)
        if self.sequence is not None:
            lines.append(f"id: {self.sequence}")
        
        # Event type line - use the value of the enum
        event_type_value = self.event_type.value if isinstance(self.event_type, EventType) else self.event_type
        lines.append(f"event: {event_type_value}")
        
        # Data line (full JSON) - handle datetime serialization
        data_dict = self.model_dump(mode='json')
        # Ensure timestamp is ISO format with Z
        if 'timestamp' in data_dict and isinstance(data_dict['timestamp'], str):
            if not data_dict['timestamp'].endswith('Z'):
                data_dict['timestamp'] = data_dict['timestamp'] + 'Z'
        import json
        data_json = json.dumps(data_dict)
        lines.append(f"data: {data_json}")
        
        # Empty line to end event
        lines.append("")
        
        return "\n".join(lines)
    
    def to_redis_dict(self) -> Dict[str, str]:
        """
        Convert event to Redis Stream entry format.
        
        Redis Streams store entries as field-value pairs.
        We store the entire event as JSON in a single field.
        
        Returns:
            Dict with 'data' field containing JSON
        """
        return {
            "event_type": self.event_type,
            "data": self.model_dump_json()
        }
    
    @classmethod
    def from_redis_dict(cls, data: Dict[str, Any]) -> "BaseEvent":
        """
        Deserialize event from Redis Stream entry.
        
        Args:
            data: Dict from Redis with 'data' field containing JSON
            
        Returns:
            Appropriate event instance based on event_type
        """
        import json
        
        if isinstance(data.get("data"), str):
            event_data = json.loads(data["data"])
        else:
            event_data = data
        
        # Get event type and dispatch to correct class
        event_type = event_data.get("event_type")
        event_class = EVENT_TYPE_MAP.get(event_type, BaseEvent)
        
        return event_class(**event_data)


class ThinkingStartEvent(BaseEvent):
    """
    Emitted when LLM starts thinking/processing.
    
    Triggered:
    - User sends message
    - Orchestration service calls LLM
    
    Frontend behavior:
    - Show animated spinner
    - Display "Thinking..." text
    - Disable input field
    """
    event_type: Literal[EventType.THINKING_START] = EventType.THINKING_START


class ThinkingEndEvent(BaseEvent):
    """
    Emitted when LLM finishes thinking.
    
    Triggered:
    - LLM returns response
    - Before executing any tools
    
    Frontend behavior:
    - Hide spinner
    - Show thinking duration
    - Prepare for next phase (tool execution or message)
    """
    event_type: Literal[EventType.THINKING_END] = EventType.THINKING_END
    duration_ms: int = Field(..., description="How long LLM thinking took (milliseconds)")


class ToolStartEvent(BaseEvent):
    """
    Emitted when tool execution begins.
    
    Triggered:
    - Policy check passed
    - About to call MCP tool
    
    Frontend behavior:
    - Create tool execution card
    - Show tool name and arguments
    - Start progress indicator
    """
    event_type: Literal[EventType.TOOL_START] = EventType.TOOL_START
    tool_name: str = Field(..., description="Name of tool being executed")
    arguments: Dict[str, Any] = Field(..., description="Arguments passed to tool")
    estimated_duration_ms: Optional[int] = Field(None, description="Expected duration if known")


class ToolEndEvent(BaseEvent):
    """
    Emitted when tool execution completes.
    
    Triggered:
    - Tool execution finishes (success or failure)
    - After MCP tool returns
    
    Frontend behavior:
    - Update tool card with result
    - Show success/error status
    - Display summary
    - Provide link to full results
    """
    event_type: Literal[EventType.TOOL_END] = EventType.TOOL_END
    tool_name: str = Field(..., description="Name of tool that executed")
    status: ToolStatus = Field(..., description="Execution status")
    duration_ms: int = Field(..., description="Actual execution time")
    summary: str = Field(..., description="Short human-readable result summary")
    error_message: Optional[str] = Field(None, description="Error details if status=error")
    result_id: Optional[str] = Field(None, description="Reference to full result in MongoDB")


class RunProgressEvent(BaseEvent):
    """
    Emitted during long-running simulations to show progress.
    
    Triggered:
    - During simulate_process tool execution
    - Calculation engine reports progress
    - Multiple times throughout simulation
    
    Frontend behavior:
    - Update progress bar
    - Show current stage/block
    - Display percentage complete
    """
    event_type: Literal[EventType.RUN_PROGRESS] = EventType.RUN_PROGRESS
    stage: str = Field(..., description="Current process stage (Mill, Heater, Clarifier, etc.)")
    percentage: int = Field(..., ge=0, le=100, description="Overall completion percentage (0-100)")
    message: Optional[str] = Field(None, description="Detailed status message")
    current_block: Optional[int] = Field(None, description="Current block number (e.g., 3 of 9)")
    total_blocks: Optional[int] = Field(None, description="Total number of blocks")
    
    @field_validator('percentage')
    @classmethod
    def validate_percentage(cls, v):
        """Ensure percentage is between 0 and 100"""
        if not 0 <= v <= 100:
            raise ValueError(f"Percentage must be between 0 and 100, got {v}")
        return v


class MessageDeltaEvent(BaseEvent):
    """
    Emitted during streaming LLM response (character by character).
    
    Triggered:
    - During Step B (Report Generation)
    - LLM streams response token by token
    - Many events per message
    
    Frontend behavior:
    - Append delta to current message
    - Create typing effect
    - Update in real-time
    """
    event_type: Literal[EventType.MESSAGE_DELTA] = EventType.MESSAGE_DELTA
    delta: str = Field(..., description="Incremental text chunk")
    accumulated_length: int = Field(..., description="Total character count so far")


class MessageFinalEvent(BaseEvent):
    """
    Emitted when complete LLM message is ready.
    
    Triggered:
    - After all MessageDeltaEvents
    - LLM response complete
    
    Frontend behavior:
    - Replace streaming bubble with formatted markdown
    - Render tables, headers, code blocks
    - Show copy button, regenerate option
    """
    event_type: Literal[EventType.MESSAGE_FINAL] = EventType.MESSAGE_FINAL
    content: str = Field(..., description="Complete message in markdown format")
    role: str = Field(default="assistant", description="Message role (assistant|user)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Extra info (tool_calls, tokens, etc.)")


class AppErrorEvent(BaseEvent):
    """
    Emitted when errors occur during execution.
    
    Triggered:
    - Policy violations
    - Tool execution failures
    - LLM API errors
    - System errors
    
    Frontend behavior:
    - Show error banner
    - Display user-friendly message
    - Offer recovery options if recoverable
    - Show "Contact Support" if system error
    """
    event_type: Literal[EventType.APP_ERROR] = EventType.APP_ERROR
    error_type: ErrorType = Field(..., description="Category of error")
    error_message: str = Field(..., description="User-friendly error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Technical details for debugging")
    recoverable: bool = Field(default=True, description="Can user retry/fix this error?")


# Type union for type-safe event parsing
Event = Union[
    ThinkingStartEvent,
    ThinkingEndEvent,
    ToolStartEvent,
    ToolEndEvent,
    RunProgressEvent,
    MessageDeltaEvent,
    MessageFinalEvent,
    AppErrorEvent
]

# Mapping from event_type string to event class (for deserialization)
EVENT_TYPE_MAP: Dict[str, Type[BaseEvent]] = {
    EventType.THINKING_START: ThinkingStartEvent,
    EventType.THINKING_END: ThinkingEndEvent,
    EventType.TOOL_START: ToolStartEvent,
    EventType.TOOL_END: ToolEndEvent,
    EventType.RUN_PROGRESS: RunProgressEvent,
    EventType.MESSAGE_DELTA: MessageDeltaEvent,
    EventType.MESSAGE_FINAL: MessageFinalEvent,
    EventType.APP_ERROR: AppErrorEvent,
}


def parse_event(event_data: Dict[str, Any]) -> Event:
    """
    Parse event data into appropriate event class.
    
    Factory function that reads event_type and returns
    the correct event class instance.
    
    Args:
        event_data: Dictionary with event data including event_type
        
    Returns:
        Appropriate event instance
        
    Raises:
        ValueError: If event_type is unknown
    """
    event_type = event_data.get("event_type")
    
    if event_type not in EVENT_TYPE_MAP:
        raise ValueError(f"Unknown event type: {event_type}")
    
    event_class = EVENT_TYPE_MAP[event_type]
    return event_class(**event_data)


def get_all_event_types() -> List[str]:
    """
    Get list of all valid event type strings.
    
    Useful for validation, testing, and documentation.
    
    Returns:
        List of event type strings
    """
    return [et.value for et in EventType]
