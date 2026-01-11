"""
Health Check Endpoint

This module provides system health check endpoints to verify all services are operational.
"""

import time
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.dependencies import (
    get_redis_client,
    get_mongo_client,
    get_mcp_client
)
from app.config import settings
from app.models.requests import HealthResponse, ServiceStatus


router = APIRouter()


async def check_redis_health(redis_client) -> ServiceStatus:
    """Check Redis connection and performance"""
    try:
        start_time = time.time()
        await redis_client.ping()
        response_time = (time.time() - start_time) * 1000  # Convert to ms
        
        return ServiceStatus(
            name="Redis",
            status="healthy",
            message="Connected and responsive",
            response_time_ms=round(response_time, 2)
        )
    except Exception as e:
        print(f"ERROR: " + str(f"Redis health check failed: {e}"))
        return ServiceStatus(
            name="Redis",
            status="unhealthy",
            message=f"Connection failed: {str(e)}"
        )


async def check_mongodb_health(mongo_client) -> ServiceStatus:
    """Check MongoDB connection and performance"""
    try:
        start_time = time.time()
        # Access the underlying Motor client and ping
        if hasattr(mongo_client, '_client') and mongo_client._client:
            await mongo_client._client.admin.command('ping')
        else:
            # Client not initialized yet, try to check connection status
            is_connected = await mongo_client.is_connected()
            if not is_connected:
                return ServiceStatus(
                    name="MongoDB",
                    status="unhealthy",
                    message="Client not connected"
                )
        
        response_time = (time.time() - start_time) * 1000  # Convert to ms
        
        return ServiceStatus(
            name="MongoDB",
            status="healthy",
            message="Connected and responsive",
            response_time_ms=round(response_time, 2)
        )
    except Exception as e:
        print(f"ERROR: " + str(f"MongoDB health check failed: {e}"))
        return ServiceStatus(
            name="MongoDB",
            status="unhealthy",
            message=f"Connection failed: {str(e)}"
        )


async def check_mcp_health(mcp_client) -> ServiceStatus:
    """Check MCP server connection by listing tools"""
    try:
        start_time = time.time()
        # Try to list tools as a health check
        tools = await mcp_client.list_tools()
        response_time = (time.time() - start_time) * 1000  # Convert to ms
        
        tool_count = len(tools) if tools else 0
        
        return ServiceStatus(
            name="MCP Server",
            status="healthy",
            message=f"Connected, {tool_count} tools available",
            response_time_ms=round(response_time, 2)
        )
    except Exception as e:
        print(f"ERROR: " + str(f"MCP health check failed: {e}"))
        return ServiceStatus(
            name="MCP Server",
            status="unhealthy",
            message=f"Connection failed: {str(e)}"
        )


async def check_llm_health() -> ServiceStatus:
    """Check LLM provider (Claude) availability"""
    try:
        # Check if Claude API key is configured
        if settings.anthropic_api_key and len(settings.anthropic_api_key) > 0:
            return ServiceStatus(
                name="LLM Provider",
                status="healthy",
                message="Claude API key configured (model: claude-sonnet-4-20250514)"
            )
        else:
            return ServiceStatus(
                name="LLM Provider",
                status="unknown",
                message="Claude API key not set"
            )
    except Exception as e:
        print(f"ERROR: LLM health check failed: {e}")
        return ServiceStatus(
            name="LLM Provider",
            status="unhealthy",
            message=f"Client initialization failed: {str(e)}"
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="System Health Check",
    description="Check the health status of all backend services (Redis, MongoDB, MCP, LLM)",
    responses={
        200: {
            "description": "All services are healthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "healthy",
                        "services": {
                            "redis": {
                                "name": "Redis",
                                "status": "healthy",
                                "message": "Connected and responsive",
                                "response_time_ms": 2.5
                            },
                            "mongodb": {
                                "name": "MongoDB",
                                "status": "healthy",
                                "message": "Connected and responsive",
                                "response_time_ms": 5.1
                            },
                            "mcp": {
                                "name": "MCP Server",
                                "status": "healthy",
                                "message": "Connected, 13 tools available",
                                "response_time_ms": 10.3
                            },
                            "llm": {
                                "name": "LLM Provider",
                                "status": "healthy",
                                "message": "Google Gemini client initialized (model: gemini-2.0-flash)"
                            }
                        },
                        "timestamp": "2026-01-07T10:00:00Z",
                        "version": "1.0.0"
                    }
                }
            }
        },
        503: {
            "description": "One or more services are unhealthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "unhealthy",
                        "services": {
                            "redis": {
                                "name": "Redis",
                                "status": "unhealthy",
                                "message": "Connection failed: Connection refused"
                            }
                        },
                        "timestamp": "2026-01-07T10:00:00Z",
                        "version": "1.0.0"
                    }
                }
            }
        }
    }
)
async def health_check(
    redis_client=Depends(get_redis_client),
    mongo_client=Depends(get_mongo_client),
    mcp_client=Depends(get_mcp_client)
):
    """
    Perform health check on all backend services.
    
    Returns:
        HealthResponse with overall status and individual service statuses
    """
    # Check all services in parallel
    service_checks = {
        "redis": await check_redis_health(redis_client),
        "mongodb": await check_mongodb_health(mongo_client),
        "mcp": await check_mcp_health(mcp_client),
        "llm": await check_llm_health()
    }
    
    # Determine overall health status
    unhealthy_services = [
        name for name, check in service_checks.items()
        if check.status == "unhealthy"
    ]
    unknown_services = [
        name for name, check in service_checks.items()
        if check.status == "unknown"
    ]
    
    if unhealthy_services:
        overall_status = "unhealthy"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif unknown_services:
        overall_status = "degraded"
        http_status = status.HTTP_200_OK
    else:
        overall_status = "healthy"
        http_status = status.HTTP_200_OK
    
    response = HealthResponse(
        status=overall_status,
        services=service_checks,
        timestamp=datetime.now(),
        version="1.0.0"
    )
    
    return JSONResponse(
        status_code=http_status,
        content=response.model_dump(mode="json")
    )
