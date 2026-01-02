# MCP Chat Backend

Production-grade chat backend that replicates the Claude Desktop experience for the MCP Process Server.

---

## 🎯 Project Status

**Current Phase**: Phase 0 - Infrastructure Setup  
**Backend Development**: In Progress (Phases 0-7)  
**Frontend Development**: Not Started (Phase 8+)

---

## 📋 Phase 0: Infrastructure Setup - Progress

### ✅ Step 1: Redis Added to Docker Compose (COMPLETE)

**What was done:**
- Added Redis 7 service to `mcp_process_server/docker/docker-compose.yml`
- Configured Redis with persistence (RDB + AOF)
- Added memory management (256MB limit, LRU eviction)
- Added health checks
- Added RedisInsight UI for debugging

**Services Running:**
- ✅ MongoDB: `localhost:27017` (existing)
- ✅ Mongo Express: `http://localhost:8081` (existing)
- ✅ Redis: `localhost:6379` (new)
- ✅ RedisInsight: `http://localhost:5540` (new)

**Verification:**
```bash
cd mcp_process_server/docker
docker-compose ps
# All services should show as "healthy" or "running"

# Test Redis
docker exec -it arken-redis redis-cli ping
# Should return: PONG
```

**Access RedisInsight UI:**
- URL: http://localhost:5540
- Add database with Host: `redis`, Port: `6379`

---

### ✅ Step 2: Environment Configuration (COMPLETE)

**What was done:**
- Created `backend/.env.example` with all configuration templates
- Created `backend/.env` for local development
- Created `backend/ENV_VARIABLES.md` with detailed documentation
- Added `backend/.env` to `.gitignore` for security

**Files Created:**
- ✅ `backend/.env.example` - Template (safe to commit)
- ✅ `backend/.env` - Local config (git ignored)
- ✅ `backend/ENV_VARIABLES.md` - Documentation

**Configuration Overview:**

| Category | Variables | Status |
|----------|-----------|--------|
| MongoDB | `MONGODB_URL`, `MONGODB_DB_NAME` | ✅ Configured |
| Redis | `REDIS_HOST`, `REDIS_PORT`, `REDIS_EVENT_TTL` | ✅ Configured |
| Anthropic | `ANTHROPIC_API_KEY` | ⚠️ Needs API key |
| MCP Server | `MCP_SERVER_COMMAND`, `MCP_SERVER_ARGS`, `MCP_SERVER_CWD` | ✅ Configured |
| API | `API_HOST`, `API_PORT`, `CORS_ORIGINS` | ✅ Configured |
| Workers | `RQ_QUEUE_NAME`, `RQ_WORKER_COUNT` | ✅ Configured |

**MongoDB Details:**
- Database: `arken_process_db` (already exists)
- Collections: `industries`, `processes`, `equipment_types`, `stream_schemas`, `runs`
- New collection needed: `conversations` (will be created in Phase 2)
- 19 simulation runs already stored

**Action Required:**
```bash
# Edit backend/.env and add your Anthropic API key
# Get it from: https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
```

**Verification:**
```bash
# Test MongoDB connection
docker exec -it arken-mongodb mongosh \
  "mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin" \
  --quiet --eval "db.runs.countDocuments()"
# Should return: 19 (or current count)

# Test Redis connection
redis-cli ping
# Should return: PONG
```

---

### ⏳ Step 3: Backend Project Structure (PENDING)

**What will be done:**
- Create directory structure for FastAPI backend
- Create all `__init__.py` files
- Create `requirements.txt` with dependencies
- Create placeholder files

**Directory Structure to Create:**
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── dependencies.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   └── health.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── redis_client.py
│   │   ├── mongo_client.py
│   │   ├── mcp_client.py
│   │   └── llm_provider.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── events.py
│   │   ├── requests.py
│   │   └── storage.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── tool_registry.py
│   │   ├── intent_detector.py
│   │   ├── policy_gates.py
│   │   ├── event_emitter.py
│   │   └── result_summarizer.py
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── agentic_loop.py
│   │   ├── report_generator.py
│   │   └── integrity_guard.py
│   └── utils/
│       ├── __init__.py
│       └── helpers.py
├── requirements.txt
├── Dockerfile
└── .dockerignore
```

**Status:** Not Started

---

### ⏳ Step 4: Skip Frontend (Backend-First Approach) (N/A)

Frontend initialization is skipped in Phase 0. Frontend development will begin in Phase 8 after backend is fully functional and tested.

---

## 🔧 Quick Start

### Prerequisites
- Docker and Docker Compose installed
- Python 3.11+ installed
- Redis CLI (optional, for testing)
- Anthropic API key

### Start All Services
```bash
# Navigate to docker directory
cd mcp_process_server/docker

