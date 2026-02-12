"""
FastAPI Application Entry Point

Main FastAPI application with SSE streaming support.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import stream, health, chat, test_stream, runs, reports, auth
from app.dependencies import close_redis_client, close_mcp_clients, close_llm_provider
from app.config import settings

logger = logging.getLogger(__name__)


class CORSSafeErrorMiddleware(BaseHTTPMiddleware):
    """
    Middleware that catches unhandled exceptions (e.g. dependency injection failures,
    connection drops) and returns a proper JSON error response.
    
    Without this, when a dependency like Redis/MongoDB/MCP crashes, FastAPI generates 
    a raw 500 response during dependency resolution — BEFORE CORSMiddleware can add 
    CORS headers. The browser then reports a misleading "CORS error" instead of the 
    real connection error.
    
    This middleware sits INSIDE CORSMiddleware in the stack, so any response it 
    produces will still get CORS headers added by the outer CORSMiddleware.
    """
    
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logger.error(f"Unhandled exception in request {request.method} {request.url.path}: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error. A backend service may be temporarily unavailable.",
                    "error": str(e)
                }
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown events:
    - Startup: Initialize connections
    - Shutdown: Close Redis, MongoDB, MCP clients, etc.
    """
    # Startup
    yield
    
    # Shutdown
    await close_llm_provider()  # Close LLM httpx connection pool
    await close_mcp_clients()  # Close both MCP server connections
    await close_redis_client()


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Returns:
        FastAPI: Configured FastAPI application instance
    """
    app = FastAPI(
        title="MCP Chat Backend",
        description="Production-grade chat backend for MCP Process Server",
        version="0.1.0",
        lifespan=lifespan
    )
    
    # CORS middleware (allow frontend to connect)
    # NOTE: Middleware stack is LIFO — last added runs first (outermost).
    # CORSMiddleware is added last so it wraps everything, ensuring CORS 
    # headers are present on ALL responses, including error responses.
    
    if settings.cors_origins == "*":
        origins = ["*"]
    else:
        origins = [o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()]
    logger.info(f"CORS allowed origins: {origins}")
    
    # 1. Add error-catching middleware FIRST (runs inside CORS)
    # This catches unhandled exceptions from dependency injection failures
    # (Redis/MongoDB/MCP connection drops) and converts them to proper JSON 
    # responses that CORSMiddleware can then decorate with CORS headers.
    app.add_middleware(CORSSafeErrorMiddleware)
    
    # 2. Add CORS middleware SECOND (runs outside, wraps everything)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    
    # Include routers
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(auth.router, prefix="/api/auth", tags=["authentication"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(stream.router, prefix="/api", tags=["streaming"])
    app.include_router(test_stream.router, prefix="/api", tags=["testing"])
    app.include_router(runs.router, prefix="/api", tags=["runs"])
    app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
    
    return app


app = create_app()


@app.get("/")
async def root():
    """Root endpoint - basic health check"""
    return {
        "service": "MCP Chat Backend",
        "version": "0.1.0",
        "status": "online",
        "features": ["SSE Event Streaming"],
        "endpoints": {
            "stream": "/api/chat/{request_id}/stream",
            "docs": "/docs"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
