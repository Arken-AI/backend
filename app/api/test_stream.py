"""
Test Stream API Endpoint

Provides a dummy SSE stream for testing frontend without calling LLM.
"""

import asyncio
import json
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()


async def generate_test_events():
    """Generate dummy SSE events for testing"""
    
    # Event 1: thinking_start
    yield f"event: thinking_start\n"
    yield f"data: {json.dumps({'event_type': 'thinking_start', 'sequence': 1, 'timestamp': datetime.now().isoformat()})}\n\n"
    await asyncio.sleep(1)
    
    # Event 2: tool_start
    yield f"event: tool_start\n"
    yield f"data: {json.dumps({'event_type': 'tool_start', 'sequence': 2, 'tool_name': 'test_tool', 'arguments': {'param': 'value'}, 'estimated_duration_ms': 2000})}\n\n"
    await asyncio.sleep(2)
    
    # Event 3: tool_end
    yield f"event: tool_end\n"
    yield f"data: {json.dumps({'event_type': 'tool_end', 'sequence': 3, 'tool_name': 'test_tool', 'status': 'success', 'duration_ms': 2000, 'summary': 'Test tool executed successfully'})}\n\n"
    await asyncio.sleep(0.5)
    
    # Event 4: thinking_end
    yield f"event: thinking_end\n"
    yield f"data: {json.dumps({'event_type': 'thinking_end', 'sequence': 4, 'duration_ms': 3500})}\n\n"
    await asyncio.sleep(0.5)
    
    # Event 5-10: message_delta (streaming text)
    text_chunks = [
        "Hello! ",
        "This is ",
        "a test ",
        "streaming ",
        "response. ",
        "Everything is working correctly!"
    ]
    
    accumulated = ""
    for i, chunk in enumerate(text_chunks):
        accumulated += chunk
        yield f"event: message_delta\n"
        yield f"data: {json.dumps({'event_type': 'message_delta', 'sequence': 5 + i, 'delta': chunk, 'accumulated_length': len(accumulated)})}\n\n"
        await asyncio.sleep(0.3)
    
    # Final event: message_final
    yield f"event: message_final\n"
    yield f"data: {json.dumps({'event_type': 'message_final', 'sequence': 11, 'role': 'assistant', 'content': accumulated, 'metadata': {}})}\n\n"


@router.get(
    "/test/stream",
    summary="Test SSE Stream",
    description="Returns a dummy SSE stream for testing frontend without calling LLM"
)
async def test_stream():
    """Test SSE endpoint that sends dummy events"""
    return StreamingResponse(
        generate_test_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
