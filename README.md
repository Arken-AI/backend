# MCP Chat Backend

Production-grade chat backend that replicates the Claude Desktop experience for the MCP Process Server.

**Architecture**: Clean Architecture with layered structure (API → Services → Core) for scalability, testability, and maintainability.

---

## 🎯 Project Status

**Current Phase**: Phase 1 - Tool Registry & Metadata ✅ COMPLETE  
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

### ✅ Step 3: Backend Project Structure (COMPLETE)

**What was done:**
- Created complete directory structure for FastAPI backend
- Created all `__init__.py` files with module docstrings
- Created `pyproject.toml` with all dependencies (modern Python standard)
- Created placeholder Python files with class/function stubs
- Created Docker configuration files

**Directory Structure Created:**
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point (working!)
│   ├── config.py            # Pydantic settings
│   ├── dependencies.py      # Dependency injection stubs
│   ├── api/
│   │   ├── __init__.py
│   │   ├── chat.py          # Chat endpoints (placeholder)
│   │   └── health.py        # Health check (placeholder)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── redis_client.py  # Redis client class structure
│   │   └── mongo_client.py  # MongoDB client class structure
│   ├── models/
│   │   └── __init__.py
│   ├── services/
│   │   └── __init__.py
│   ├── workers/
│   │   └── __init__.py
│   └── utils/
│       └── __init__.py
├── venv/                    # Virtual environment
├── pyproject.toml           # Modern Python config with dependencies
├── Dockerfile               # Docker build configuration
└── .dockerignore            # Docker ignore patterns
```

**Status:** Complete

**Verification:**
```bash
# Virtual environment created
ls -la venv/

# All dependencies installed
pip list

# FastAPI app running on http://localhost:8001
curl http://localhost:8001/
```

**API Running:**
- **URL**: http://localhost:8001
- **Docs**: http://localhost:8001/docs
- **Status**: Online ✅

---

### ⏳ Step 4: Skip Frontend (Backend-First Approach) (N/A)

Frontend initialization is skipped in Phase 0. Frontend development will begin in Phase 8 after backend is fully functional and tested.

---

## 🔧 Installation Process

### What We Built
1. **Created `pyproject.toml`** - Modern Python project configuration (replaces `requirements.txt`)
2. **Set up virtual environment** - `python3 -m venv venv`
3. **Installed dependencies** - `pip install -e .` (editable mode with all dependencies from pyproject.toml)
4. **Started FastAPI server** - `python -m app.main`

---

## 🔧 Quick Start

### Prerequisites
- Docker and Docker Compose installed
- Python 3.11+ installed (you have 3.13.5 ✅)
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

### Setup Backend Environment
```bash
# Navigate to backend directory
cd backend

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env and add your Anthropic API key
nano .env  # or use any editor
```

### Run FastAPI Backend
```bash
# Make sure you're in backend directory with venv activated
cd backend
source venv/bin/activate

# Start the server
PYTHONPATH=/Users/akashnikam/arken/calculation_engine/backend python -m app.main

# Or run in background
PYTHONPATH=/Users/akashnikam/arken/calculation_engine/backend python -m app.main &
```

### Access the Backend
- **API**: http://localhost:8001
- **API Docs (Swagger)**: http://localhost:8001/docs
- **ReDoc**: http://localhost:8001/redoc

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
- **Status**: ✅ Running with basic structure
- **URL**: http://localhost:8001
- **Docs**: http://localhost:8001/docs
- **Endpoints**: 
  - `GET /` - Root endpoint (working)
  - `GET /docs` - Swagger API documentation
  - More endpoints in Phase 7

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

# 2. Activate Python virtual environment
cd ../../backend
source venv/bin/activate

# 3. Start FastAPI server
PYTHONPATH=$(pwd) python -m app.main

# Server will be available at http://localhost:8001

# 4. Start RQ worker (Phase 5+)
# rq worker
```

### Development Commands
```bash
# Install new dependencies (add to pyproject.toml first)
pip install -e .

# Run with auto-reload (for development)
uvicorn app.main:app --reload --port 8001

# Check installed packages
pip list

# Update dependencies
pip install --upgrade -e .
```

