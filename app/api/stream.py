"""
SSE Streaming Endpoint

Server-Sent Events (SSE) endpoint for real-time event streaming to frontend.

Key features:
- Streams events from Redis as they're emitted
- Supports replay (reconnection with last sequence)
- Keepalive heartbeat every 15 seconds
- Auto-terminates on completion (message_final event)

Usage:
    # Frontend JavaScript - use the same conversation_id from your chat request
    const eventSource = new EventSource('/api/chat/conv_test_123/stream?after_sequence=0');
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log(data.event_type, data);
    };
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.services.event_emitter import EventEmitter
from app.dependencies import get_event_emitter
from app.models.events import EventType

logger = logging.getLogger(__name__)

router = APIRouter()


class EventSourceResponse(StreamingResponse):
    """
    SSE (Server-Sent Events) response with proper headers.
    
    Sets headers required for SSE protocol:
    - Content-Type: text/event-stream
    - Cache-Control: no-cache
    - Connection: keep-alive
    - X-Accel-Buffering: no (disable nginx buffering)
    """
    
    def __init__(self, content: AsyncGenerator):
        super().__init__(
            content=content,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            }
        )


async def event_stream_generator(
    request_id: str,
    event_emitter: EventEmitter,
    after_sequence: int = 0,
    poll_interval: float = 0.1,  # Reduced from 0.5s for real-time streaming
    keepalive_interval: float = 15.0
) -> AsyncGenerator[str, None]:
    """
    Generate SSE stream of events for a request.
    
    Flow:
    1. Send all existing events (replay)
    2. Poll Redis for new events every 500ms
    3. Send keepalive comment every 15s
    4. Terminate on message_final event
    
    Args:
        request_id: Request identifier
        event_emitter: EventEmitter service instance
        after_sequence: Only send events after this sequence (for replay)
        poll_interval: How often to check for new events (seconds)
        keepalive_interval: How often to send keepalive (seconds)
        
    Yields:
        SSE formatted strings: "data: {...}\n\n" or ":keepalive\n\n"
    """
    try:
        last_sequence = after_sequence
        last_keepalive = asyncio.get_event_loop().time()
        stream_complete = False
        
        logger.info(f"Starting SSE stream for {request_id} (after_sequence={after_sequence})")
        
        # Send initial keepalive to keep connection open
        yield ":keepalive\n\n"
        
        while not stream_complete:
            current_time = asyncio.get_event_loop().time()
            
            # Fetch new events from Redis
            try:
                events = await event_emitter.get_events(
                    request_id,
                    after_sequence=last_sequence
                )
                
                # Stream each event in SSE format
                for event in events:
                    sse_data = event.to_sse_format()
                    yield sse_data
                    
                    # Update last sequence
                    if event.sequence:
                        last_sequence = event.sequence
                    
                    # Check if stream is complete
                    if event.event_type == EventType.MESSAGE_FINAL:
                        logger.info(f"Stream complete for {request_id} at sequence {last_sequence}")
                        stream_complete = True
                        break
                    
                    # Reset keepalive timer after sending event
                    last_keepalive = current_time
                
            except Exception as e:
                logger.error(f"Error fetching events for {request_id}: {e}", exc_info=True)
                # Continue polling even on error
            
            # Send keepalive if needed
            if current_time - last_keepalive >= keepalive_interval:
                yield ":keepalive\n\n"
                last_keepalive = current_time
            
            # Wait before next poll
            if not stream_complete:
                await asyncio.sleep(poll_interval)
        
        logger.info(f"SSE stream ended for {request_id}")
        
    except asyncio.CancelledError:
        logger.info(f"SSE stream cancelled for {request_id}")
        raise
    except Exception as e:
        logger.error(f"SSE stream error for {request_id}: {e}", exc_info=True)
        # Send error event
        yield f'data: {{"event_type": "error", "message": "Stream error"}}\n\n'


@router.get("/chat/{conversation_id}/stream", response_class=EventSourceResponse)
async def stream_chat_events(
    conversation_id: str,
    after_sequence: int = Query(
        default=0,
        description="Only stream events after this sequence (for replay on reconnection)",
        ge=0
    ),
    event_emitter: EventEmitter = Depends(get_event_emitter)
) -> StreamingResponse:
    """
    Stream real-time events for a chat conversation using SSE.
    
    **IMPORTANT:** Use the same `conversation_id` from your POST /api/chat request.
    Events are stored by conversation_id, so you must use the same value here.
    
    **SSE Protocol:**
    - Opens long-lived HTTP connection
    - Streams events as they occur
    - Client automatically reconnects on disconnect
    
    **Reconnection Support:**
    - Client tracks last sequence number received
    - On reconnect, pass `after_sequence` to resume from that point
    - No events are lost
    
    **Event Format:**
    ```
    data: {"event_type": "thinking_start", "sequence": 1, ...}
    
    data: {"event_type": "tool_start", "sequence": 2, "tool_name": "validate_process_inputs"}
    
    data: {"event_type": "run_progress", "sequence": 5, "percentage": 33}
    
    data: {"event_type": "message_final", "sequence": 12, "content": "Calculation complete!"}
    ```
    
    **Stream Termination:**
    - Stream ends when `message_final` event is sent
    - Client receives last event and connection closes gracefully
    
    Args:
        conversation_id: The conversation identifier (same as used in POST /api/chat)
        after_sequence: Resume from this sequence number (default: 0 = all events)
        event_emitter: Injected EventEmitter service
        
    Returns:
        EventSourceResponse: SSE stream with text/event-stream content type
        
    Example:
        ```javascript
        // Frontend usage - use the same conversation_id from your chat request
        const eventSource = new EventSource('/api/chat/conv_test_123/stream?after_sequence=0');
        
        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            switch(data.event_type) {
                case 'thinking_start':
                    showSpinner();
                    break;
                case 'tool_start':
                    showToolExecution(data.tool_name);
                    break;
                case 'run_progress':
                    updateProgressBar(data.percentage);
                    break;
                case 'message_final':
                    displayResult(data.content);
                    eventSource.close();
                    break;
            }
        };
        
        eventSource.onerror = () => {
            // Auto-reconnect with last sequence
            const lastSeq = getLastReceivedSequence();
            reconnect(lastSeq);
        };
        ```
    """
    generator = event_stream_generator(
        request_id=conversation_id,
        event_emitter=event_emitter,
        after_sequence=after_sequence
    )
    
    return EventSourceResponse(generator)
