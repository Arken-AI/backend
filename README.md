# ARKEN AI — Backend Service

FastAPI orchestration service that powers the conversational interface for the ARKEN AI heat exchanger design platform. Claude Sonnet 4.6 (Loop 1) interprets natural language, dispatches to the HX Engine, and streams real-time design progress back to the browser.

**Port:** `8001`  
**Architecture:** Clean Architecture — API → Services → Core

---

## Overview

The Backend is the entry point for all user interactions. It:

1. Receives natural language from the frontend chat panel
2. Passes it to Claude (with `hx_validate_requirements` and `hx_design` tools registered)
3. Claude extracts process parameters and calls the HX Engine via HTTP
4. Streams 8 SSE event types (chat deltas, thinking, tool calls, design events) back to the browser via Redis Streams
5. On design completion, fetches step records from the HX Engine and generates a full design report via Claude

It never runs engineering calculations directly — all math lives in the HX Engine.

---

## Prerequisites

- Python 3.10+
- Redis 7
- MongoDB 7
- Anthropic API key (Claude Sonnet 4.6)
- HX Engine running on port `8100`

---

## Quick Start

```bash
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY, MONGODB_URL, REDIS_HOST
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

API available at **http://localhost:8001**  
Swagger UI: **http://localhost:8001/docs**  
Health check: `GET /health`

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude Sonnet 4.6 API key |
| `MONGODB_URL` | Yes | MongoDB connection string |
| `MONGODB_DB_NAME` | No | Database name (default: `arken_process_db`) |
| `REDIS_HOST` | Yes | Redis host (default: `localhost`) |
| `REDIS_PORT` | No | Redis port (default: `6379`) |
| `REDIS_EVENT_TTL` | No | SSE event stream TTL in seconds (default: `3600`) |
| `HX_ENGINE_BASE_URL` | Yes | HX Engine base URL (default: `http://localhost:8100`) |
| `HX_ENGINE_INTERNAL_SECRET` | Yes | Shared secret for service-to-service auth |
| `APP_SHARED_PASSWORD` | Yes | Shared login password (pre-JWT auth) |
| `CORS_ORIGINS` | No | Comma-separated allowed origins |

---

## Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, lifespan, middleware
│   ├── config.py            # Pydantic settings (env-driven)
│   ├── dependencies.py      # Dependency injection
│   ├── api/
│   │   ├── chat.py          # POST /chat
│   │   ├── stream.py        # SSE /chat/{id}/stream
│   │   ├── auth.py          # Login, visitor tracking
│   │   └── health.py        # GET /health
│   ├── core/
│   │   ├── redis_client.py  # Async Redis client
│   │   └── mongo_client.py  # Async MongoDB client (Motor)
│   ├── models/              # Pydantic request/response models
│   ├── services/
│   │   ├── orchestration_service.py  # Loop 1: Claude + tool dispatch
│   │   ├── context_manager.py        # Conversation context (Redis cache)
│   │   ├── event_emitter.py          # Publishes SSE events to Redis Streams
│   │   ├── pdf_processor.py          # PDF/DOCX text extraction
│   │   └── tool_registry.py          # engines.yaml → Anthropic tool format
│   ├── workers/             # Background event workers
│   └── utils/               # Shared helpers
├── engines.yaml             # Tool definitions for Claude (HX Engine tools)
├── pyproject.toml
├── Dockerfile
└── .env.example
```

---

## Key Design Decisions

**Loop 1 Orchestration:** Claude receives two tools (`hx_validate_requirements`, `hx_design`). Claude extracts parameters, validates, then immediately starts the design in the same turn. The frontend opens a separate SSE stream to the HX Engine for step-by-step events.

**Tool Registry (`engines.yaml`):** Tool schemas are defined in YAML, not hardcoded Python. Supports `${ENV_VAR:-default}` syntax for environment-specific URLs. Adding a new engine requires only a YAML change.

**SSE via Redis Streams:** Chat events (thinking, deltas, tool calls, design reports) are published to Redis Streams with 1-hour TTL. The frontend reads via `GET /api/chat/{id}/stream`. Redis Streams support replay on reconnect.

**Dual SSE Architecture:** The frontend opens two simultaneous EventSource connections — one to the Backend (chat events) and one directly to the HX Engine (step-by-step pipeline events). The Backend never proxies HX Engine SSE.

---

## Running Tests

```bash
cd backend
pytest tests/ -v
```

---

## Docker

The Backend runs as the `backend` service in `docker/docker-compose.yml`. See `docker/README.md` for the full stack setup.

```bash
cd docker
docker compose up -d
```

