"""
FastAPI Application Entry Point

This is the main FastAPI application that will be implemented in Phase 7.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# TODO: Import routers in Phase 7
# from app.api import chat, health
# from app.config import settings


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
    )
    
    # TODO: Add CORS middleware in Phase 7
    # app.add_middleware(
    #     CORSMiddleware,
    #     allow_origins=settings.CORS_ORIGINS,
    #     allow_credentials=True,
    #     allow_methods=["*"],
    #     allow_headers=["*"],
    # )
    
    # TODO: Include routers in Phase 7
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
        "message": "Backend structure initialized. API endpoints will be added in Phase 7."
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
