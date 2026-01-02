"""
Health Check Endpoints

This module will be implemented in Phase 7 to provide health check endpoints.
"""

from fastapi import APIRouter
from typing import Dict, Any

router = APIRouter()


@router.get("/")
async def health_check() -> Dict[str, Any]:
    """
    Basic health check endpoint.
    
    Returns service status and version information.
    Will be expanded in Phase 7 to check MongoDB, Redis, and MCP connections.
    """
    return {
        "status": "healthy",
        "service": "MCP Chat Backend",
        "version": "0.1.0",
        "message": "Service is running. Detailed health checks will be added in Phase 7."
    }


# TODO: Implement in Phase 7
# @router.get("/detailed")
# async def detailed_health_check():
#     """Check MongoDB, Redis, and MCP server connections"""
#     pass
