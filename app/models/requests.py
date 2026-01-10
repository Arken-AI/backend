"""
Request and Response Models for Chat API

This module defines Pydantic models for all REST API endpoints.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TokenUsage(BaseModel):
    """Token usage information for LLM calls"""
    
    input_tokens: int = Field(
        default=0,
        description="Total input tokens used in this conversation"
    )
    output_tokens: int = Field(
        default=0,
        description="Total output tokens generated in this conversation"
    )
    total_tokens: int = Field(
        default=0,
        description="Total tokens (input + output)"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "input_tokens": 1250,
                    "output_tokens": 420,
                    "total_tokens": 1670
                }
            ]
        }
    }


class ChatRequest(BaseModel):
    """Request model for sending a chat message"""
    
    conversation_id: Optional[str] = Field(
        default=None,
        description="Existing conversation ID for multi-turn chat. Leave empty for new conversation."
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="User message to send to the assistant"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata for the message (e.g., user info, session data)"
    )
    
    @field_validator('message')
    @classmethod
    def validate_message(cls, v: str) -> str:
        """Ensure message is not just whitespace"""
        if not v.strip():
            raise ValueError("Message cannot be empty or whitespace only")
        return v.strip()
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message": "What tools are available?"
                },
                {
                    "conversation_id": "conv_123abc",
                    "message": "Simulate sugar mill with 1000kg cane",
                    "metadata": {"user_id": "user_456"}
                }
            ]
        }
    }


class ToolExecution(BaseModel):
    """Tool execution record for chat response"""
    
    tool_name: str = Field(..., description="Name of the tool executed")
    status: Literal["success", "error"] = Field(..., description="Execution status")
    duration_ms: Optional[int] = Field(default=None, description="Execution time in milliseconds")
    summary: Optional[str] = Field(default=None, description="Brief result summary")


class ChatResponse(BaseModel):
    """Response model after sending a chat message"""
    
    conversation_id: str = Field(
        ...,
        description="Conversation ID for tracking multi-turn chat"
    )
    request_id: str = Field(
        ...,
        description="Unique request ID for tracking this specific message processing"
    )
    status: Literal["processing", "completed", "error"] = Field(
        ...,
        description="Current status of the request"
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional status message (e.g., error details)"
    )
    token_usage: Optional[TokenUsage] = Field(
        default=None,
        description="Token usage statistics for this conversation"
    )
    run_ids: List[str] = Field(
        default_factory=list,
        description="Array of simulation run IDs (newest first, max 10)"
    )
    tool_executions: List[ToolExecution] = Field(
        default_factory=list,
        description="List of tools executed in this request"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "conversation_id": "conv_123abc",
                    "request_id": "req_789xyz",
                    "status": "processing"
                },
                {
                    "conversation_id": "conv_123abc",
                    "request_id": "req_789xyz",
                    "status": "completed",
                    "message": "Here are the available tools for sugar processing...",
                    "token_usage": {
                        "input_tokens": 1250,
                        "output_tokens": 420,
                        "total_tokens": 1670
                    }
                }
            ]
        }
    }


class MessageHistoryItem(BaseModel):
    """Single message in conversation history"""
    
    role: Literal["user", "assistant"] = Field(
        ...,
        description="Role of the message sender"
    )
    content: str = Field(
        ...,
        description="Message content"
    )
    timestamp: datetime = Field(
        ...,
        description="When the message was created"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata (tool calls, run_id, etc.)"
    )


class ExecutedToolRecord(BaseModel):
    """Record of an executed tool in conversation context"""
    
    tool_name: str = Field(..., description="Name of the tool executed")
    status: Optional[str] = Field(default=None, description="Execution status")
    duration_ms: Optional[int] = Field(default=None, description="Execution time in milliseconds")
    summary: Optional[str] = Field(default=None, description="Brief result summary")
    run_id: Optional[str] = Field(default=None, description="Associated run ID if applicable")


class ConversationContextResponse(BaseModel):
    """Response model for conversation context/state"""
    
    conversation_id: str = Field(
        ...,
        description="The conversation identifier"
    )
    messages: List[MessageHistoryItem] = Field(
        default_factory=list,
        description="Full message history"
    )
    run_ids: List[str] = Field(
        default_factory=list,
        description="Array of simulation run IDs (newest first, max 10)"
    )
    executed_tools: List[ExecutedToolRecord] = Field(
        default_factory=list,
        description="List of tools that have been executed in this conversation"
    )
    current_industry: Optional[str] = Field(
        default=None,
        description="Currently selected industry (if any)"
    )
    current_process: Optional[str] = Field(
        default=None,
        description="Currently selected process (if any)"
    )
    created_at: datetime = Field(
        ...,
        description="When the conversation was created"
    )
    updated_at: datetime = Field(
        ...,
        description="When the conversation was last updated"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "conversation_id": "conv_123abc",
                    "messages": [
                        {
                            "role": "user",
                            "content": "List available industries",
                            "timestamp": "2026-01-07T10:00:00Z"
                        },
                        {
                            "role": "assistant",
                            "content": "Available industries: sugar, ethanol",
                            "timestamp": "2026-01-07T10:00:02Z",
                            "metadata": {"tool_calls": ["list_industries"]}
                        }
                    ],
                    "run_ids": ["run_abc123"],
                    "executed_tools": ["list_industries"],
                    "current_industry": None,
                    "current_process": None,
                    "created_at": "2026-01-07T10:00:00Z",
                    "updated_at": "2026-01-07T10:00:02Z"
                }
            ]
        }
    }


class ConversationListItem(BaseModel):
    """Summary of a conversation for list view"""
    
    conversation_id: str = Field(..., description="The conversation identifier")
    title: Optional[str] = Field(default=None, description="Conversation title (first user message)")
    message_count: int = Field(default=0, description="Number of messages in conversation")
    has_simulations: bool = Field(default=False, description="Whether any simulations were run")
    created_at: datetime = Field(..., description="When the conversation was created")
    updated_at: datetime = Field(..., description="When the conversation was last updated")


class ConversationListResponse(BaseModel):
    """Response model for listing conversations"""
    
    conversations: List[ConversationListItem] = Field(
        default_factory=list,
        description="List of conversations"
    )
    total: int = Field(default=0, description="Total number of conversations")


class ServiceStatus(BaseModel):
    """Status of an individual service"""
    
    name: str = Field(..., description="Service name")
    status: Literal["healthy", "unhealthy", "unknown"] = Field(
        ...,
        description="Health status of the service"
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional status message or error details"
    )
    response_time_ms: Optional[float] = Field(
        default=None,
        description="Response time in milliseconds (if applicable)"
    )


class HealthResponse(BaseModel):
    """Response model for system health check"""
    
    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        ...,
        description="Overall system health status"
    )
    services: Dict[str, ServiceStatus] = Field(
        ...,
        description="Individual service health statuses"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When the health check was performed"
    )
    version: str = Field(
        default="1.0.0",
        description="API version"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "healthy",
                    "services": {
                        "redis": {
                            "name": "Redis",
                            "status": "healthy",
                            "response_time_ms": 2.5
                        },
                        "mongodb": {
                            "name": "MongoDB",
                            "status": "healthy",
                            "response_time_ms": 5.1
                        },
                        "mcp": {
                            "name": "MCP Server",
                            "status": "healthy",
                            "response_time_ms": 10.3
                        },
                        "llm": {
                            "name": "LLM Provider",
                            "status": "healthy"
                        }
                    },
                    "timestamp": "2026-01-07T10:00:00Z",
                    "version": "1.0.0"
                }
            ]
        }
    }


class ErrorResponse(BaseModel):
    """Standard error response model"""
    
    error: str = Field(
        ...,
        description="Error type or code"
    )
    message: str = Field(
        ...,
        description="Human-readable error message"
    )
    details: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional error details"
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="Conversation ID if applicable"
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Request ID if applicable"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When the error occurred"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": "validation_error",
                    "message": "Message cannot be empty",
                    "timestamp": "2026-01-07T10:00:00Z"
                },
                {
                    "error": "policy_violation",
                    "message": "Cannot simulate without validation",
                    "details": {
                        "required_tool": "validate_process_inputs",
                        "attempted_tool": "simulate_process"
                    },
                    "conversation_id": "conv_123abc",
                    "request_id": "req_789xyz",
                    "timestamp": "2026-01-07T10:00:00Z"
                }
            ]
        }
    }
