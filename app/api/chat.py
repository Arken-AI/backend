"""
Chat API Endpoints

REST API endpoints for chat interface:
- POST /chat - Send a message
- GET /chat/{conversation_id}/context - Get conversation state
- DELETE /chat/{conversation_id} - Delete conversation
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse

from app.models.requests import (
    ChatRequest,
    ChatResponse,
    ConversationContextResponse,
    MessageHistoryItem,
    ErrorResponse
)
from app.services.orchestration_service import OrchestrationService
from app.services.context_manager import ContextManager
from app.dependencies import get_orchestration_service, get_redis_client, get_mongo_client
from app.core.mongo_client import MongoClient
import redis.asyncio as redis

logger = logging.getLogger(__name__)

router = APIRouter()


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
    orchestration: OrchestrationService = Depends(get_orchestration_service)
) -> ChatResponse:
    """
    Send a chat message and get response.
    
    For new conversations, leave conversation_id empty.
    For multi-turn conversations, provide the same conversation_id.
    
    Args:
        request: Chat request with message and optional conversation_id
        orchestration: Orchestration service dependency
        
    Returns:
        ChatResponse with conversation_id and request_id
    """
    try:
        # Generate conversation_id if new conversation
        conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex[:16]}"
        
        # Generate unique request_id for this message
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        
        logger.info(
            f"Processing chat message: conversation_id={conversation_id}, "
            f"request_id={request_id}, message_length={len(request.message)}"
        )
        
        # Process message through orchestration service
        # This will:
        # 1. Load/create context
        # 2. Call LLM with tools
        # 3. Execute any tool calls
        # 4. Emit events via SSE
        # 5. Return final response
        result = await orchestration.process_message(
            conversation_id=conversation_id,
            user_message=request.message,
            user_id=request.metadata.get("user_id", "default_user") if request.metadata else "default_user"
        )
        
        # Determine status based on result
        if result.get("error"):
            response_status = "error"
            response_message = result.get("error")
        else:
            response_status = "completed"
            # Get the LLM's response message from the result
            response_message = result.get("message", "")
        
        # Extract token usage if available
        token_usage = None
        if result.get("token_usage"):
            from app.models.requests import TokenUsage
            token_usage = TokenUsage(**result["token_usage"])
        
        logger.info(
            f"Message processed: conversation_id={conversation_id}, "
            f"request_id={request_id}, status={response_status}"
        )
        
        return ChatResponse(
            conversation_id=conversation_id,
            request_id=request_id,
            status=response_status,
            message=response_message,
            token_usage=token_usage
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
    mongo_client: MongoClient = Depends(get_mongo_client)
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
        
        # Build response
        response = ConversationContextResponse(
            conversation_id=conversation_id,
            messages=messages,
            run_ids=context.get("run_ids", []),  # Array of run IDs, newest first
            executed_tools=context.get("executed_tools", []),
            current_industry=context.get("current_industry"),
            current_process=context.get("current_process"),
            created_at=datetime.fromisoformat(context.get("created_at")) if context.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(context.get("updated_at")) if context.get("updated_at") else datetime.now()
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

