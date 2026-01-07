"""
FastAPI Application Entry Point

Main FastAPI application with SSE streaming support.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import stream
from app.dependencies import close_redis_client
from app.config import settings

# TODO: Import additional routers in Phase 7
# from app.api import chat, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown events:
    - Startup: Initialize connections
    - Shutdown: Close Redis, MongoDB, etc.
    """
    # Startup
    yield
    
    # Shutdown
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Restrict in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(stream.router, prefix="/api", tags=["streaming"])
    
    # TODO: Include additional routers in Phase 7
    # app.include_router(health.router, prefix="/health", tags=["health"])
    # app.include_router(chat.router, prefix="/chat", tags=["chat"])
    
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
