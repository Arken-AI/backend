"""
Dependency Injection

Provides dependency injection for database connections, clients, and services.
"""

import asyncio
import logging
from typing import AsyncGenerator
import redis.asyncio as redis
from fastapi import Depends

from app.config import settings
from app.services.event_emitter import EventEmitter
from app.core.mongo_client import MongoClient
from app.services.context_manager import ContextManager
from app.services.orchestration_service import OrchestrationService
from app.services.tool_registry import ToolRegistry
from app.core.llm_provider import ClaudeProvider

logger = logging.getLogger(__name__)


# =============================================================================
# LLM Provider (Singleton)
# =============================================================================

_llm_provider: ClaudeProvider | None = None


def get_llm_provider() -> ClaudeProvider:
    """
    Get singleton ClaudeProvider instance.
    
    Reuses the same AsyncAnthropic httpx connection pool across all requests
    to prevent socket/connection leaks.
    """
    global _llm_provider
    
    if _llm_provider is None:
        _llm_provider = ClaudeProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model
        )
    
    return _llm_provider


async def close_llm_provider():
    """Close LLM provider on application shutdown"""
    global _llm_provider
    if _llm_provider:
        await _llm_provider.close()
        _llm_provider = None


# =============================================================================
# Redis Client
# =============================================================================

_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis:
    """
    Dependency to get Redis client instance (singleton).
    
    Validates the connection is alive and recreates if stale.
    
    Returns:
        redis.Redis: Async Redis client from redis.asyncio
    """
    global _redis_client
    
    if _redis_client is not None:
        # Verify connection is alive
        try:
            await _redis_client.ping()
        except Exception:
            logger.warning("Redis connection stale, reconnecting...")
            try:
                await _redis_client.aclose()
            except Exception:
                pass
            _redis_client = None
    
    if _redis_client is None:
        # Use redis.asyncio.Redis for async operations
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password if settings.redis_password else None,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
            retry_on_timeout=True,
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
_mongo_lock: asyncio.Lock = asyncio.Lock()


async def get_mongo_client() -> MongoClient:
    """
    Dependency to get MongoDB client instance (singleton).
    
    Uses an asyncio.Lock to prevent race conditions where concurrent
    coroutines could close/recreate the Motor client while another
    coroutine is still using it (causes "Cannot use MongoClient after close").
    
    Returns:
        MongoClient: Async MongoDB client
    """
    global _mongo_client
    
    # Fast path: if connected, return without acquiring lock
    if _mongo_client is not None:
        try:
            if _mongo_client._client is not None:
                await _mongo_client._client.admin.command("ping")
                return _mongo_client
        except Exception:
            pass  # Fall through to locked reconnection
    
    # Slow path: acquire lock to safely reconnect
    async with _mongo_lock:
        # Double-check after acquiring lock (another coroutine may have fixed it)
        if _mongo_client is not None:
            try:
                if _mongo_client._client is not None:
                    await _mongo_client._client.admin.command("ping")
                    return _mongo_client
            except Exception:
                logger.warning("MongoDB connection stale, reconnecting...")
                try:
                    _mongo_client._client.close()
                except Exception:
                    pass
                _mongo_client = None
        
        # Create and connect new client
        new_client = MongoClient(
            connection_url=settings.mongodb_url,
            database_name=settings.mongodb_db_name
        )
        try:
            await new_client.connect()
        except Exception:
            # Don't leave a half-initialized singleton
            try:
                await new_client.disconnect()
            except Exception:
                pass
            raise
        
        _mongo_client = new_client
    
    return _mongo_client


async def close_mongo_client():
    """Close MongoDB client on application shutdown."""
    global _mongo_client
    if _mongo_client:
        await _mongo_client.disconnect()
        _mongo_client = None


# =============================================================================
# Orchestration Service
# =============================================================================

_tool_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Singleton ToolRegistry — reads engines.yaml once at startup."""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


async def get_orchestration_service(
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client),
    event_emitter: EventEmitter = Depends(get_event_emitter)
) -> OrchestrationService:
    """
    Dependency to get OrchestrationService instance.

    Creates a new instance per request with all required dependencies,
    including the HX Engine client and tool registry for browser-based
    HX design (Path 2).
    """
    context_manager = ContextManager(
        redis_client=redis_client,
        mongo_client=mongo_client._client,  # Pass underlying Motor client
    )

    engine_client = await get_engine_client()

    return OrchestrationService(
        context_manager=context_manager,
        event_emitter=event_emitter,
        llm_provider=get_llm_provider(),
        redis_client=redis_client,
        engine_client=engine_client,
        tool_registry=get_tool_registry(),
    )


# =============================================================================
# HX Engine Client
# =============================================================================

_engine_client: "HXEngineClient | None" = None
_engine_client_lock: asyncio.Lock | None = None


def _get_engine_lock() -> asyncio.Lock:
    global _engine_client_lock
    if _engine_client_lock is None:
        _engine_client_lock = asyncio.Lock()
    return _engine_client_lock


async def get_engine_client():
    """
    Singleton HXEngineClient.  Connects lazily on first call.
    Returns the client even when the HX Engine is unreachable —
    callers should call health_check() if they need to verify availability.
    """
    global _engine_client
    if _engine_client is not None:
        return _engine_client
    async with _get_engine_lock():
        if _engine_client is None:
            from app.core.engine_client import HXEngineClient
            _engine_client = HXEngineClient()
            await _engine_client.connect()
    return _engine_client


async def close_engine_client():
    """Close HX Engine client on application shutdown."""
    global _engine_client
    if _engine_client:
        await _engine_client.close()
        _engine_client = None
