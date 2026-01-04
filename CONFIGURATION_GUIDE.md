# Configuration Management Guide

## Overview

The application uses **Pydantic Settings** for centralized, type-validated configuration management. All settings are loaded from environment variables and the `.env` file.

## Features

✅ **Type Validation** - All settings are type-checked at startup  
✅ **Environment Variables** - Load from `.env` file automatically  
✅ **API Key Cleaning** - Automatically cleans Google API keys (removes leading `=`)  
✅ **Helper Properties** - Convenient methods for checking configuration  
✅ **FastAPI Integration** - Easy dependency injection  
✅ **Smart Provider Selection** - Automatically chooses LLM provider based on availability

## Quick Start

### Import Settings

```python
from app.config import settings

# Access configuration
print(settings.mongodb_url)
print(settings.api_port)
print(settings.get_llm_provider())
```

### FastAPI Dependency

```python
from fastapi import Depends
from app.config import get_settings, Settings

@app.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    return {
        "status": "ok",
        "environment": settings.environment,
        "llm_provider": settings.get_llm_provider()
    }
```

## Configuration Sections

### MongoDB Configuration

```python
settings.mongodb_url         # Connection URL with auth
settings.mongodb_db_name     # Database name (default: arken_process_db)
```

### Redis Configuration

```python
settings.redis_host          # Redis hostname (default: localhost)
settings.redis_port          # Redis port (default: 6379)
settings.redis_db            # Redis database number (default: 0)
settings.redis_password      # Redis password (optional)
settings.redis_event_ttl     # Event TTL in seconds (default: 3600)
settings.redis_url           # Constructed Redis URL
```

### LLM Provider Configuration

```python
# API Keys
settings.anthropic_api_key   # Claude API key (optional)
settings.google_api_key      # Gemini API key (optional, auto-cleaned)

# Provider Selection
settings.default_llm_provider  # Default provider (claude or gemini)
settings.get_llm_provider()    # Get active provider based on availability

# Models
settings.llm_model_claude      # Claude model (default: claude-3-5-sonnet-20241022)
settings.llm_model_gemini      # Gemini model (default: gemini-2.0-flash)

# Parameters
settings.llm_max_tokens        # Max tokens (default: 8192)
settings.llm_temperature       # Temperature 0.0-2.0 (default: 1.0)
settings.llm_timeout           # Timeout in seconds (default: 300.0)
```

### MCP Server Configuration

```python
settings.mcp_server_command              # Python interpreter path
settings.mcp_server_args                 # MCP server.py path
settings.mcp_server_env_calc_engine_url  # Calculation engine URL
settings.mcp_server_env_max_stored_runs  # Max stored runs
settings.mcp_server_env_mongodb_uri      # MongoDB URI for MCP
```

### API Configuration

```python
settings.api_host            # API host (default: 0.0.0.0)
settings.api_port            # API port (default: 8001)
settings.api_debug           # Debug mode (default: False)
settings.cors_origins        # CORS origins (comma-separated string)
```

### Worker Configuration

```python
settings.rq_queue_name       # RQ queue name (default: default)
settings.rq_worker_count     # Number of workers (default: 1)
settings.rq_job_timeout      # Job timeout in seconds (default: 300)
```

## Helper Properties

### Environment Checks

```python
settings.is_production       # True if environment == "production"
settings.is_development      # True if environment == "development"
```

### LLM Provider Checks

```python
settings.has_claude          # True if Claude API key configured
settings.has_gemini          # True if Gemini API key configured
```

### Smart Provider Selection

```python
# Returns 'claude' or 'gemini' based on:
# 1. Default provider preference
# 2. API key availability
# 3. Fallback to any available provider
provider = settings.get_llm_provider()
```

## Validators

Settings are validated on startup:

- **cors_origins**: Accepts comma-separated string
- **default_llm_provider**: Must be 'claude' or 'gemini'
- **log_level**: Must be DEBUG, INFO, WARNING, ERROR, or CRITICAL
- **llm_temperature**: Must be between 0.0 and 2.0
- **google_api_key**: Automatically cleaned (removes leading `=`)

## Environment Variables

Create a `.env` file in the `backend/` directory:

