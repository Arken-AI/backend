"""
Dependency Injection

Provides dependency injection for database connections, clients, and services.
"""

from typing import AsyncGenerator
import redis.asyncio as redis

from app.config import settings
from app.services.event_emitter import EventEmitter

# TODO: Implement in Phase 2
# from app.core.mongo_client import MongoClient
# from app.core.mcp_client import MCPClient
# from app.core.llm_provider import ClaudeProvider


# =============================================================================
# Redis Client
# =============================================================================

_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis:
    """
    Dependency to get Redis client instance (singleton).
    
    Returns:
        redis.Redis: Async Redis client
    """
    global _redis_client
    
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password if settings.redis_password else None,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
    
    return _redis_client


async def close_redis_client():
    """Close Redis client on application shutdown"""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
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
# TODO: Implement remaining dependencies
# =============================================================================

async def get_mongo_client():
    """
    Dependency to get MongoDB client instance.
    To be implemented in Phase 2.
    """
    # TODO: Implement MongoDB client dependency
    pass


async def get_mcp_client():
    """
    Dependency to get MCP client instance.
    To be implemented in Phase 2.
    """
    # TODO: Implement MCP client dependency
    pass


async def get_llm_provider():
    """
    Dependency to get LLM provider (Claude) instance.
    To be implemented in Phase 2.
    """
    # TODO: Implement LLM provider dependency
    pass
