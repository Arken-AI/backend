"""
FastAPI Application Entry Point

Main FastAPI application with SSE streaming support.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import stream, health, chat, test_stream, runs
from app.dependencies import close_redis_client, close_mcp_clients
from app.config import settings


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Restrict in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(stream.router, prefix="/api", tags=["streaming"])
    app.include_router(test_stream.router, prefix="/api", tags=["testing"])
    app.include_router(runs.router, prefix="/api", tags=["runs"])
    
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
