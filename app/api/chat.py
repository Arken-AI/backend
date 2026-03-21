"""
Chat API Endpoints

REST API endpoints for chat interface:
- POST /chat - Send a message (synchronous - waits for response)
- GET /chat/{conversation_id}/context - Get conversation state
- DELETE /chat/{conversation_id} - Delete conversation

Note: SSE streaming is still available for real-time tool progress updates,
but the final response is returned directly in the HTTP response.
"""

import base64
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Depends, UploadFile, status
from fastapi.responses import JSONResponse

from app.models.requests import (
    ChatRequest,
    ChatResponse,
    ImageAttachment,
    ALLOWED_IMAGE_TYPES,
    MAX_ATTACHMENT_SIZE_BYTES,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationListItem,
    MessageHistoryItem,
    ErrorResponse,
    ToolExecution,
    TokenUsage
)
from app.services.orchestration_service import OrchestrationService
from app.services.context_manager import ContextManager
from app.dependencies import get_orchestration_service, get_redis_client, get_mongo_client, get_event_emitter
from app.core.mongo_client import MongoClient
from app.services.event_emitter import EventEmitter
import redis.asyncio as redis

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send Chat Message",
    description="Send a message to the assistant. Waits for processing and returns the complete response.",
    responses={
        200: {
            "description": "Message processed successfully",
            "model": ChatResponse
        },
        400: {
            "description": "Invalid request",
            "model": ErrorResponse
        },
        500: {
            "description": "Internal server error",
            "model": ErrorResponse
        }
    }
)
async def send_message(
    request: ChatRequest,
    orchestration: OrchestrationService = Depends(get_orchestration_service)
) -> ChatResponse:
    """
    Send a chat message and wait for the complete response.
    
    This endpoint processes the message synchronously and returns the final response.
    Tool progress events are still emitted via SSE for real-time UI updates.
    
    The conversation_id must be provided by the client (frontend generates it).
    This ensures the SSE stream can connect before the HTTP response arrives.
    
    Args:
        request: Chat request with message and required conversation_id
        orchestration: Orchestration service dependency
        
    Returns:
        ChatResponse with complete assistant response
    """
    try:
        conversation_id = request.conversation_id
        
        # Generate unique request_id for this message
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        
        user_id = request.metadata.get("user_id", "default_user") if request.metadata else "default_user"
        
        # Convert attachments to dicts for the orchestration layer
        attachments = None
        if request.attachments:
            attachments = [
                {
                    "media_type": att.media_type,
                    "data": att.data,
                    "filename": att.filename,
                }
                for att in request.attachments
            ]
        
        # Process message synchronously - wait for complete response
        result = await orchestration.process_message(
            conversation_id=conversation_id,
            user_message=request.message,
            user_id=user_id,
            metadata=request.metadata or {},
            attachments=attachments,
        )
        
        logger.info(
            f"Chat processing completed: conversation_id={conversation_id}, "
            f"status={result.get('status', 'unknown')}"
        )
        
        # Build token usage if available
        token_usage = None
        if result.get("token_usage"):
            token_usage = TokenUsage(
                input_tokens=result["token_usage"].get("input_tokens", 0),
                output_tokens=result["token_usage"].get("output_tokens", 0),
                total_tokens=result["token_usage"].get("total_tokens", 0)
            )
        
        # Build tool executions list
        tool_executions = []
        for tool_call in result.get("tool_calls", []):
            # Summary lives inside the nested "result" dict, not at top level.
            # Prefer result.message (str), skip result.summary if it's a dict.
            tool_result = tool_call.get("result") or {}
            raw_summary = tool_call.get("summary")
            if not isinstance(raw_summary, str):
                raw_summary = None
            if not raw_summary:
                raw_summary = tool_result.get("message") if isinstance(tool_result.get("message"), str) else None
            if not raw_summary:
                raw_summary = tool_result.get("summary") if isinstance(tool_result.get("summary"), str) else None
            if not raw_summary:
                raw_summary = str(tool_result)[:200]
            tool_executions.append(ToolExecution(
                tool_name=tool_call.get("name") or tool_call.get("tool_name") or tool_call.get("tool", "unknown"),
                status=tool_call.get("status", "success"),
                duration_ms=tool_call.get("duration_ms"),
                summary=raw_summary,
                arguments=tool_call.get("arguments"),
                result=tool_result if tool_result else None
            ))
        
        # Return complete response
        return ChatResponse(
            conversation_id=conversation_id,
            request_id=request_id,
            status="completed" if result.get("status") == "success" else "error",
            message=result.get("message", ""),
            token_usage=token_usage,
            run_ids=result.get("run_ids", []),
            tool_executions=tool_executions
        )
        
    except Exception as e:
        logger.error(f"Error processing chat message: {e}", exc_info=True)
        
        # Return error response
        error_conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex[:16]}"
        
        return ChatResponse(
            conversation_id=error_conversation_id,
            request_id=f"req_{uuid.uuid4().hex[:16]}",
            status="error",
            message=f"Internal error: {str(e)}"
        )


