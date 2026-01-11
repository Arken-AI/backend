"""
Chat API Endpoints

REST API endpoints for chat interface:
- POST /chat - Send a message
- GET /chat/{conversation_id}/context - Get conversation state
- DELETE /chat/{conversation_id} - Delete conversation
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, status, BackgroundTasks
from fastapi.responses import JSONResponse

from app.models.requests import (
    ChatRequest,
    ChatResponse,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationListItem,
    MessageHistoryItem,
    ErrorResponse,
    ToolExecution
)
from app.services.orchestration_service import OrchestrationService
from app.services.context_manager import ContextManager
from app.dependencies import get_orchestration_service, get_redis_client, get_mongo_client, get_event_emitter
from app.core.mongo_client import MongoClient
from app.services.event_emitter import EventEmitter
import redis.asyncio as redis

logger = logging.getLogger(__name__)

router = APIRouter()


async def process_message_background(
    orchestration: OrchestrationService,
    event_emitter: EventEmitter,
    conversation_id: str,
    message: str,
    user_id: str
):
    """
    Background task to process message asynchronously.
    
    This runs after the POST /chat endpoint returns, allowing
    the frontend to connect to SSE immediately.
    
    Any errors are emitted as app_error events.
    """
    try:
        logger.info(f"Background task started for {conversation_id}")
        await orchestration.process_message(
            conversation_id=conversation_id,
            user_message=message,
            user_id=user_id
        )
        logger.info(f"Background task completed for {conversation_id}")
    except Exception as e:
        logger.error(f"Background task error for {conversation_id}: {e}", exc_info=True)
        # Emit error event so frontend knows something went wrong
        if event_emitter:
            await event_emitter.emit_app_error(
                conversation_id,
                error_type="processing_error",
                error_message=str(e),
                recoverable=False
            )


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send Chat Message",
    description="Send a message to the assistant. Returns conversation_id and request_id for tracking.",
    responses={
        200: {
            "description": "Message accepted and processing",
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
    orchestration: OrchestrationService = Depends(get_orchestration_service),
    event_emitter: EventEmitter = Depends(get_event_emitter)
) -> ChatResponse:
    """
    Send a chat message and start processing.
    
    Returns immediately with conversation_id and request_id.
    Processing happens in background - listen to SSE stream for events.
    
    For new conversations, leave conversation_id empty.
    For multi-turn conversations, provide the same conversation_id.
    
    Args:
        request: Chat request with message and optional conversation_id
        orchestration: Orchestration service dependency
        event_emitter: Event emitter for error handling
        
    Returns:
        ChatResponse with conversation_id and request_id (status="processing")
    """
    try:
        # Generate conversation_id if new conversation
        conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex[:16]}"
        
        # Generate unique request_id for this message
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        
        user_id = request.metadata.get("user_id", "default_user") if request.metadata else "default_user"
        
        logger.info(
            f"Starting chat processing: conversation_id={conversation_id}, "
            f"request_id={request_id}, message_length={len(request.message)}"
        )
        
        # Spawn background task - DON'T AWAIT
        # This allows the endpoint to return immediately
        asyncio.create_task(
            process_message_background(
                orchestration=orchestration,
                event_emitter=event_emitter,
                conversation_id=conversation_id,
                message=request.message,
                user_id=user_id
            )
        )
        
        logger.info(
            f"Background task spawned: conversation_id={conversation_id}, "
            f"request_id={request_id}"
        )
        
        # Return immediately - frontend should connect to SSE stream
        return ChatResponse(
            conversation_id=conversation_id,
            request_id=request_id,
            status="processing",
            message=None,  # Response will come via SSE events
            token_usage=None,
            run_ids=[],
            tool_executions=[]
        )
        
    except Exception as e:
        logger.error(f"Error starting chat processing: {e}", exc_info=True)
        
        # Return error response
        error_conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex[:16]}"
        
        return ChatResponse(
            conversation_id=error_conversation_id,
            request_id=f"req_{uuid.uuid4().hex[:16]}",
            status="error",
            message=f"Internal error: {str(e)}"
        )


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
                metadata=msg.get("metadata")
            ))
        
        # Get last event sequence for SSE reconnection
        last_sequence = await event_emitter.get_current_sequence(conversation_id)
        
        # Build response
        response = ConversationContextResponse(
            conversation_id=conversation_id,
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
    mongo_client: MongoClient = Depends(get_mongo_client)
) -> ConversationListResponse:
    """
    List all conversations.
    
    Returns a paginated list of conversations with summary information.
    Conversations are sorted by updated_at (newest first).
    
    Args:
        limit: Maximum number of conversations to return (default: 50)
        offset: Number of conversations to skip (default: 0)
        mongo_client: MongoDB client dependency
        
    Returns:
        ConversationListResponse with list of conversations
    """
    try:
        db = mongo_client._client["arken_process_db"]
        collection = db["conversations"]
        
        # Get total count
        total = await collection.count_documents({})
        
        # Get conversations sorted by updated_at
        cursor = collection.find({}).sort("updated_at", -1).skip(offset).limit(limit)
        
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