### Stopping Services
```bash
# Stop FastAPI (if running in foreground)
# Press CTRL+C

# Stop FastAPI (if running in background)
pkill -f "python -m app.main"

# Stop Docker services
cd mcp_process_server/docker
docker-compose down

# Deactivate virtual environment
deactivate
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
- [x] Step 3: Backend project structure initialized
- [x] Step 4: Virtual environment created and dependencies installed
- [x] Step 5: FastAPI application running successfully

**Phase 0 Status: ✅ COMPLETE**

---

## 📋 Phase 1: Tool Registry & Metadata - Progress

### ✅ Step 1: Tool Metadata Schema (COMPLETE)

**What was done:**
- Created `backend/app/models/tool_metadata.py` with Pydantic model
- Defined complete schema with 9 fields: name, description, input_schema, output_schema, domain, category, prerequisites, risk_level, equipment_types
- Added validation patterns for domain, category, and risk_level
- Implemented helper methods: `is_safe()`, `is_gated()`, `has_prerequisites()`, `applies_to_equipment()`

**Testing:**
```bash
cd backend
source venv/bin/activate
python -c "from app.models import ToolMetadata; print('✅ Model imported')"
```

---

### ✅ Step 2: Tool Registry Service (COMPLETE)

**What was done:**
- Created `backend/app/services/tool_registry.py` with ToolRegistry class
- Implemented core methods: `register_tool()`, `get_tool()`, `get_all_tools()`
- Implemented filtering methods: `filter_by_domain()`, `filter_by_category()`, `filter_by_risk()`, `filter_by_equipment()`
- Added prerequisite chain resolution with circular dependency detection
- Added helper methods: `get_safe_tools()`, `get_gated_tools()`, `get_tools_with_prerequisites()`

**Testing:**
```bash
cd backend
source venv/bin/activate
python -c "from app.services import ToolRegistry; registry = ToolRegistry(); print('✅ Service imported')"
```

---

### ✅ Step 3: All 13 MCP Tools Populated (COMPLETE)

**What was done:**
- Added `load_default_tools()` method to ToolRegistry
- Registered all 13 MCP tools with complete metadata

**Tools Registered:**

**Discovery Tools (6)** - domain: "discovery", category: "discovery", risk: "safe"
1. `list_industries` - List all industries
2. `list_processes` - List processes for an industry
3. `get_process` - Get full process details with equipment sequence
4. `get_equipment_types` - List equipment types for an industry
5. `get_equipment_schema` - Get equipment parameter schema
6. `get_stream_schema` - Get stream property schema

**Validation Tools (3)** - domain: "sugar", category: "validate", risk: "safe"
7. `validate_process_inputs` - Validate process parameters before simulation
8. `validate_connections` - Validate process equipment connections
9. `validate_equipment_inputs` - Validate equipment parameters before simulation

**Simulation Tools (2)** - domain: "sugar", category: "simulate", risk: "gated"
10. `simulate_process` - Simulate complete process (prerequisite: validate_process_inputs)
11. `simulate_equipment` - Simulate standalone equipment (prerequisite: validate_equipment_inputs)

**Run Tools (2)** - domain: "generic", risk: "safe"
12. `get_run` - category: "lookup", retrieve past simulation run
13. `compare_runs` - category: "compare", compare two simulation runs

**Testing:**
```bash
cd backend
source venv/bin/activate

# Test all 13 tools loaded
python -c "
from app.services import ToolRegistry
registry = ToolRegistry()
registry.load_default_tools()
print(f'✅ Loaded {registry.count()} tools')
assert registry.count() == 13
"

# Test filtering by category
python -c "
from app.services import ToolRegistry
registry = ToolRegistry()
registry.load_default_tools()
print(f'Discovery: {len(registry.filter_by_category(\"discovery\"))} tools')
print(f'Validate: {len(registry.filter_by_category(\"validate\"))} tools')
print(f'Simulate: {len(registry.filter_by_category(\"simulate\"))} tools')
print(f'Lookup: {len(registry.filter_by_category(\"lookup\"))} tools')
print(f'Compare: {len(registry.filter_by_category(\"compare\"))} tools')
"

# Test risk filtering
python -c "
from app.services import ToolRegistry
registry = ToolRegistry()
registry.load_default_tools()
print(f'Safe tools: {len(registry.get_safe_tools())}')
print(f'Gated tools: {len(registry.get_gated_tools())}')
print(f'Gated: {[t.name for t in registry.get_gated_tools()]}')
"

# Test prerequisite chains
python -c "
from app.services import ToolRegistry
registry = ToolRegistry()
registry.load_default_tools()
chain = registry.get_prerequisite_chain('simulate_process')
print(f'simulate_process prerequisites: {chain}')
"
```

**Verification Results:**
- ✅ All 13 tools registered with complete metadata
- ✅ Domain filtering: 6 discovery, 5 sugar, 2 generic
- ✅ Category filtering: 6 discovery, 3 validate, 2 simulate, 1 lookup, 1 compare
- ✅ Risk filtering: 11 safe, 2 gated
- ✅ Prerequisites correctly defined for gated tools
- ✅ Prerequisite chain resolution working

---

### ⏭️ Step 4: Unit Tests (SKIPPED)

Unit tests skipped to proceed with Phase 2.

---

## 📊 Phase 1 Completion Summary

**Phase 1 Status: ✅ COMPLETE**

**Files Created:**
- ✅ `backend/app/models/tool_metadata.py` - Tool metadata Pydantic model (163 lines)
- ✅ `backend/app/services/tool_registry.py` - Tool registry service with all 13 tools (680+ lines)
- ✅ `backend/app/models/__init__.py` - Updated with ToolMetadata export
- ✅ `backend/app/services/__init__.py` - Updated with ToolRegistry export

**Capabilities Added:**
- ✅ Tool metadata schema with validation
- ✅ Centralized tool registry with 13 MCP tools
- ✅ Intelligent filtering by domain, category, risk, equipment
- ✅ Prerequisite chain resolution for workflow enforcement
- ✅ Foundation for policy-based tool access control

**Ready for Phase 2: Core Infrastructure (Redis, MongoDB, MCP, Claude clients)**

---

## 📊 Phase 0 Completion Checklist

- [x] Step 1: Redis added to Docker Compose
- [x] Step 2: Environment configuration created
- [x] Step 3: Backend project structure initialized
- [x] Step 4: Virtual environment created and dependencies installed
- [x] Step 5: FastAPI application running successfully

**Phase 0 Status: ✅ COMPLETE**

---

## 📊 Phase 1 Completion Checklist

- [x] Step 1: Tool Metadata Schema created (tool_metadata.py)
- [x] Step 2: Tool Registry Service created (tool_registry.py)
- [x] Step 3: All 13 MCP Tools populated with metadata
- [ ] Step 4: Unit Tests (Skipped)

**Phase 1 Status: ✅ COMPLETE**

**Next: Proceed to Phase 2: Core Infrastructure (Redis, MongoDB, MCP, Claude clients)**
