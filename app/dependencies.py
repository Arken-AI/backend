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
from app.core.mcp_client import MCPClient, MCPServerConfig, MCPClientRegistry
from app.services.context_manager import ContextManager
from app.services.tool_registry import ToolRegistry
from app.services.orchestration_service import OrchestrationService
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
            model=settings.llm_model_claude
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
# MCP Client Registry (Multiple Servers)
# =============================================================================

_mcp_registry: MCPClientRegistry | None = None


async def get_mcp_registry() -> MCPClientRegistry:
    """
    Dependency to get MCP Client Registry instance (singleton).
    
    Manages connections to multiple MCP servers:
    - process_server: Sugar industry simulations
    - calc_engine: Dynamic flowsheet simulations
    
    Returns:
        MCPClientRegistry: Registry managing all MCP server connections
    """
    global _mcp_registry
    
    if _mcp_registry is None:
        _mcp_registry = MCPClientRegistry()
        await _mcp_registry.initialize()
        print(f"INFO: MCP Registry initialized with servers: {_mcp_registry.connected_servers}")
    
    return _mcp_registry


# Legacy: Keep get_mcp_client for backward compatibility
_mcp_client: MCPClient | None = None


async def get_mcp_client() -> MCPClient:
    """
    Dependency to get MCP Process Server client instance (singleton).
    
    DEPRECATED: Use get_mcp_registry() instead for multi-server support.
    This is kept for backward compatibility.
    
    Returns:
        MCPClient: MCP process server client
    """
    global _mcp_client
    
    if _mcp_client is None:
        config = MCPServerConfig.from_env_process_server()
        _mcp_client = MCPClient(config)
        await _mcp_client.connect()
    
    return _mcp_client


async def close_mcp_clients():
    """Close all MCP clients on application shutdown."""
    global _mcp_client, _mcp_registry
    
    # Close registry (preferred)
    if _mcp_registry:
        await _mcp_registry.shutdown()
        _mcp_registry = None
    
    # Close legacy single client
    if _mcp_client:
        await _mcp_client.disconnect()
        _mcp_client = None


# =============================================================================
# Orchestration Service
# =============================================================================

async def get_orchestration_service(
    redis_client: redis.Redis = Depends(get_redis_client),
    mongo_client: MongoClient = Depends(get_mongo_client),
    mcp_registry: MCPClientRegistry = Depends(get_mcp_registry),
    event_emitter: EventEmitter = Depends(get_event_emitter)
) -> OrchestrationService:
    """
    Dependency to get OrchestrationService instance.
    
    Creates a new instance per request with all required dependencies.
    Uses MCPClientRegistry for multi-server support.
    
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
    
    # Create orchestration service with MCP registry (multi-server)
    # Use singleton LLM provider to reuse httpx connection pool
    orchestration = OrchestrationService(
        context_manager=context_manager,
        tool_registry=tool_registry,
        mcp_registry=mcp_registry,  # Use registry instead of single client
        event_emitter=event_emitter,
        anthropic_api_key=settings.anthropic_api_key,
        llm_provider=get_llm_provider()  # Reuse singleton
    )
    
    return orchestration
