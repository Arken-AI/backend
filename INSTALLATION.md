# MCP Chat Backend - Installation Guide

This guide walks you through setting up and running the MCP Chat Backend on your local machine.

---

## Prerequisites

- **Python**: 3.11 or higher
- **MongoDB**: Running on `localhost:27017` with authentication
- **Redis**: Running on `localhost:5540` 
- **MCP Process Server**: Installed and configured

---

## Installation Steps

### 1. Navigate to Backend Directory

```bash
cd /Users/akashnikam/workspace/backend
```

### 2. Create Python Virtual Environment

```bash
python3 -m venv venv
```

This creates a new virtual environment in the `venv` directory.

### 3. Install Dependencies

```bash
pip install -e .
```

This installs the backend package in editable mode along with all required dependencies:
- FastAPI & Uvicorn (API framework and server)
- Redis & Motor (database clients)
- Anthropic & Google GenAI (LLM providers)
- Pydantic (validation)
- RQ (task queue)

**Expected output**: All packages installed successfully.

### 4. Configure Environment Variables

The `.env` file should already exist in the backend directory. Verify it contains the required variables:

```bash
cat .env
```

**Required variables:**
- `MONGODB_URL` - MongoDB connection string
- `MONGODB_DB_NAME` - Database name (default: `arken_process_db`)
- `REDIS_HOST` - Redis host (default: `localhost`)
- `REDIS_PORT` - Redis port (default: `6379`)
- `ANTHROPIC_API_KEY` - Your Anthropic API key
- `GOOGLE_API_KEY` - Your Google API key
- `MCP_SERVER_COMMAND` - Path to Python executable for MCP server
- `MCP_SERVER_ARGS` - Path to MCP server script
- `MCP_SERVER_ENV_MONGODB_URI` - MongoDB URI for MCP server
- `MCP_SERVER_ENV_CALC_ENGINE_URL` - Calculation engine URL

**Note**: If `.env` doesn't exist, copy from `.env.example`:
```bash
cp .env.example .env
# Then edit .env with your actual values
```

### 5. Verify Dependencies are Running

**MongoDB:**
```bash
mongosh mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin
```

**Redis (optional):**
```bash
redis-cli ping
# Should return: PONG
```

### 6. Start the Backend Server

**Option A: Using the virtual environment directly**
```bash
/Users/akashnikam/workspace/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

**Option B: Using activated virtual environment**
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

**Expected output:**
```
INFO:     Will watch for changes in these directories: ['/Users/akashnikam/workspace/backend']
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
INFO:     Started reloader process [xxxxx] using WatchFiles
INFO:     Started server process [xxxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### 7. Verify Installation

**Test the API:**
```bash
curl http://localhost:8001/
```

**Expected response:**
```json
{
  "service": "MCP Chat Backend",
  "version": "0.1.0",
  "status": "online",
  "features": ["SSE Event Streaming"],
  "endpoints": {
    "stream": "/api/chat/{request_id}/stream",
    "docs": "/docs"
  }
}
```

**Test health endpoint:**
```bash
curl http://localhost:8001/api/health
```

---

## Available Endpoints

Once running, you can access:

- **Root**: `http://localhost:8001/`
- **API Documentation**: `http://localhost:8001/docs` (Swagger UI)
- **ReDoc**: `http://localhost:8001/redoc`
- **Health Check**: `http://localhost:8001/api/health`
- **Chat Stream**: `http://localhost:8001/api/chat/{request_id}/stream`

---

## Troubleshooting

### Port Already in Use

If you see `ERROR: [Errno 48] Address already in use`:

```bash
# Kill the process using port 8001
lsof -ti:8001 | xargs kill -9

# Then restart the server
/Users/akashnikam/workspace/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### MongoDB Connection Error

Verify MongoDB is running and credentials are correct:
```bash
docker ps | grep mongo
# OR
mongosh mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin
```

### Missing Environment Variables

If you see validation errors about missing fields:
```
ValidationError: 4 validation errors for Settings
  mongodb_url: Field required
  ...
```

Ensure your `.env` file exists and contains all required variables.

### Python Command Not Found

Use `python3` instead of `python`:
```bash
python3 -m venv venv
```

### Module Import Errors

Reinstall dependencies:
```bash
source venv/bin/activate
pip install -e .
```

---

## Development Mode

The server runs with `--reload` flag by default, which automatically restarts when code changes are detected. This is ideal for development.

**To run without auto-reload (production):**
```bash
/Users/akashnikam/workspace/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
```

---

## Next Steps

- Review the [README.md](README.md) for project overview and architecture
- Check [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) for development roadmap
- See [ENV_VARIABLES.md](ENV_VARIABLES.md) for detailed environment configuration
- Explore API documentation at `http://localhost:8001/docs`

---