```bash
# MongoDB
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=arken_process_db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_EVENT_TTL=3600

# LLM Providers
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIzaSy...
DEFAULT_LLM_PROVIDER=gemini

# LLM Models
LLM_MODEL_CLAUDE=claude-3-5-sonnet-20241022
LLM_MODEL_GEMINI=gemini-2.0-flash

# LLM Parameters
LLM_MAX_TOKENS=8192
LLM_TEMPERATURE=1.0
LLM_TIMEOUT=300.0

# MCP Server
MCP_SERVER_COMMAND=/path/to/venv/bin/python
MCP_SERVER_ARGS=/path/to/server.py
MCP_SERVER_ENV_CALC_ENGINE_URL=http://localhost:8000
MCP_SERVER_ENV_MAX_STORED_RUNS=1000
MCP_SERVER_ENV_MONGODB_URI=mongodb://localhost:27017

# API
API_HOST=0.0.0.0
API_PORT=8001
API_DEBUG=True
CORS_ORIGINS=http://localhost:5173,http://localhost:3000

# Workers
RQ_QUEUE_NAME=default
RQ_WORKER_COUNT=1
RQ_JOB_TIMEOUT=300

# Application
LOG_LEVEL=INFO
ENVIRONMENT=development
```

## Usage Examples

### Example 1: Get Active LLM Provider

```python
from app.config import settings

# Automatically selects best available provider
provider_name = settings.get_llm_provider()

if provider_name == "gemini":
    from app.core.llm_gemini_provider import GeminiProvider
    provider = GeminiProvider()
elif provider_name == "claude":
    from app.core.llm_claude_provider import ClaudeProvider
    provider = ClaudeProvider()
```

### Example 2: Connect to MongoDB

```python
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

client = AsyncIOMotorClient(settings.mongodb_url)
db = client[settings.mongodb_db_name]
```

### Example 3: Connect to Redis

```python
import redis
from app.config import settings

redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True
)
```

### Example 4: Configure CORS

```python
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Testing Configuration

Run configuration tests:

```bash
cd backend
python -m pytest test_config.py -v
```

Or test manually:

```python
from app.config import settings

print(f"Environment: {settings.environment}")
print(f"LLM Provider: {settings.get_llm_provider()}")
print(f"MongoDB: {settings.mongodb_db_name}")
print(f"Redis: {settings.redis_url}")
```

## Error Handling

### Missing Required Fields

If a required field is missing, Pydantic will raise a validation error on startup:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
mongodb_url
  Field required [type=missing, input_value={...}, input_type=dict]
```

**Solution**: Add the missing field to `.env` file

### Invalid Provider

If `default_llm_provider` is invalid:

```
ValueError: default_llm_provider must be 'claude' or 'gemini'
```

**Solution**: Set to 'claude' or 'gemini' in `.env`

### No LLM Provider Configured

If `get_llm_provider()` is called without any API keys:

```
ValueError: No LLM provider configured. Set ANTHROPIC_API_KEY or GOOGLE_API_KEY
```

**Solution**: Add at least one API key to `.env`

### Temperature Out of Range

If `llm_temperature` is invalid:

```
ValueError: llm_temperature must be between 0.0 and 2.0
```

**Solution**: Set temperature between 0.0 and 2.0 in `.env`

## Best Practices

1. **Use settings everywhere** - Don't use `os.getenv()` directly
2. **Check provider availability** - Use `has_claude` / `has_gemini` before using
3. **Use get_llm_provider()** - Let config choose the best provider
4. **Validate on startup** - Settings are validated when app starts
5. **Use FastAPI dependency** - Use `Depends(get_settings)` in endpoints
6. **Don't modify settings** - Treat settings as immutable
7. **Document env vars** - Keep `.env.example` updated

## Migration Notes

### Before (scattered configuration):

```python
import os
api_key = os.getenv("GOOGLE_API_KEY", "")
model = "gemini-2.0-flash"
temperature = 1.0
```

### After (centralized configuration):

```python
from app.config import settings
api_key = settings.google_api_key
model = settings.llm_model_gemini
temperature = settings.llm_temperature
```

## Next Steps

1. **Update existing code** - Replace `os.getenv()` with `settings`
2. **Update LLM providers** - Use `settings.anthropic_api_key` / `settings.google_api_key`
3. **Update MCP client** - Use `settings.mcp_server_*` fields
4. **Add FastAPI dependencies** - Use `Depends(get_settings)` in endpoints
5. **Test configuration** - Run `test_config.py` after changes

## Configuration File Location

`backend/app/config.py` - 308 lines with comprehensive configuration management

## Status

✅ **Phase 2 Step 5: Configuration Management - COMPLETE**

All configuration tests passing (8/8)
