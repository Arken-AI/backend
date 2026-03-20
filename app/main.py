"""
FastAPI Application Entry Point

Main FastAPI application with SSE streaming support.
"""

import logging
import logging.config
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class _SuppressHealthOK(logging.Filter):
    """Drop uvicorn access-log lines for successful health checks.
    Failures (non-2xx) are still logged.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "GET /api/health" in msg and any(f" {c} " in msg for c in ("200", "201", "204")):
            return False
        return True


# Install via dictConfig so it survives --reload worker restarts.
# dictConfig runs at import time in every worker process, before uvicorn
# emits its first access log line.
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "suppress_health_ok": {
            "()": _SuppressHealthOK,
        }
    },
    "handlers": {
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "filters": ["suppress_health_ok"],
        }
    },
    "formatters": {
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        }
    },
    "loggers": {
        "uvicorn.access": {
            "handlers": ["access"],
            "level": "INFO",
            "propagate": False,
        }
    },
})

from app.api import stream, health, chat, test_stream, runs, reports, auth
from app.dependencies import close_redis_client, close_llm_provider, close_mongo_client
from app.config import settings

logger = logging.getLogger(__name__)


class CORSSafeErrorMiddleware(BaseHTTPMiddleware):
    """
    Middleware that catches unhandled exceptions (e.g. dependency injection failures,
    connection drops) and returns a proper JSON error response.
    
    Without this, when a dependency like Redis/MongoDB crashes, FastAPI generates 
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
    - Shutdown: Close Redis, MongoDB, LLM clients, etc.
    """
    # Startup
    yield
    
    # Shutdown
    await close_llm_provider()  # Close LLM httpx connection pool
    await close_mongo_client()  # Close MongoDB connection pool
    await close_redis_client()


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Returns:
        FastAPI: Configured FastAPI application instance
    """
    app = FastAPI(
        title="ARKEN AI Backend",
        description="AI-powered chat backend for process engineering",
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
    # (Redis/MongoDB connection drops) and converts them to proper JSON 
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
        "service": "ARKEN AI Backend",
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