# =============================================================================
# Multipart File Upload Endpoint
# =============================================================================

@router.post(
    "/chat/upload",
    response_model=ChatResponse,
    summary="Send Chat Message with File Uploads",
    description="Send a message with image attachments via multipart form data.",
    responses={
        200: {"description": "Message processed successfully", "model": ChatResponse},
        400: {"description": "Invalid request", "model": ErrorResponse},
        413: {"description": "File too large", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    }
)
async def send_message_with_files(
    conversation_id: str = Form(..., description="Client-generated conversation ID"),
    message: str = Form(..., min_length=1, max_length=10000, description="User message"),
    metadata_json: Optional[str] = Form(default=None, description="JSON-encoded metadata"),
    files: List[UploadFile] = File(default=[], description="Image files to attach"),
    orchestration: OrchestrationService = Depends(get_orchestration_service),
) -> ChatResponse:
    """
    Send a chat message with image file uploads.
    
    This is an alternative to POST /chat for when the client wants to upload
    real files instead of base64-encoding them in JSON.  The backend reads
    each file, validates the MIME type and total size, base64-encodes the
    bytes, and feeds them into the same orchestration pipeline.
    """
    try:
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        
        # Parse optional metadata
        import json as _json
        metadata: Dict[str, Any] = {}
        if metadata_json:
            try:
                metadata = _json.loads(metadata_json)
            except _json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="metadata_json is not valid JSON")
        
        user_id = metadata.get("user_id", "default_user")
        
        # Validate and convert uploaded files
        attachments: List[Dict[str, Any]] = []
        if len(files) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 image attachments per message")
        
        total_bytes = 0
        for f in files:
            # Validate MIME type
            content_type = f.content_type or "application/octet-stream"
            if content_type not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type '{content_type}' for file '{f.filename}'. "
                           f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}"
                )
            
            raw_bytes = await f.read()
            total_bytes += len(raw_bytes)
            if total_bytes > MAX_ATTACHMENT_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload size exceeds {MAX_ATTACHMENT_SIZE_BYTES // (1024*1024)} MB limit"
                )
            
            attachments.append({
                "media_type": content_type,
                "data": base64.b64encode(raw_bytes).decode("ascii"),
                "filename": f.filename,
            })
        
        # Process through the same orchestration pipeline
        result = await orchestration.process_message(
            conversation_id=conversation_id,
            user_message=message.strip(),
            user_id=user_id,
            metadata=metadata,
            attachments=attachments if attachments else None,
        )
        
        logger.info(
            f"Upload chat completed: conversation_id={conversation_id}, "
            f"files={len(attachments)}, status={result.get('status', 'unknown')}"
        )
        
        # Build response (same as send_message)
        token_usage = None
        if result.get("token_usage"):
            token_usage = TokenUsage(
                input_tokens=result["token_usage"].get("input_tokens", 0),
                output_tokens=result["token_usage"].get("output_tokens", 0),
                total_tokens=result["token_usage"].get("total_tokens", 0)
            )
        
        tool_executions = []
        for tool_call in result.get("tool_calls", []):
            tool_result = tool_call.get("result") or {}
            raw_summary = tool_call.get("summary")
            if not isinstance(raw_summary, str):
                raw_summary = None
            if not raw_summary:
                raw_summary = tool_result.get("message") if isinstance(tool_result.get("message"), str) else None
            if not raw_summary:
                raw_summary = tool_result.get("summary") if isinstance(tool_result.get("summary"), str) else None
            if not raw_summary:
                raw_summary = str(tool_result)[:200]
            tool_executions.append(ToolExecution(
                tool_name=tool_call.get("name") or tool_call.get("tool_name") or "unknown",
                status=tool_call.get("status", "success"),
                duration_ms=tool_call.get("duration_ms"),
                summary=raw_summary,
                arguments=tool_call.get("arguments"),
                result=tool_result if tool_result else None
            ))
        
        return ChatResponse(
            conversation_id=conversation_id,
            request_id=request_id,
            status="completed" if result.get("status") == "success" else "error",
            message=result.get("message", ""),
            token_usage=token_usage,
            run_ids=result.get("run_ids", []),
            tool_executions=tool_executions
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing upload chat message: {e}", exc_info=True)
        return ChatResponse(
            conversation_id=conversation_id,
            request_id=f"req_{uuid.uuid4().hex[:16]}",
            status="error",
            message=f"Internal error: {str(e)}"
        )


@router.post(
    "/chat/{conversation_id}/retry",
    response_model=ChatResponse,
    summary="Retry Last Message",
    description=(
        "Delete the last assistant response (and optionally the preceding user "
        "message) from the conversation history, then re-process the user "
        "message through the LLM so Claude regenerates from scratch."
    ),
    responses={
        200: {"description": "Retry processed successfully", "model": ChatResponse},
        404: {"description": "Conversation or user message not found", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def retry_last_message(
    conversation_id: str,
    orchestration: OrchestrationService = Depends(get_orchestration_service),
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client),
):
    """
    Retry the last user message in a conversation.

    Steps:
      1. Load conversation history and find the last user message.
      2. Determine how many tail messages to remove (assistant + user, or just
         the trailing user message if no assistant response was saved yet).
      3. Delete those messages from Redis + MongoDB so the LLM never sees the
         stale/incomplete response.
      4. Re-run process_message which saves the user message, streams a fresh
         Claude response, and saves the new assistant message.

    The frontend should open the SSE stream BEFORE calling this endpoint
    (same as the normal send_message flow).
    """
    try:
        # Build a ContextManager for this request
        context_manager = ContextManager(
            redis_client=redis_client,
            mongo_client=mongo_client._client,
        )

        # ── 1. Load history and locate the last user message ─────────
        messages = await context_manager.get_messages(conversation_id)
        if not messages:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No messages in conversation {conversation_id}",
            )

        # Walk backwards to find the last user message
        last_user_msg = None
        last_user_index = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_msg = messages[i]
                last_user_index = i
                break

        if not last_user_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No user message found to retry",
            )

        # ── 2. Determine how many messages to trim ───────────────────
        # Possible tail shapes:
        #   [..., user]                    → remove 1  (error before assistant saved)
        #   [..., user, assistant]         → remove 2  (normal retry)
        #   [..., user, assistant, ...]    → unusual, but count from last_user_index
        messages_after_user = len(messages) - last_user_index  # includes the user msg itself
        deleted = await context_manager.delete_messages_from_tail(
            conversation_id, count=messages_after_user
        )
        logger.info(
            f"Retry: deleted {deleted} tail messages from {conversation_id}"
        )

        # ── 3. Re-process the same user message ─────────────────────
        user_text = last_user_msg.get("content", "")
        user_id = "default_user"

        request_id = f"req_{uuid.uuid4().hex[:16]}"

        # NOTE: Do NOT pass request_id in metadata. The orchestration service
        # falls back to conversation_id as the event key, which is what the
        # SSE stream endpoint listens on. Passing a separate request_id would
        # key events under "req_..." while SSE polls "conv_..." — no live streaming.
        result = await orchestration.process_message(
            conversation_id=conversation_id,
            user_message=user_text,
            user_id=user_id,
            metadata={"is_retry": True},
        )

        logger.info(
            f"Retry completed: conversation_id={conversation_id}, "
            f"status={result.get('status', 'unknown')}"
        )

        # ── 4. Build response (same shape as send_message) ──────────
        token_usage = None
        if result.get("token_usage"):
            token_usage = TokenUsage(
                input_tokens=result["token_usage"].get("input_tokens", 0),
                output_tokens=result["token_usage"].get("output_tokens", 0),
                total_tokens=result["token_usage"].get("total_tokens", 0),
            )

        return ChatResponse(
            conversation_id=conversation_id,
            request_id=request_id,
            status="completed" if result.get("status") == "success" else "error",
            message=result.get("message", ""),
            token_usage=token_usage,
            run_ids=result.get("run_ids", []),
            tool_executions=[],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying message: {e}", exc_info=True)
        return ChatResponse(
            conversation_id=conversation_id,
            request_id=f"req_{uuid.uuid4().hex[:16]}",
            status="error",
            message=f"Retry failed: {str(e)}",
        )


@router.post(
    "/chat/{conversation_id}/cancel",
    summary="Cancel In-Flight Request",
    description=(
        "Set a cancellation flag so the backend stops streaming the response "
        "for this conversation. Optionally accepts partial_message from the "
        "frontend to persist as a cancelled assistant message."
    ),
)
async def cancel_request(
    conversation_id: str,
    body: Optional[Dict[str, Any]] = None,
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client),
):
    """
    Signal the backend to stop processing the current request for this conversation.

    Sets a Redis key that the streaming orchestration loop checks on each chunk,
    raising CancelledError when found.

    If the frontend sends a `partial_message` in the body, we persist it as a
    cancelled assistant message so it survives page reloads.
    """
    await redis_client.setex(f"cancel:{conversation_id}", 60, "1")
    logger.info("Cancel flag set for conversation: %s", conversation_id)

    # If the frontend sent partial streamed text, save it as a cancelled message.
    # The orchestration CancelledError handler will also try to save, but there
    # is a race — this is a safety net so the user's partial text is never lost.
    partial_message = (body or {}).get("partial_message", "") if body else ""
    if partial_message and partial_message.strip():
        try:
            context_manager = ContextManager(
                redis_client=redis_client,
                mongo_client=mongo_client._client,
            )
            # Check if the orchestration already saved a cancelled message
            messages = await context_manager.get_messages(conversation_id)
            last_msg = messages[-1] if messages else None
            already_saved = (
                last_msg
                and last_msg.get("role") == "assistant"
                and last_msg.get("status") == "cancelled"
            )
            if not already_saved:
                await context_manager.add_message(
                    conversation_id,
                    role="assistant",
                    content=partial_message.strip(),
                    status="cancelled",
                    metadata={"cancelled": True, "saved_by": "cancel_endpoint"},
                )
                logger.info(
                    "Saved partial message via cancel endpoint: %s (len=%d)",
                    conversation_id,
                    len(partial_message),
                )
        except Exception as exc:
            logger.warning(
                "Failed to save partial message on cancel: %s — %s",
                conversation_id,
                exc,
            )

    return {"status": "cancelled", "conversation_id": conversation_id}


@router.get(
    "/chat/{conversation_id}/context",
    response_model=ConversationContextResponse,
    summary="Get Conversation Context",
    description="Retrieve full conversation state including message history and tool executions.",
    responses={
        200: {
            "description": "Conversation context retrieved",
            "model": ConversationContextResponse
        },
        404: {
            "description": "Conversation not found",
            "model": ErrorResponse
        }
    }
)
async def get_conversation_context(
    conversation_id: str,
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client),
    event_emitter: EventEmitter = Depends(get_event_emitter)
) -> ConversationContextResponse:
    """
    Get conversation context and state.
    
    Returns full message history, executed tools, and metadata.
    
    Args:
        conversation_id: Conversation identifier
        redis_client: Redis client dependency
        mongo_client: MongoDB client dependency
        
    Returns:
        ConversationContextResponse with full conversation state
    """
    try:
        # Create context manager to load conversation
        context_manager = ContextManager(
            redis_client=redis_client, 
            mongo_client=mongo_client._client  # Pass underlying Motor client
        )
        
        # Load conversation context
        context = await context_manager.get_context(conversation_id)
        
        if not context:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {conversation_id} not found"
            )
        
        # Parse message history
        messages = []
        for msg in context.get("messages", []):
            messages.append(MessageHistoryItem(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                timestamp=datetime.fromisoformat(msg.get("timestamp")) if msg.get("timestamp") else datetime.now(),
                status=msg.get("status", "complete"),  # Include message status
                message_id=msg.get("message_id"),  # Include message_id
                metadata=msg.get("metadata")
            ))
        
        # Get last event sequence for SSE reconnection
        last_sequence = await event_emitter.get_current_sequence(conversation_id)
        
        # Determine conversation status
        # Check if there are pending events (thinking_start without message_final)
        conversation_status = "idle"
        if last_sequence > 0:
            # Get the last few events to check if processing is complete
            recent_events = await event_emitter.get_events(conversation_id, after_sequence=max(0, last_sequence - 5))
            
            has_thinking_start = False
            has_message_final = False
            
            for event in recent_events:
                # Handle event_type as either string or enum
                event_type = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
                
                if event_type == "thinking_start":
                    has_thinking_start = True
                elif event_type == "message_final":
                    # Only count non-intermediate message_final as completion
                    if not (hasattr(event, 'metadata') and event.metadata and event.metadata.get('is_intermediate')):
                        has_message_final = True
            
            if has_thinking_start and not has_message_final:
                conversation_status = "processing"
            elif has_message_final:
                conversation_status = "completed"
        
        # Build response
        response = ConversationContextResponse(
            conversation_id=conversation_id,
            status=conversation_status,
            messages=messages,
            run_ids=context.get("run_ids", []),  # Array of run IDs, newest first
            executed_tools=context.get("executed_tools", []),
            current_industry=context.get("current_industry"),
            current_process=context.get("current_process"),
            created_at=datetime.fromisoformat(context.get("created_at")) if context.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(context.get("updated_at")) if context.get("updated_at") else datetime.now(),
            last_event_sequence=last_sequence
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving conversation context: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve conversation: {str(e)}"
        )


