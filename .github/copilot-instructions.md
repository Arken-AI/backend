# ARKEN AI - Chat Backend: Copilot Instructions

## Project Overview

Production-grade chat backend that orchestrates AI-powered process simulation conversations. This is ONE component in a larger multi-service architecture (see workspace-level `../.github/copilot-instructions.md` for full system context).

**Role in System**:

- Receives chat messages from frontend (React UI - not yet implemented)
- Orchestrates agentic LLM loops with policy-governed tool execution
- Streams real-time events via SSE (Server-Sent Events)
- Calls MCP server (stdio transport) for process simulation tools
- Uses dual storage: Redis (fast/temporary) + MongoDB (persistent)

**This service**:

- ✅ Calls: MCP server (stdio), LLM APIs (Claude/Gemini), MongoDB, Redis
- ❌ Does NOT call: Calculation engine directly (goes through MCP server)
- ❌ Does NOT handle: Physics calculations (delegated to calc engine via MCP)

## Architecture Pattern: Policy-Governed Agentic Loop

### Two-Step Execution (Replicates Claude Desktop UX)

**Step A - Tool Execution Loop**:

```python
# LLM proposes tool calls from eligible set
# Backend enforces policy gates (prerequisites, ordering, budgets)
# MCP executes approved tools
# Loop continues until completion criteria met
```

**Step B - Report Generation**:

```python
# Tools disabled, LLM streams narrative
# Uses stored run data to generate user-facing report
# Streams via message.delta events
```

### Policy Gates (`app/services/policy_engine.py`)

Backend enforces correctness, LLM doesn't:

```python
# Example: Simulation requires validation first
if tool_name == "simulate_process":
    if not context.has_valid_validation():
        return PolicyDenial("validate_process_inputs required first")
```

**Critical principle**: Never trust LLM for sequencing. Policy layer is authoritative.

## Event-Driven SSE Streaming

### Event Types (`app/models/events.py`)

All events include `{request_id, seq, ts}` for correlation:

```python
# Agentic loop lifecycle
thinking.start / thinking.end

# Tool execution
tool.start / tool.end

# Simulation progress
run.status / run.progress / run.cancelling / run.cancelled

# Errors
app.error

# Note: LLM responses are returned via HTTP, not streamed as events
# message.delta/message.final events exist but are NOT currently used
```

### Event Flow Example

```
User: "Simulate sugar factory at 15000 kg/hr"

thinking.start
tool.start {validate_process_inputs}
tool.end {success}
tool.start {simulate_process}
run.status {running}
run.progress {mill: 20%}
run.progress {evaporator: 60%}
tool.end {success, calc_run_id}
thinking.end

# Final response returned via HTTP POST /api/chat response body
# (not streamed as SSE events)
```

## Development Workflows

### Running the Backend

```bash
# Standard (with access logs)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

# Production-like (no access logs - cleaner for debugging events)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 --no-access-log
```

### Testing

```bash
# All tests (uses fixtures for Redis/MongoDB mocks)
pytest tests/ -v

# Specific test files
pytest tests/test_policy_engine.py -v
pytest tests/test_context_manager.py -v
pytest tests/test_event_emitter.py -v
```

**Test patterns**:

- Fixtures in `conftest.py` provide mock clients
- `fakeredis` for Redis mocking
- `mongomock` for MongoDB mocking (async support)
- Tests verify event emission order and payloads

### Required Services

```bash
# MongoDB + Redis (from mcp_process_server/docker)
cd ../mcp_process_server/docker
docker-compose up -d

# Verify
docker ps | grep -E "arken-(mongo|redis)"
redis-cli ping  # Should return PONG
```

## Key Service Patterns

### Context Management (`app/services/context_manager.py`)

Tracks conversation state across messages:

```python
# Dual storage: Redis (1-hour TTL) + MongoDB (permanent)
await context_manager.create_context(conversation_id, user_id)
await context_manager.update_state(conversation_id, {"industry": "sugar"})
await context_manager.add_tool_execution(conversation_id, tool_name, result)

# Check validation status for policy gates
validation_status = await context_manager.get_validation_status(conversation_id)
```

### MCP Client (`app/core/mcp_client.py`)

**Critical**: MCP server uses stdio transport (stdin/stdout), NOT HTTP.

```python
# Start MCP server process
await mcp_client.connect()  # Spawns subprocess with stdio transport

# List tools (for LLM)
tools = await mcp_client.list_tools()

# Execute tool
result = await mcp_client.call_tool("simulate_process", args)

# Cleanup
await mcp_client.close()
```

**Common mistake**: Don't try to call MCP server via HTTP. It's invoked as a subprocess.

