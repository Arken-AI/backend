"""
Dependency Injection

Provides dependency injection for database connections, clients, and services.
"""

from typing import AsyncGenerator
import redis.asyncio as redis
from fastapi import Depends

from app.config import settings
from app.services.event_emitter import EventEmitter
from app.core.mongo_client import MongoClient
from app.core.mcp_client import MCPClient, MCPServerConfig
from app.core.policy_engine import PolicyEngine
from app.services.context_manager import ContextManager
from app.services.tool_registry import ToolRegistry
from app.services.orchestration_service import OrchestrationService


# =============================================================================
# Redis Client
# =============================================================================

_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis:
    """
    Dependency to get Redis client instance (singleton).
    
    Returns:
        redis.Redis: Async Redis client from redis.asyncio
    """
    global _redis_client
    
    if _redis_client is None:
        # Use redis.asyncio.Redis for async operations
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password if settings.redis_password else None,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    
    return _redis_client


async def close_redis_client():
    """Close Redis client on application shutdown"""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()  # Use aclose() for async redis client
        _redis_client = None


# =============================================================================
# Event Emitter
# =============================================================================

async def get_event_emitter() -> EventEmitter:
    """
    Dependency to get EventEmitter service instance.
    
    Returns:
        EventEmitter: Event emitter with Redis client
    """
    redis_client = await get_redis_client()
    return EventEmitter(redis_client)


# =============================================================================
# MongoDB Client
# =============================================================================

_mongo_client: MongoClient | None = None


async def get_mongo_client() -> MongoClient:
    """
    Dependency to get MongoDB client instance (singleton).
    
    Returns:
        MongoClient: Async MongoDB client
    """
    global _mongo_client
    
    if _mongo_client is None:
        _mongo_client = MongoClient(
            connection_url=settings.mongodb_url,
            database_name=settings.mongodb_db_name
        )
        # Initialize connection
        await _mongo_client.connect()
    
    return _mongo_client


# =============================================================================
# MCP Process Server Client (Sugar Industry)
# =============================================================================

_mcp_client: MCPClient | None = None


async def get_mcp_client() -> MCPClient:
    """
    Dependency to get MCP Process Server client instance (singleton).
    
    Used for industry-specific simulations (sugar, etc.)
    
    Returns:
        MCPClient: MCP process server client
    """
    global _mcp_client
    
    if _mcp_client is None:
        config = MCPServerConfig(
            command=settings.mcp_server_command,
            args=[settings.mcp_server_args],
            env={
                "CALC_ENGINE_API_URL": settings.mcp_server_env_calc_engine_url,
                "MAX_STORED_RUNS": str(settings.mcp_server_env_max_stored_runs),
                "MONGODB_URI": settings.mcp_server_env_mongodb_uri
            }
        )
        _mcp_client = MCPClient(config)
        # Initialize connection
        await _mcp_client.connect()
    
    return _mcp_client


async def close_mcp_clients():
    """Close MCP client on application shutdown."""
    global _mcp_client
    
    if _mcp_client:
        await _mcp_client.disconnect()
        _mcp_client = None


# =============================================================================
# Orchestration Service
# =============================================================================

async def get_orchestration_service(
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client),
    mcp_client: MCPClient = Depends(get_mcp_client),
    event_emitter: EventEmitter = Depends(get_event_emitter)
) -> OrchestrationService:
    """
    Dependency to get OrchestrationService instance.
    
    Creates a new instance per request with all required dependencies.
    
    Returns:
        OrchestrationService: Orchestration service instance
    """
    # Initialize services
    # ContextManager expects raw Motor client, not our wrapper
    context_manager = ContextManager(
        redis_client=redis_client, 
        mongo_client=mongo_client._client  # Pass underlying Motor client
    )
    tool_registry = ToolRegistry()
    policy_engine = PolicyEngine()  # PolicyEngine takes no arguments
    
    # Create orchestration service with MCP client
    orchestration = OrchestrationService(
        context_manager=context_manager,
        tool_registry=tool_registry,
        policy_engine=policy_engine,
        mcp_client=mcp_client,
        event_emitter=event_emitter,
        anthropic_api_key=settings.anthropic_api_key
    )
    
    return orchestration