@router.delete(
    "/chat/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Conversation",
    description="Delete conversation from Redis and MongoDB. Cannot be undone.",
    responses={
        204: {
            "description": "Conversation deleted successfully"
        },
        500: {
            "description": "Internal server error",
            "model": ErrorResponse
        }
    }
)
async def delete_conversation(
    conversation_id: str,
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client)
):
    """
    Delete a conversation.
    
    Removes conversation from both Redis cache and MongoDB permanent storage.
    This operation cannot be undone.
    
    Args:
        conversation_id: Conversation identifier
        redis_client: Redis client dependency
        mongo_client: MongoDB client dependency
        
    Returns:
        204 No Content on success
    """
    try:
        # Create context manager to delete conversation
        context_manager = ContextManager(
            redis_client=redis_client, 
            mongo_client=mongo_client._client  # Pass underlying Motor client
        )
        
        # Clear context (deletes from both Redis and MongoDB)
        await context_manager.clear_context(conversation_id)
        
        logger.info(f"Conversation deleted: {conversation_id}")
        
        # Return 204 No Content
        return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
        
    except Exception as e:
        logger.error(f"Error deleting conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete conversation: {str(e)}"
        )


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List Conversations",
    description="Get a list of all conversations for the current user.",
    responses={
        200: {
            "description": "List of conversations",
            "model": ConversationListResponse
        },
        500: {
            "description": "Internal server error",
            "model": ErrorResponse
        }
    }
)
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    username: Optional[str] = None,
    mongo_client: MongoClient = Depends(get_mongo_client)
) -> ConversationListResponse:
    """
    List all conversations.
    
    Returns a paginated list of conversations with summary information.
    Conversations are sorted by updated_at (newest first).
    Optionally filtered by username (matches user_id field).
    
    Args:
        limit: Maximum number of conversations to return (default: 50)
        offset: Number of conversations to skip (default: 0)
        username: Optional username to filter conversations by
        mongo_client: MongoDB client dependency
        
    Returns:
        ConversationListResponse with list of conversations
    """
    try:
        db = mongo_client._client["arken_process_db"]
        collection = db["conversations"]
        
        # Build query filter
        query = {}
        if username:
            query["user_id"] = username.lower()
        
        # Get total count
        total = await collection.count_documents(query)
        
        # Get conversations sorted by updated_at
        cursor = collection.find(query).sort("updated_at", -1).skip(offset).limit(limit)
        
        conversations = []
        async for doc in cursor:
            # Extract first user message as title
            messages = doc.get("messages", [])
            title = None
            for msg in messages:
                if msg.get("role") == "user":
                    title = msg.get("content", "")[:100]  # First 100 chars
                    break
            
            # Check if has simulations
            run_ids = doc.get("run_ids", [])
            
            conversations.append(ConversationListItem(
                conversation_id=doc.get("conversation_id", str(doc.get("_id"))),
                title=title,
                message_count=len(messages),
                has_simulations=len(run_ids) > 0,
                created_at=datetime.fromisoformat(doc.get("created_at")) if doc.get("created_at") else datetime.now(),
                updated_at=datetime.fromisoformat(doc.get("updated_at")) if doc.get("updated_at") else datetime.now()
            ))
        
        return ConversationListResponse(
            conversations=conversations,
            total=total
        )
        
    except Exception as e:
        logger.error(f"Error listing conversations: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list conversations: {str(e)}"
        )