### Event Emitter (`app/services/event_emitter.py`)

Redis Streams for SSE event distribution:

```python
# Emit events (auto-increments seq number)
await emitter.emit_thinking_start(request_id)
await emitter.emit_tool_start(request_id, tool_name, args)
await emitter.emit_message_delta(request_id, text_chunk)
await emitter.emit_thinking_end(request_id)

# Events stored in: chat:{request_id}:events (1-hour TTL)
```

### LLM Provider Abstraction (`app/core/llm_provider.py`)

Dual provider support (Claude + Gemini):

```python
# Auto-selects based on available API keys
provider = get_llm_provider()  # Returns AnthropicProvider or GeminiProvider

# Unified interface
response = await provider.send_message(
    messages=conversation_history,
    tools=eligible_tools,  # Filtered by policy layer
    stream=True
)

# Handle tool calls
if response.has_tool_use():
    tool_name, tool_args = response.get_tool_call()
```

## Configuration (`app/config.py`)

Environment variables (see `ENV_VARIABLES.md` for details):

```bash
# LLM Provider (one required)
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...

# MongoDB
MONGODB_URL=mongodb://arken_app:password@localhost:27017/arken_process_db?authSource=admin
MONGODB_DB_NAME=arken_process_db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_EVENT_TTL=3600  # 1 hour

# MCP Server
MCP_SERVER_COMMAND=uv run python server.py
MCP_SERVER_CWD=/path/to/mcp_process_server

# API
API_HOST=0.0.0.0
API_PORT=8001
CORS_ORIGINS=["http://localhost:5173"]  # Frontend origin
```

## Data Storage Strategy

### Redis Keys (1-hour TTL)

```python
context:{conversation_id}              # Hash - conversation state
chat:{request_id}:events               # Stream - SSE events
job:{request_id}:status                # String - job status
session:{session_id}:last_run_id       # String - quick reference
```

### MongoDB Collections

```python
conversations  # Full conversation history
runs          # Simulation runs with calc_run_id
```

## API Endpoints

| Method | Endpoint                        | Purpose                                          |
| ------ | ------------------------------- | ------------------------------------------------ |
| POST   | `/api/chat`                     | Submit message, returns `request_id`, queues job |
| GET    | `/api/chat/{request_id}/stream` | SSE endpoint for real-time events                |
| POST   | `/api/chat/{request_id}/cancel` | Cancel running simulation                        |
| GET    | `/api/health`                   | Health check (Redis + MongoDB + MCP)             |

## Common Pitfalls

1. **Don't call MCP server via HTTP** - It uses stdio transport, started as subprocess
2. **Don't trust LLM for prerequisites** - Policy gates enforce validation → simulation order
3. **Don't forget event correlation** - All events need `request_id`, `seq`, `ts`
4. **Don't use sync Redis in async code** - Use `aioredis` or run in executor
5. **MongoDB auth required** - Connection string must include `authSource=admin`
6. **Event TTL matters** - Redis Streams expire after 1 hour, MongoDB is permanent backup

## Extending the Backend

**Adding new policy gates**:

1. Add rule to `app/services/policy_engine.py`
2. Reference context state (validation status, tool history)
3. Return `PolicyDenial` with clear error message

**Adding new endpoints**:

1. Create router in `app/api/`
2. Add business logic in `app/services/`
3. Register in `app/main.py`
4. Add tests in `tests/`

**Adding new event types**:

1. Define schema in `app/models/events.py`
2. Add emit method in `app/services/event_emitter.py`
3. Update SSE stream handler
4. Document in `architecture-mcpChatInterface.md`

## Key Files to Reference

- `architecture-mcpChatInterface.md` - Complete architecture (850+ lines)
- `DEVELOPMENT_PLAN.md` - Phase tracking, task breakdown
- `ENV_VARIABLES.md` - Configuration documentation
- `CONFIGURATION_GUIDE.md` - Setup instructions
- `KEY_NAMING_CONVENTIONS.md` - Naming standards

## Current Status

**Phase 7 Complete**: All backend endpoints implemented
**Next**: Phase 8 - Frontend development

**Completed Features**:

- ✅ Tool registry with 13 MCP tools
- ✅ Policy engine with prerequisite enforcement
- ✅ Context manager (Redis + MongoDB dual storage)
- ✅ Event emitter (SSE via Redis Streams)
- ✅ Agentic loop with auto-recovery
- ✅ LLM provider abstraction (Claude + Gemini)
- ✅ MCP client with stdio transport

---

**Version**: Backend-specific instructions | **Last Updated**: 16 January 2026
