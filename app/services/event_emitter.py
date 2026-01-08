"""
Event Emitter Service

Stores events in Redis Streams for real-time SSE streaming to frontend.

Key responsibilities:
- Auto-increment sequence numbers per request
- Store events in Redis Streams
- Retrieve events for replay/SSE streaming
- Manage event TTL (1 hour expiration)

Usage:
    emitter = EventEmitter(redis_client)
    emitter.emit_tool_start("req_123", "validate_process_inputs", {...})
    emitter.emit_tool_end("req_123", "validate_process_inputs", "success", 2000, "All valid")
    
    # For SSE endpoint
    events = await emitter.get_events("req_123", after_sequence=5)
"""

import traceback
from typing import List, Optional, Dict, Any
from datetime import datetime
import redis.asyncio as redis

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
    ToolStatus,
    ErrorType,
)



class EventEmitter:
    """
    Emits and stores events in Redis Streams for real-time streaming.
    
    Redis Keys:
    - events:{request_id} - Stream of events for a request
    - events:{request_id}:seq - Sequence counter for event ordering
    
    Events expire after 1 hour (configurable TTL).
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        ttl_seconds: int = 3600,  # 1 hour default
        max_stream_length: int = 10000
    ):
        """
        Initialize Event Emitter.
        
        Args:
            redis_client: Async Redis client
            ttl_seconds: Time-to-live for event streams (default: 1 hour)
            max_stream_length: Maximum events per stream (prevents unbounded growth)
        """
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self.max_stream_length = max_stream_length
    
    def _stream_key(self, request_id: str) -> str:
        """Get Redis stream key for request"""
        return f"events:{request_id}"
    
    def _sequence_key(self, request_id: str) -> str:
        """Get Redis sequence counter key for request"""
        return f"events:{request_id}:seq"
    
    async def _get_next_sequence(self, request_id: str) -> int:
        """
        Get next sequence number for request (atomic increment).
        
        Uses Redis INCR for atomic counter. First call returns 1.
        
        Args:
            request_id: Request identifier
            
        Returns:
            Next sequence number (1, 2, 3, ...)
        """
        seq_key = self._sequence_key(request_id)
        sequence = await self.redis.incr(seq_key)
        
        # Set TTL on first increment
        if sequence == 1:
            await self.redis.expire(seq_key, self.ttl_seconds)
        
        return sequence
    
    async def _emit_event(self, request_id: str, event: BaseEvent) -> int:
        """
        Core method to emit any event to Redis Stream.
        
        Steps:
        1. Get next sequence number
        2. Assign to event
        3. Store in Redis Stream
        4. Set TTL on stream
        
        Args:
            request_id: Request identifier
            event: Event to emit
            
        Returns:
            Sequence number assigned to event
        """
        try:
            # Get next sequence number
            sequence = await self._get_next_sequence(request_id)
            event.sequence = sequence
            
            # Store in Redis Stream
            stream_key = self._stream_key(request_id)
            redis_data = event.to_redis_dict()
            
            # XADD with MAXLEN to prevent unbounded growth
            await self.redis.xadd(
                stream_key,
                redis_data,
                maxlen=self.max_stream_length,
                approximate=True  # More efficient, allows some overage
            )
            
            # Set TTL on stream (only on first event)
            if sequence == 1:
                await self.redis.expire(stream_key, self.ttl_seconds)
            
            print(f"Emitted {event.event_type} (seq={sequence}) for {request_id}")
            return sequence
            
        except Exception as e:
            print(f"ERROR: " + str(f"Failed to emit event for {request_id}: {e}")); import traceback; traceback.print_exc()
            # Don't raise - events are best-effort, don't break main flow
            return -1
    
    # ========== Thinking Events ==========
    
    async def emit_thinking_start(self, request_id: str) -> int:
        """
        Emit thinking start event (LLM begins processing).
        
        Args:
            request_id: Request identifier
            
        Returns:
            Sequence number
        """
        event = ThinkingStartEvent(request_id=request_id)
        return await self._emit_event(request_id, event)
    
    async def emit_thinking_end(self, request_id: str, duration_ms: int) -> int:
        """
        Emit thinking end event (LLM finished processing).
        
        Args:
            request_id: Request identifier
            duration_ms: How long thinking took (milliseconds)
            
        Returns:
            Sequence number
        """
        event = ThinkingEndEvent(
            request_id=request_id,
            duration_ms=duration_ms
        )
        return await self._emit_event(request_id, event)
    
    # ========== Tool Events ==========
    
    async def emit_tool_start(
        self,
        request_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        estimated_duration_ms: Optional[int] = None
    ) -> int:
        """
        Emit tool start event (tool execution begins).
        
        Args:
            request_id: Request identifier
            tool_name: Name of tool being executed
            arguments: Tool arguments
            estimated_duration_ms: Expected duration if known
            
        Returns:
            Sequence number
        """
        event = ToolStartEvent(
            request_id=request_id,
            tool_name=tool_name,
            arguments=arguments,
            estimated_duration_ms=estimated_duration_ms
        )
        return await self._emit_event(request_id, event)
    
    async def emit_tool_end(
        self,
        request_id: str,
        tool_name: str,
        status: str,
        duration_ms: int,
        summary: str,
        error_message: Optional[str] = None,
        result_id: Optional[str] = None
    ) -> int:
        """
        Emit tool end event (tool execution complete).
        
        Args:
            request_id: Request identifier
            tool_name: Name of tool that executed
            status: "success" | "error" | "cancelled"
            duration_ms: Actual execution time
            summary: Short human-readable summary
            error_message: Error details if status="error"
            result_id: Reference to full result in MongoDB
            
        Returns:
            Sequence number
        """
        # Convert string to enum if needed
        if isinstance(status, str):
            status = ToolStatus(status)
        
        event = ToolEndEvent(
            request_id=request_id,
            tool_name=tool_name,
            status=status,
            duration_ms=duration_ms,
            summary=summary,
            error_message=error_message,
            result_id=result_id
        )
        return await self._emit_event(request_id, event)
    
    # ========== Progress Events ==========
    
    async def emit_run_progress(
        self,
        request_id: str,
        stage: str,
        percentage: int,
        message: Optional[str] = None,
        current_block: Optional[int] = None,
        total_blocks: Optional[int] = None
    ) -> int:
        """
        Emit progress event (during long simulations).
        
        Args:
            request_id: Request identifier
            stage: Current stage (Mill, Heater, Clarifier, etc.)
            percentage: Overall completion (0-100)
            message: Detailed status message
            current_block: Current block number
            total_blocks: Total number of blocks
            
        Returns:
            Sequence number
        """
        event = RunProgressEvent(
            request_id=request_id,
            stage=stage,
            percentage=percentage,
            message=message,
            current_block=current_block,
            total_blocks=total_blocks
        )
        return await self._emit_event(request_id, event)
    
    # ========== Message Events ==========
    
    async def emit_message_delta(
        self,
        request_id: str,
        delta: str,
        accumulated_length: int
    ) -> int:
        """
        Emit message delta (streaming text chunk).
        
        Args:
            request_id: Request identifier
            delta: Text chunk
            accumulated_length: Total characters so far
            
        Returns:
            Sequence number
        """
        event = MessageDeltaEvent(
            request_id=request_id,
            delta=delta,
            accumulated_length=accumulated_length
        )
        return await self._emit_event(request_id, event)
    
    async def emit_message_final(
        self,
        request_id: str,
        content: str,
        role: str = "assistant",
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Emit final message (complete LLM response).
        
        Args:
            request_id: Request identifier
            content: Complete message in markdown format
            role: Message role (assistant|user)
            metadata: Extra info (tool_calls, tokens, etc.)
            
        Returns:
            Sequence number
        """
        event = MessageFinalEvent(
            request_id=request_id,
            content=content,
            role=role,
            metadata=metadata
        )
        return await self._emit_event(request_id, event)
    
    # ========== Error Events ==========
    
    async def emit_app_error(
        self,
        request_id: str,
        error_type: str,
        error_message: str,
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = True
    ) -> int:
        """
        Emit application error event.
        
        Args:
            request_id: Request identifier
            error_type: Error category (policy_violation, tool_error, etc.)
            error_message: User-friendly error message
            details: Technical details for debugging
            recoverable: Can user retry/fix this error?
            
        Returns:
            Sequence number
        """
        # Convert string to enum if needed
        if isinstance(error_type, str):
            error_type = ErrorType(error_type)
        
        event = AppErrorEvent(
            request_id=request_id,
            error_type=error_type,
            error_message=error_message,
            details=details,
            recoverable=recoverable
        )
        return await self._emit_event(request_id, event)
    
    # ========== Event Retrieval ==========
    
    async def get_events(
        self,
        request_id: str,
        after_sequence: int = 0,
        count: Optional[int] = None
    ) -> List[BaseEvent]:
        """
        Retrieve events from Redis Stream (for replay/SSE).
        
        Args:
            request_id: Request identifier
            after_sequence: Only return events after this sequence (for replay)
            count: Maximum events to return (None = all)
            
        Returns:
            List of events in order
        """
        try:
            stream_key = self._stream_key(request_id)
            
            # Read from Redis Stream
            # XRANGE returns all entries from start (-) to end (+)
            entries = await self.redis.xrange(
                stream_key,
                min='-',
                max='+',
                count=count
            )
            
            events = []
            for redis_id, fields in entries:
                try:
                    # Deserialize event
                    event = BaseEvent.from_redis_dict(fields)
                    
                    # Filter by sequence if requested
                    if event.sequence and event.sequence > after_sequence:
                        events.append(event)
                        
                except Exception as e:
                    print(f"ERROR: " + str(f"Failed to parse event from Redis: {e}"))
                    continue
            
            return events
            
        except Exception as e:
            print(f"ERROR: " + str(f"Failed to get events for {request_id}: {e}")); import traceback; traceback.print_exc()
            return []
    
    async def get_event_count(self, request_id: str) -> int:
        """
        Get total number of events for a request.
        
        Args:
            request_id: Request identifier
            
        Returns:
            Event count
        """
        try:
            stream_key = self._stream_key(request_id)
            length = await self.redis.xlen(stream_key)
            return length
        except Exception as e:
            print(f"ERROR: " + str(f"Failed to get event count for {request_id}: {e}"))
            return 0
    
    async def clear_events(self, request_id: str) -> bool:
        """
        Clear all events for a request (delete stream).
        
        Args:
            request_id: Request identifier
            
        Returns:
            True if cleared successfully
        """
        try:
            stream_key = self._stream_key(request_id)
            seq_key = self._sequence_key(request_id)
            
            await self.redis.delete(stream_key, seq_key)
            print(f"Cleared events for {request_id}")
            return True
            
        except Exception as e:
            print(f"ERROR: " + str(f"Failed to clear events for {request_id}: {e}"))
            return False