# Start all services (MongoDB, Redis, UIs)
docker-compose up -d

# Check all services are healthy
docker-compose ps
```

### Configure Backend
```bash
# Copy environment template
cp backend/.env.example backend/.env

# Edit .env and add your Anthropic API key
# Get it from: https://console.anthropic.com/
nano backend/.env  # or use any editor
```

### Verify Setup
```bash
# Test MongoDB
docker exec -it arken-mongodb mongosh \
  "mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin" \
  --quiet --eval "db.runs.countDocuments()"

# Test Redis
docker exec -it arken-redis redis-cli ping

# Access UIs
# MongoDB: http://localhost:8081 (user: arken, pass: arken_ui_2025)
# Redis: http://localhost:5540
```

---

## 📦 Services Overview

### MongoDB (Port 27017)
- **Purpose**: Permanent storage for simulation runs and conversations
- **Database**: `arken_process_db`
- **User**: `arken_app` / `arken_app_password`
- **UI**: http://localhost:8081

### Redis (Port 6379)
- **Purpose**: Real-time event streaming and session management
- **Configuration**: 256MB max memory, 1-hour TTL for events
- **UI**: http://localhost:5540

### FastAPI Backend (Port 8001)
- **Purpose**: REST API and SSE endpoints for chat interface
- **Status**: Not yet implemented (Phase 2-7)
- **Endpoints**: `/chat`, `/chat/{id}/stream`, `/health`

### RQ Workers
- **Purpose**: Background job processing for agentic loop
- **Status**: Not yet implemented (Phase 5)
- **Queue**: `default`

---

## 📚 Documentation

- **Environment Variables**: See `ENV_VARIABLES.md`
- **Development Plan**: See `DEVELOPMENT_PLAN.md`
- **Docker Setup**: See `mcp_process_server/docker/README.md`
- **API Documentation**: Will be available at `/docs` once backend is running

---

## 🚀 Next Steps

1. ✅ Complete Step 3: Create backend project structure
2. ✅ Complete Phase 0 verification
3. ➡️ Move to Phase 1: Tool Registry & Metadata

---

## 🛠️ Development Workflow

### Daily Startup
```bash
# 1. Start Docker services
cd mcp_process_server/docker
docker-compose up -d

# 2. Activate Python environment (when ready)
# cd backend
# source venv/bin/activate  # or your preferred method

# 3. Start FastAPI server (Phase 7+)
# uvicorn app.main:app --reload --port 8001

# 4. Start RQ worker (Phase 5+)
# rq worker
```

### Stopping Services
```bash
cd mcp_process_server/docker
docker-compose down
```

---

## 🔍 Troubleshooting

### MongoDB Connection Issues
```bash
# Check if MongoDB is running
docker ps | grep mongodb

# Check MongoDB logs
cd mcp_process_server/docker
docker-compose logs mongodb

# Test connection
docker exec -it arken-mongodb mongosh \
  "mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin" \
  --quiet --eval "db.adminCommand('ping')"
```

### Redis Connection Issues
```bash
# Check if Redis is running
docker ps | grep redis

# Check Redis logs
cd mcp_process_server/docker
docker-compose logs redis

# Test connection
docker exec -it arken-redis redis-cli ping
```

### Port Conflicts
```bash
# Check what's using a port (e.g., 6379)
lsof -i :6379

# Stop local Redis if running
brew services stop redis  # macOS
sudo systemctl stop redis  # Linux
```

---

## 📝 Notes

- All backend development happens in `backend/` directory
- Frontend development deferred to Phase 8
- No changes to existing `mcp_process_server/` code
- No changes to existing `main.py` calculation engine
- Use existing MongoDB database `arken_process_db`
- New `conversations` collection will be added in Phase 2

---

## 🔐 Security

### Git Ignore
The following files are git-ignored for security:
- `backend/.env` - Contains secrets
- `backend/__pycache__/` - Python cache
- `backend/venv/` - Virtual environment

### Safe to Commit
- `backend/.env.example` - Template only
- `backend/ENV_VARIABLES.md` - Documentation
- All source code in `backend/app/`

---

## 📊 Phase 0 Completion Checklist

- [x] Step 1: Redis added to Docker Compose
- [x] Step 2: Environment configuration created
- [ ] Step 3: Backend project structure initialized
- [ ] Step 4: All services verified and tested
- [ ] Phase 0 Documentation complete

**Once Phase 0 is complete, proceed to Phase 1: Tool Registry & Metadata**
