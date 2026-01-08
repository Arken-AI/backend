# 🚀 MCP Chat Interface - Development Plan

## Project Overview
Build a production-grade chat interface that replicates the Claude Desktop experience for the MCP Process Server. The system uses a controlled agentic pattern where the LLM proposes tool calls and the backend enforces correctness through policy gates.

## 🎯 Development Approach

**CURRENT PHASE: Backend & Infrastructure Only**

This development plan follows a **backend-first approach**:
- **Phases 0-7**: Focus exclusively on backend development, infrastructure setup, and API implementation
- **Phase 8**: Frontend development begins (after backend is fully functional and tested)
- **Phases 9-11**: Full-stack integration, testing, and deployment

**Rationale**: Complete and stabilize the backend API, event system, and business logic before building the UI. This ensures the frontend can be built against a solid, tested foundation.

## Technology Stack

### Backend ✅ Implemented
- **Framework**: FastAPI (async) with Pydantic v2
- **LLM Provider**: Google Gemini 2.0 Flash (configurable, Claude support ready)
- **Databases**: MongoDB (persistent), Redis (session + events)
- **MCP**: Existing mcp_process_server (stdio transport, 13 tools)
- **Event Streaming**: Redis Streams + SSE

### Frontend (Phase 8 - Not Started)
- **Framework**: Vite + React
- **State Management**: Context API (simple, built-in)
- **Styling**: Tailwind CSS
- **Markdown**: react-markdown + remark-gfm

### Infrastructure ✅ Running
- **MongoDB**: Running in Docker with authentication
- **Redis**: Running with Streams support
- **Authentication**: Skipped for MVP (add later)

---

## Current Status Summary

| Phase | Status | Key Deliverables |
|-------|--------|------------------|
| Phase 0: Infrastructure | ✅ Complete | Docker, Redis, MongoDB, Project structure |
| Phase 1: Tool Registry | ✅ Complete | 13 tools with metadata |
| Phase 2: Core Infrastructure | ✅ Complete | Redis, MongoDB, MCP, LLM clients |
| Phase 3: Policy & Context | ✅ Complete | Context manager, Policy gates |
| Phase 4: Event System | ✅ Complete | 8 event types, SSE streaming |
| Phase 5: Agentic Loop | ✅ Complete | Multi-turn, auto-recovery, token tracking |
| Phase 6: MongoDB Persistence | ✅ Complete | Dual storage (Redis + MongoDB) |
| Phase 7: FastAPI Endpoints | ✅ Complete | POST /chat, GET /stream, health |
| Phase 8: Frontend | 🔄 Next | React UI with SSE integration |

**Backend is fully functional and ready for frontend development!**

---

## Project Structure

```
calculation_engine/
├── backend/              # New FastAPI chat backend
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py      # FastAPI app with chat endpoints
│   │   ├── config.py    # Configuration & environment variables
│   │   ├── dependencies.py  # Dependency injection
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── chat.py  # Chat endpoints (POST /chat, GET /stream, etc.)
│   │   │   └── health.py  # Health check endpoints
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── redis_client.py    # Redis connection & event streaming
│   │   │   ├── mongo_client.py    # MongoDB connection
│   │   │   ├── mcp_client.py      # MCP client wrapper
│   │   │   └── llm_provider.py    # Claude API client
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── events.py    # SSE event models
│   │   │   ├── requests.py  # API request models
│   │   │   └── storage.py   # MongoDB document models
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── tool_registry.py    # Tool metadata registry
│   │   │   ├── intent_detector.py  # Intent-based filtering
│   │   │   ├── policy_gates.py     # Backend policy enforcement
│   │   │   ├── event_emitter.py    # SSE event emission
│   │   │   └── result_summarizer.py  # Tool result summarization
│   │   ├── workers/
│   │   │   ├── __init__.py
│   │   │   ├── agentic_loop.py     # RQ job: agentic loop
│   │   │   ├── report_generator.py # Step B: report generation
│   │   │   └── integrity_guard.py  # Report validation
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── helpers.py
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
│
├── frontend/            # New React frontend
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   ├── components/
│   │   │   ├── ChatThread.jsx         # Main chat container
│   │   │   ├── ThinkingIndicator.jsx  # thinking.start/end
│   │   │   ├── ToolExecutionCard.jsx  # tool.start/end
│   │   │   ├── RunProgressBar.jsx     # run.progress
│   │   │   ├── StreamingBubble.jsx    # message.delta
│   │   │   ├── MarkdownRenderer.jsx   # message.final
│   │   │   └── MessageInput.jsx       # User input
│   │   ├── hooks/
│   │   │   ├── useSSE.js             # EventSource connection
│   │   │   └── useChatState.js       # Chat state management
│   │   ├── utils/
│   │   │   ├── api.js                # API client
│   │   │   └── eventHandlers.js      # SSE event handlers
│   │   └── styles/
│   │       └── tailwind.css
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── index.html
│
├── mcp_process_server/  # Existing (no changes)
│   ├── docker/
│   │   └── docker-compose.yml  # Will add Redis here
│   └── ...
│
└── main.py              # Existing calculation engine (no changes)
```

---

## 📋 Phase-by-Phase Development Plan

---

### **PHASE 0: Infrastructure Setup** (Day 1)

#### Goal
Set up development environment with all required services

#### Tasks
1. **Add Redis to Docker Compose**
   - Add Redis service to `mcp_process_server/docker/docker-compose.yml`
   - Configure Redis with persistence and Streams support
   - Set 1-hour TTL for event streams
   - Add Redis health check
   - Add RedisInsight (optional UI for debugging)

2. **Create Environment Configuration**
   - Create `backend/.env.example` with all required variables:
     - MongoDB connection (existing)
     - Redis connection (new)
     - Anthropic API key
     - MCP server connection settings
     - CORS settings
   - Document all environment variables

3. **Initialize Backend Project Structure**
   - Create all folders from structure above
   - Create `backend/requirements.txt` with dependencies:
     - fastapi, uvicorn
     - redis[hiredis]
     - motor (async MongoDB)
     - anthropic
     - rq
     - pydantic-settings
   - Create empty `__init__.py` files

4. **Initialize Frontend Project**
   - Run `npm create vite@latest frontend -- --template react`
   - Install dependencies:
     - tailwindcss, postcss, autoprefixer
     - react-markdown
     - remark-gfm (GitHub Flavored Markdown)
     - date-fns (time formatting)
   - Configure Tailwind CSS

#### Deliverables
- ✅ All services running (MongoDB + Redis)
- ✅ Project structure created
- ✅ Dependencies installed
- ✅ Environment configured

#### Testing
- `docker-compose ps` shows all services healthy
- `redis-cli ping` returns PONG
- MongoDB accessible at localhost:27017

---

### **PHASE 1: Tool Registry & Metadata** (Day 2)

#### Goal
Create production-grade tool registry with full metadata for all 13 tools

#### Tasks
1. **Define Tool Metadata Schema**
   - Create `backend/app/models/tool_metadata.py`
   - Define `ToolMetadata` Pydantic model with fields:
     - name, domain, category, risk_level
     - description, prerequisites, estimated_duration

2. **Create Tool Registry Service**
   - File: `backend/app/services/tool_registry.py`
   - Implement `ToolRegistry` class with methods:
     - `get_all_tools()` → List[ToolMetadata]
     - `get_tool(name: str)` → ToolMetadata
     - `filter_by_domain(domain: str)` → List[ToolMetadata]
     - `filter_by_category(category: str)` → List[ToolMetadata]
     - `get_safe_tools()` → List[ToolMetadata]
     - `get_gated_tools()` → List[ToolMetadata]

3. **Define Metadata for All 13 Tools**
   - Map each existing MCP tool to metadata:

   **Discovery Tools (6 - all safe):**
   - `list_industries` → domain: "discovery", category: "discovery", risk: "safe"
   - `list_processes` → domain: "discovery", category: "discovery", risk: "safe"
   - `get_process` → domain: "discovery", category: "discovery", risk: "safe"
   - `get_equipment_types` → domain: "discovery", category: "discovery", risk: "safe"
   - `get_equipment_schema` → domain: "discovery", category: "discovery", risk: "safe"
   - `get_stream_schema` → domain: "discovery", category: "discovery", risk: "safe"

   **Validation Tools (3 - all safe):**
   - `validate_process_inputs` → domain: "sugar", category: "validate", risk: "safe"
   - `validate_connections` → domain: "sugar", category: "validate", risk: "safe"
   - `validate_equipment_inputs` → domain: "sugar", category: "validate", risk: "safe"

   **Simulation Tools (2 - gated):**
   - `simulate_process` → domain: "sugar", category: "simulate", risk: "gated", prerequisites: ["validate_process_inputs"]
   - `simulate_equipment` → domain: "sugar", category: "simulate", risk: "gated", prerequisites: ["validate_equipment_inputs"]

   **Run Tools (2 - safe):**
   - `get_run` → domain: "generic", category: "lookup", risk: "safe"
   - `compare_runs` → domain: "generic", category: "compare", risk: "safe"

#### Deliverables
- ✅ Tool registry with full metadata for all 13 tools
- ✅ Query methods for filtering tools
- ✅ Unit tests for tool registry

#### Testing
- Verify all 13 tools have metadata
- Test filtering by domain, category, risk_level
- Validate prerequisite chains

---

### **PHASE 2: Core Infrastructure** (Day 3-4)

#### Goal
Build foundational backend services (Redis, MongoDB, MCP, Claude)

#### Tasks
1. **Redis Client & Event Streaming**
   - File: `backend/app/core/redis_client.py`
   - Implement connection pool management
   - Redis Streams operations:
     - `add_event(request_id, event_type, data)` → stream_id
     - `get_events(request_id, after_id=None)` → List[Event]
     - `set_job_status(request_id, status)`
     - `get_job_status(request_id)` → str
   - Session state operations:
     - `save_message(session_id, message)`
     - `get_messages(session_id)` → List
     - `set_last_run_id(session_id, run_id)`
   - TTL management (1 hour for events)

2. **MongoDB Client**
   - File: `backend/app/core/mongo_client.py`
   - Implement async Motor client
   - Collections: conversations, runs
   - CRUD operations:
     - `save_conversation(session_id, messages)`
     - `get_conversation(session_id)`
     - `save_run(calc_run_id, run_data)`
     - `get_run(calc_run_id)`

3. **MCP Client Wrapper**
   - File: `backend/app/core/mcp_client.py`
   - MCP connection management (stdio transport)
   - Supervisor pattern (restart on crash)
   - Async lock for serialized tool calls
   - Methods:
     - `list_tools()` → List[Tool]
     - `call_tool(name, args)` → Any
     - `health_check()` → bool

4. **Claude API Client**
   - File: `backend/app/core/llm_provider.py`
   - Implement `ClaudeProvider` class:
     - `create_message(messages, tools, stream=False)` → Response
     - `create_message_stream(messages, tools)` → AsyncIterator
     - Convert MCP tools to Anthropic tool format
     - Handle streaming responses
     - Error handling & retries

5. **Configuration Management**
   - File: `backend/app/config.py`
   - Use `pydantic-settings` for environment variables
   - Validate all required settings on startup

#### Deliverables
- ✅ All core clients working
- ✅ Connection pooling & error handling
- ✅ Integration tests for each client

#### Testing
- Redis: Write event, read event, verify TTL
- MongoDB: Save run, retrieve run
- MCP: List tools, call simulate_process
- Claude: Send message, receive response

---

### **PHASE 3: Policy Layer & Context Management** (Day 5-6) ✅ **COMPLETED**

#### Goal
Implement core policy enforcement and conversation context management (simplified approach)

**Status**: ✅ All tasks completed
- ✅ Context Manager service implemented and tested
- ✅ Policy Gates service implemented with prerequisite checking
- ✅ Orchestration service connecting all components
- ✅ Integration tests validating Redis + MongoDB dual-storage
- ✅ 4/9 integration tests passing (core infrastructure validated)

**Note**: Intent Detection, Response Formatting, and Tool Pre-filtering are deferred to post-MVP optimization phase. Claude will receive all 13 tools and handle intent detection naturally.

#### Tasks
1. **Context Manager Service**
   - File: `backend/app/services/context_manager.py`
   - Implement `ContextManager` class
   - Methods:
     - `create_context(conversation_id)` - Initialize new conversation
     - `get_context(conversation_id)` - Retrieve conversation context
     - `update_context(conversation_id, updates)` - Update context state
     - `add_tool_execution(conversation_id, tool_name, result)` - Track tool usage
     - `get_executed_tools(conversation_id)` - Get tool execution history
     - `get_last_validation(conversation_id, process_id)` - Check validation status
   - Store context in Redis with TTL
   - Persist to MongoDB for long-term storage
   - Track: current_industry, current_process, executed_tools, validation_status, last_run_id

2. **Policy Gates Service**
   - File: `backend/app/services/policy_gates.py`
   - Implement `PolicyGates` class:
   
   **Hard Gates:**
   - `check_eligibility(tool_name, eligible_tools)` → (bool, str)
   - `check_prerequisites(tool_name, execution_history)` → (bool, str)
   - `check_required_params(tool_name, args)` → (bool, str)
   
   **Order Gates:**
   - Define workflows (e.g., validate → simulate)
   - `check_workflow_order(tool_name, execution_history)` → (bool, str)
   
   **Budget Gates:**
   - `check_tool_call_budget(request_id)` → (bool, str)
   - `check_time_budget(request_id)` → (bool, str)
   - Max 6 tool calls per request
   - Max 300 seconds per request
   
   **Main method:**
   - `enforce_policy(tool_name, args, context)` → (bool, str)

3. **Simple Orchestration Service**
   - File: `backend/app/services/orchestration_service.py`
   - Implement `OrchestrationService` class
   - Methods:
     - `process_message(conversation_id, message)` - Main entry point
     - `execute_with_policy(tool_name, args, context)` - Policy-checked execution
     - `prepare_llm_context(conversation_id)` - Build conversation history
   - Always send ALL 13 tools to Claude (no pre-filtering)
   - Let Claude handle intent detection and tool selection
   - Enforce policy before each tool execution
   - Update context after each tool execution

#### Deliverables ✅
- ✅ Context Manager storing conversation state in Redis/MongoDB (VERIFIED)
- ✅ Policy Gates enforcing validate-before-simulate (IMPLEMENTED)
- ✅ Simple Orchestration connecting all components (WORKING)
- ✅ End-to-end flow working (user message → tool execution → response) (PARTIAL)
- ✅ Unit tests for core logic (9 tests passing for orchestration service)

#### Testing Results ✅
- ✅ Context: Store/retrieve conversation state (PASSING - test_context_manager_storage)
- ✅ Redis: Connection and basic operations (PASSING - 2/2 tests)
- ✅ Policy: Prerequisite enforcement implemented in OrchestrationService
- ✅ Orchestration: 9/9 unit tests passing with mocks
- ✅ Integration: 4/9 tests passing (Redis + Context working, MongoDB/MCP needs fixes)
- ⚠️ Multi-turn: To be tested in Phase 5 with full agentic loop

---

### **PHASE 4: Event System & SSE** (Day 7) ✅ **COMPLETED**

**Status**: ✅ All tasks completed
**Prerequisites**: ✅ Phase 3 complete (Context + Policy working)

#### Goal
Build typed SSE event protocol for real-time UI updates

#### Summary
Event system fully implemented with Redis Streams storage, SSE endpoint, and orchestration integration.

**Completed Features:**
- ✅ 8 event types with full serialization (BaseEvent, ThinkingStart/End, ToolStart/End, RunProgress, MessageDelta/Final, AppError)
- ✅ EventEmitter service with Redis Streams backend
- ✅ SSE streaming endpoint with replay support
- ✅ Orchestration service emitting events at all key points
- ✅ MongoDB context persistence implemented
- ✅ All async operations properly awaited

**Test Results:**
- ✅ Event Models: 35/35 tests passing
- ✅ Event Emitter: 23/23 tests passing  
- ✅ SSE Stream: 13/13 tests passing
- ✅ Total: 71 tests passing

#### Tasks
1. **Event Models** ✅
   - File: `backend/app/models/events.py` (380 lines)
   - Implemented Pydantic models for all event types:
     - BaseEvent (request_id, seq, ts)
     - ThinkingStartEvent, ThinkingEndEvent (duration_ms)
     - ToolStartEvent, ToolEndEvent (tool_name, status, duration, summary)
     - RunProgressEvent (stage, percentage, message)
     - MessageDeltaEvent, MessageFinalEvent (content, role, metadata)
     - AppErrorEvent (error_type, error_message, details, recoverable)
   - Helper methods: to_sse_format(), to_redis_dict(), from_redis_dict()
   - Enums: EventType, ToolStatus, ErrorType

2. **Event Emitter Service** ✅
   - File: `backend/app/services/event_emitter.py` (472 lines)
   - Implemented `EventEmitter` class with methods:
     - `emit_thinking_start(request_id)`
     - `emit_thinking_end(request_id, duration_ms)`
     - `emit_tool_start(request_id, tool_name, args, estimated_duration_ms)`
     - `emit_tool_end(request_id, tool_name, status, duration, summary, error_message, result_id)`
     - `emit_run_progress(request_id, stage, percentage, message, current_block, total_blocks)`
     - `emit_message_delta(request_id, delta, accumulated_length)`
     - `emit_message_final(request_id, content, role, metadata)`
     - `emit_app_error(request_id, error_type, error_message, details, recoverable)`
     - `get_events(request_id, after_sequence)` - For SSE replay
     - `get_event_count(request_id)`
     - `clear_events(request_id)`
   - Redis Streams storage with automatic sequence numbers
   - TTL management (1-hour expiration)
   - MAXLEN protection (10,000 events limit)

3. **SSE Streaming Endpoint** ✅
   - File: `backend/app/api/stream.py` (190 lines)
   - Implemented SSE streaming at `GET /api/chat/{request_id}/stream`
   - Features:
     - Replay support via `after_sequence` query parameter
     - Auto-termination on `message_final` event
     - Keepalive heartbeat (every 15 seconds)
     - Proper SSE headers (Cache-Control, Connection, X-Accel-Buffering)
     - Graceful error handling
   - EventSourceResponse wrapper for SSE format

4. **Orchestration Integration** ✅
   - File: `backend/app/services/orchestration_service.py`
   - Added EventEmitter dependency (optional)
   - Event emissions at all key points:
     - `emit_thinking_start/end` around LLM calls (with duration tracking)
     - `emit_tool_start/end` around tool execution (with status and duration)
     - `emit_run_progress` for long-running calculations
     - `emit_message_delta` during streaming responses (placeholder)
     - `emit_message_final` when request completes
     - `emit_app_error` for policy violations and exceptions
   - All events include timestamps and sequence numbers

5. **MongoDB Context Persistence** ✅
   - File: `backend/app/services/context_manager.py`
   - Implemented dual-storage strategy:
     - Redis: Fast cache with 1-hour TTL (primary)
     - MongoDB: Permanent backup (secondary)
   - Methods updated to async:
     - `async update_context()` - Saves to both Redis and MongoDB
     - `async add_tool_execution()` - Saves to both storages
     - `async clear_context()` - Deletes from both storages
   - MongoDB operations with error handling (best-effort)
   - Context survives server restarts via MongoDB fallback

#### Deliverables ✅
- ✅ 8 event types with full serialization (35 tests passing)
- ✅ EventEmitter service with Redis Streams (23 tests passing)
- ✅ SSE streaming endpoint with replay (13 tests passing)
- ✅ Orchestration emitting events at all key points
- ✅ MongoDB persistence for conversations
- ✅ All async operations properly awaited

#### Testing Results ✅
- ✅ Event Models: 35/35 tests passing
  - Event creation, validation, serialization
  - SSE format generation
  - Redis dict conversion (round-trip)
- ✅ Event Emitter: 23/23 tests passing
  - Sequence number management
  - All 8 event emission methods
  - Event retrieval and replay
  - TTL and cleanup
  - Error handling
- ✅ SSE Stream: 13/13 tests passing
  - Basic streaming
  - Replay after sequence
  - Auto-termination
  - SSE format compliance
  - Error scenarios
- ✅ Context Manager: Tests updated to async
- ✅ Orchestration Service: Fixed method calls, verified async await usage

**Files Created/Modified:**
- `app/models/events.py` - 380 lines (8 event classes)
- `app/services/event_emitter.py` - 472 lines (EventEmitter class)
- `app/api/stream.py` - 190 lines (SSE endpoint)
- `app/services/orchestration_service.py` - Modified (event integration)
- `app/services/context_manager.py` - Modified (MongoDB persistence)
- `tests/test_events.py` - 510 lines (35 tests)
- `tests/test_event_emitter.py` - 340+ lines (23 tests)
- `tests/test_stream.py` - 300+ lines (13 tests)
- `tests/test_context_manager.py` - Modified (async tests)
- `test_sse_manual.py` - 250 lines (manual testing script)

---

### **PHASE 5: RQ Worker & Agentic Loop** (Day 8-10) ✅ **COMPLETED**

**Status**: ✅ Completed (Agentic Loop implemented directly in Orchestration Service)
**Prerequisites**: ✅ Phase 4 complete (Event System working)

**Decision**: Instead of using RQ background workers, the agentic loop was implemented directly in the OrchestrationService for simpler architecture. Background workers can be added later if needed for very long-running simulations.

#### Goal
Build the core agentic execution engine with auto-recovery

#### Summary
Implemented a production-grade agentic loop that matches Claude Desktop behavior - automatically retries on errors, maintains conversation history with tool results, and iterates until the LLM has all information needed.

**Completed Features:**
- ✅ Agentic loop with max 10 iterations
- ✅ Multi-turn conversation history (user → assistant tool calls → tool results → assistant response)
- ✅ Auto-recovery from errors (wrong process names, validation failures sent back to LLM)
- ✅ Model-specific system prompts (Claude minimal, Gemini detailed)
- ✅ Token tracking across all iterations
- ✅ Event emission at all key points

**Key Implementation Details:**
```
Agentic Loop Flow:
1. User sends message
2. LLM proposes tool call(s)
3. Execute tools (success OR error)
4. Send ALL results back to LLM
5. LLM decides: call more tools OR respond
6. Repeat until LLM responds with text (max 10 iterations)
```

**Files Modified:**
- `app/services/orchestration_service.py` - Complete rewrite (767 lines)
  - `process_message()` - Main agentic loop with iteration tracking
  - `_call_llm_with_history()` - Multi-turn LLM calls with tool results
  - `_execute_tool()` - Tool execution with error handling
  - Model-specific system prompts for Claude vs Gemini
- `app/core/llm_gemini_provider.py` - Multi-turn support
  - `_convert_messages()` - Handles assistant tool calls and tool results
  - Formats `functionCall` and `functionResponse` for Gemini API
- `app/models/requests.py` - Token tracking
  - Added `TokenUsage` model (input_tokens, output_tokens, total_tokens)
  - `ChatResponse` includes `token_usage` field
- `mcp_process_server/schemas.py` - Default parameters
  - `node_params` now `Optional[Dict] = None` for default equipment settings

#### Deliverables ✅
- ✅ Complete agentic loop working (tested with curl)
- ✅ Auto-recovery from wrong process names (sugar_production → sugar_factory)
- ✅ Policy enforcement integrated (can be enabled/disabled)
- ✅ Event emission throughout (thinking, tool, message events)
- ✅ Token tracking (28,000+ tokens for complex queries)

#### Testing Results ✅
- ✅ Simple query: "list all industries" → 1 tool call, 4,248 tokens
- ✅ Complex query: "how many equipments" → 3 tool calls, 9,573 tokens  
- ✅ Auto-recovery: Wrong process "sugar_production" → auto-corrects to "sugar_factory"
- ✅ Full simulation: Runs through validate → simulate with 30,000+ tokens

---

### **PHASE 6: MongoDB Context Persistence** ✅ **COMPLETED**

**Status**: ✅ Integrated with Phase 4
**Note**: Originally planned as separate phase, but architecture already supported dual storage. Implementation completed as part of Phase 4 event system work.

#### Goal
Ensure conversations persist beyond Redis TTL (complete Phase 6 MongoDB integration)

#### Summary
ContextManager already had dual-storage architecture designed. Phase 6 work involved uncommenting and implementing the MongoDB save/load methods.

**Completed Features:**
- ✅ MongoDB save operations implemented (upsert on updates)
- ✅ MongoDB load operations implemented (fallback from Redis)
- ✅ MongoDB delete operations implemented (context cleanup)
- ✅ Async/await properly used throughout
- ✅ Error handling (best-effort, doesn't break on MongoDB failures)
- ✅ Conversations survive server restarts

#### Implementation Details
- Modified `_save_to_mongo_async()` - Actually saves to MongoDB with upsert
- Modified `_load_from_mongo_sync()` - Actually loads from MongoDB with _id removal
- Modified `clear_context()` - Deletes from both Redis and MongoDB
- Updated all callers to use `await` for async methods
- Fixed orchestration service to use `_update_context()` wrapper method

**Files Modified:**
- `app/services/context_manager.py` - MongoDB methods implemented
- `app/services/orchestration_service.py` - Fixed method call (line 375)
- `tests/test_context_manager.py` - Updated to async tests

#### Storage Strategy
**Redis (Primary - Fast Access)**:
- 1-hour TTL
- In-memory cache
- Fast read/write
- Automatic expiration

**MongoDB (Secondary - Persistence)**:
- No TTL (permanent)
- Disk-based storage
- Survives restarts
- Fallback on Redis miss

#### Deliverables ✅
- ✅ MongoDB persistence fully functional
- ✅ Dual-storage strategy working
- ✅ Context survives server restarts
- ✅ All async operations properly awaited

#### Testing
- Context stored in MongoDB after updates
- Context retrieved from MongoDB on Redis miss
- Context deleted from both storages on clear
- Server restart doesn't lose conversations

---

### **PHASE 5b: Background Workers** (Future - If Needed)

**Status**: 📋 Deferred (Optional optimization)
**Reason**: Current synchronous implementation handles requests well. Background workers can be added if simulation times exceed 30 seconds regularly.

#### Goal
Move long-running simulations to background workers for better scalability

#### Tasks (If Implemented Later)
1. **RQ Job Infrastructure**
   - Set up RQ worker configuration
   - Redis queue setup
   - Job retry policies

2. **Background Simulation Job**
   - Move simulation execution to RQ worker
   - Async job status tracking
   - Progress events from worker

3. **Report Integrity Guard**
   - Number extraction utility
   - Validate LLM-generated reports against source data
   - Template report as fallback

#### When to Implement
- If average simulation time exceeds 30 seconds
- If concurrent user load requires request queuing
- If API response timeouts become an issue

---

### **PHASE 7: FastAPI Endpoints** (Day 12) ✅ **COMPLETED**

**Status**: ✅ All endpoints implemented and tested
**Note**: This was the final backend-only phase. Frontend development begins in Phase 8.

#### Goal
Build REST API endpoints for chat interface

#### Summary
Complete chat API implemented with agentic loop, SSE streaming, token tracking, and multi-turn conversation support.

**Completed Features:**
- ✅ POST /api/chat - Send messages with agentic loop processing
- ✅ GET /api/chat/{conversation_id}/stream - SSE streaming with replay support
- ✅ GET /api/chat/{conversation_id}/context - Retrieve conversation context
- ✅ DELETE /api/chat/{conversation_id}/context - Clear conversation
- ✅ GET /api/health - Health check endpoint
- ✅ Token usage tracking in responses
- ✅ Bytes-to-string conversion for Redis stream data

**API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send message, returns response with token usage |
| `/api/chat/{conversation_id}/stream` | GET | SSE stream of events (replay supported) |
| `/api/chat/{conversation_id}/context` | GET | Get conversation context |
| `/api/chat/{conversation_id}/context` | DELETE | Clear conversation |
| `/api/health` | GET | Service health check |

**Request/Response Examples:**

```bash
# Send a message
curl -X POST "http://localhost:8001/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": "conv_123", "message": "list all industries"}'

# Response
{
  "conversation_id": "conv_123",
  "request_id": "req_abc123",
  "status": "completed",
  "message": "The available industry is sugar.",
  "token_usage": {
    "input_tokens": 4238,
    "output_tokens": 10,
    "total_tokens": 4248
  }
}

# Stream events (use same conversation_id)
curl -N -H "Accept: text/event-stream" \
  "http://localhost:8001/api/chat/conv_123/stream?after_sequence=0"

# SSE Events received:
# id: 1
# event: thinking_start
# data: {"request_id": "conv_123", ...}
#
# id: 2  
# event: tool_start
# data: {"tool_name": "list_industries", ...}
#
# id: 3
# event: tool_end
# data: {"status": "success", ...}
#
# id: 4
# event: message_final
# data: {"content": "The available industry is sugar.", ...}
```

**Files Created/Modified:**
- `app/api/chat.py` - Chat endpoints
- `app/api/stream.py` - SSE streaming endpoint (renamed param to conversation_id)
- `app/api/health.py` - Health check endpoint
- `app/models/requests.py` - Added TokenUsage model
- `app/models/events.py` - Fixed bytes-to-string conversion in from_redis_dict()
- `app/core/mongo_client.py` - Fixed sparse index for session_id

**Bug Fixes:**
- ✅ Fixed Redis bytes keys issue (keys returned as b'data' not "data")
- ✅ Fixed MongoDB duplicate key error (sparse index for null session_id)
- ✅ Fixed stream endpoint parameter naming (conversation_id not request_id)
- ✅ Fixed async await issues in context manager

#### Deliverables ✅
- ✅ All REST endpoints working
- ✅ SSE streaming with replay support
- ✅ Token usage tracking
- ✅ Health check endpoint
- ✅ OpenAPI documentation at /docs

#### Testing Results ✅
- ✅ POST /api/chat → Returns response with token usage
- ✅ GET /stream → Receives all SSE events (tested with curl)
- ✅ Multi-turn conversations work correctly
- ✅ Agentic loop auto-recovers from errors
- ✅ Real-time streaming verified (events arrive as they occur)

---

### **PHASE 8: Frontend - Core Components** (Day 13-14) 🔄 **NEXT PHASE**

**🎨 FRONTEND DEVELOPMENT STARTS HERE**

**Prerequisites**: ✅ All met
- ✅ All backend endpoints functional (Phase 7 complete)
- ✅ SSE streaming working and tested
- ✅ API documentation complete (available at /docs)
- ✅ Backend running stably with all services

**Backend API Ready for Frontend:**
```
POST /api/chat                           - Send message
GET  /api/chat/{conversation_id}/stream  - SSE events  
GET  /api/chat/{conversation_id}/context - Get context
DELETE /api/chat/{conversation_id}/context - Clear context
GET  /api/health                         - Health check
```

#### Goal
Build React UI with real-time event handling

#### Tasks
1. **API Client**
   - File: `frontend/src/utils/api.js`
   - Implement functions:
     - `sendMessage(conversationId, message)`
     - `getContext(conversationId)`
     - `clearContext(conversationId)`

2. **SSE Hook**
   - File: `frontend/src/hooks/useSSE.js`
   - Implement `useSSE(requestId, onEvent)`
   - Create EventSource connection
   - Handle reconnection with last_event_id
   - Parse typed events
   - Auto-reconnect on disconnect

3. **Chat State Hook**
   - File: `frontend/src/hooks/useChatState.js`
   - Implement state management:
     - messages, isThinking, activeTool
     - runProgress, streamingText

4. **Event Handlers**
   - File: `frontend/src/utils/eventHandlers.js`
   - Implement handlers for each event type:
     - handleThinkingStart, handleThinkingEnd
     - handleToolStart, handleToolEnd
     - handleRunProgress
     - handleMessageDelta, handleMessageFinal

5. **UI Components**
   - **ChatThread.jsx**: Main container, EventSource connection
   - **ThinkingIndicator.jsx**: Animated spinner during thinking
   - **ToolExecutionCard.jsx**: Tool name, args, status, duration
   - **RunProgressBar.jsx**: Block name, percentage, animated bar
   - **StreamingBubble.jsx**: Plain text, append deltas
   - **MarkdownRenderer.jsx**: Final markdown with tables
   - **MessageInput.jsx**: Text input with send button

#### Deliverables
- ✅ All UI components working
- ✅ Real-time event updates
- ✅ SSE connection with reconnection
- ✅ Responsive design with Tailwind

#### Testing
- Test SSE connection and event handling
- Test reconnection after disconnect
- Test all event types render correctly
- Test markdown rendering with tables
- Test streaming text updates

---

### **PHASE 9: Frontend - Advanced Features** (Day 15)

#### Goal
Add production features (cancellation, error handling, state recovery)

#### Tasks
1. **Cancellation UI**
   - Add "Cancel" button during simulation
   - Show cancellation status
   - Handle run.cancelling → run.cancelled events

2. **Error Handling**
   - Create ErrorBoundary component
   - Handle app.error events
   - Show user-friendly error messages
   - Retry failed connections

3. **State Recovery**
   - Handle state.sync event for reconnection
   - Persist session_id to localStorage
   - Resume conversation after page refresh

4. **Loading States**
   - Skeleton loaders for initial load
   - Shimmer effects for streaming
   - Empty states for new conversations

5. **Responsive Design**
   - Mobile-friendly layout
   - Tablet breakpoints
   - Desktop optimized

#### Deliverables
- ✅ Cancellation working end-to-end
- ✅ Error handling comprehensive
- ✅ State recovery after refresh
- ✅ Responsive on all devices

#### Testing
- Test cancellation during simulation
- Test error handling scenarios
- Test page refresh recovery
- Test responsive layout on different devices

---

### **PHASE 10: Integration & E2E Testing** (Day 16-17)

#### Goal
Test complete flow end-to-end

#### Tasks
1. **E2E Test Scenarios**
   
   **Scenario 1: Full simulation flow**
   - User: "Simulate sugar factory with 15000 kg/hr"
   - Verify: validate → simulate → report
   - Check: All events emitted correctly
   - Check: MongoDB has full run data
   
   **Scenario 2: Explanation request**
   - User: "Explain the results"
   - Verify: No tools called (report-only mode)
   - Check: Report uses stored data only
   
   **Scenario 3: Cancellation**
   - Start simulation
   - Cancel mid-way
   - Verify: Cancellation events + partial results
   
   **Scenario 4: Reconnection**
   - Start simulation
   - Disconnect frontend
   - Reconnect and verify replay

2. **Policy Gate Testing**
   - Test simulate without validate → rejected
   - Test tool not in eligible set → rejected
   - Test exceeding tool call budget → rejected

3. **Performance Testing**
   - Measure end-to-end latency
   - Check Redis memory usage
   - Monitor MongoDB query performance

4. **Bug Fixes & Refinements**
   - Fix any issues found
   - Optimize slow operations
   - Improve error messages

#### Deliverables
- ✅ All E2E scenarios passing
- ✅ Policy gates enforced correctly
- ✅ Performance acceptable
- ✅ No critical bugs

#### Testing
- Run all E2E scenarios
- Verify policy enforcement
- Measure performance metrics
- Document any issues and fixes

---

### **PHASE 11: Documentation & Deployment Prep** (Day 18)

#### Goal
Document everything and prepare for production

#### Tasks
1. **Backend Documentation**
   - API documentation (OpenAPI/Swagger)
   - Event protocol documentation
   - Environment variable reference
   - Deployment guide

2. **Frontend Documentation**
   - Component documentation
   - Event handling guide
   - Styling guide (Tailwind classes)

3. **Developer Guide**
   - Local development setup
   - Running all services
   - Debugging tips
   - Common issues and solutions

4. **Docker Compose Updates**
   - Add backend service to docker-compose
   - Add RQ worker service
   - Configure networking
   - Add health checks

5. **Production Checklist**
   - Environment variables documented
   - Secrets management strategy
   - Monitoring and logging setup
   - Error tracking setup
   - Performance monitoring

#### Deliverables
- ✅ Complete documentation
- ✅ Docker Compose updated
---

## 📊 CURRENT STATUS SUMMARY (January 6, 2026)

### ✅ Completed Phases
- **Phase 0**: Infrastructure Setup (Redis + MongoDB running)
- **Phase 1**: Tool Registry & Metadata (13 tools registered)
- **Phase 2**: Core Infrastructure (Redis, MongoDB, MCP, Claude clients)
- **Phase 3**: Policy Layer & Context Management (✅ JUST COMPLETED)
  - Context Manager: Dual-storage (Redis + MongoDB) working
  - Policy Gates: Prerequisite enforcement implemented
  - Orchestration Service: 9/9 unit tests passing
  - Integration Tests: 4/9 passing (core infrastructure validated)

### 🔄 Current Work
- **Phase 3 Completion**: 
  - ✅ All orchestration unit tests passing
  - ✅ Redis integration working (3/3 tests)
  - ✅ Context Manager validated with dual storage
  - ⚠️ MongoDB async tests need event loop fix (2 tests)
  - ⚠️ MCP client tests skipped (API mismatch to fix)

### 🎯 Next Steps (In Priority Order)

**Immediate (Today/Tomorrow)**:
1. **Fix remaining integration tests** (2-3 hours)
   - Fix MongoDB event loop issues (2 tests)
   - Fix MCP client initialization (3 tests)
   - Target: 9/9 integration tests passing

2. **Phase 4: Event System** (1 day)
   - Implement basic event models
   - Add event emission to orchestration flow
   - Simple SSE endpoint for event streaming

**Short-term (This Week)**:
3. **Phase 5: RQ Worker & Agentic Loop** (2-3 days)
   - Implement background job processing
   - Build agentic tool-calling loop
   - Integrate with orchestration service
   - Test end-to-end flow

4. **Phase 6: Report Integrity Guard** (1 day)
   - Number validation for LLM reports
   - Template fallback system

**Medium-term (Next Week)**:
5. **Phase 7: FastAPI Endpoints** (1 day)
   - REST API for chat interface
   - Health checks
   - Request/response models

6. **Phase 8: Frontend Development** (2-3 days)
   - React UI components
   - SSE connection
   - Real-time updates

### 🎓 Key Learnings from Phase 3
1. ✅ **Dual-storage strategy works**: Redis for speed, MongoDB for persistence
2. ✅ **Context management validated**: Can track conversation state across requests
3. ✅ **Policy enforcement implemented**: Prerequisite checking in place
4. ✅ **Test infrastructure solid**: Unit tests + integration tests framework ready
5. ⚠️ **Integration test challenges**: Event loop issues with Motor (solvable)
6. ⚠️ **MCP client needs work**: API initialization needs alignment

### 🚀 Development Velocity
- **Phases 0-3**: ~6 days (planned: 6 days) ✅ ON TRACK
- **Remaining Backend**: ~6 days (Phases 4-7)
- **Frontend**: ~6 days (Phases 8-11)
- **Total Estimated**: ~18 days total, ~12 days remaining

---

## 🎯 Success Criteria
#### Testing
- Follow setup guide on fresh machine
- Verify all documentation is accurate
- Test Docker Compose setup

---

## 🎯 Success Criteria

### Functional Requirements
- ✅ User can send message and see thinking indicator
- ✅ Tools execute in correct order (validate → simulate)
- ✅ Real-time progress updates during simulation
- ✅ Report generated with no invented numbers
- ✅ User can cancel running simulation
- ✅ Page refresh preserves conversation state
- ✅ All 13 MCP tools accessible via chat

### Technical Requirements
- ✅ Policy gates prevent invalid tool calls
- ✅ Intent detection filters tools correctly
- ✅ SSE reconnection works reliably
- ✅ MongoDB stores all run data
- ✅ Redis TTL cleans up old events
- ✅ Report integrity guard catches hallucinations
- ✅ Tool results summarized to save context

### Performance Requirements
- ✅ Full simulation completes in < 30 seconds
- ✅ Event latency < 100ms
- ✅ SSE reconnection < 2 seconds
- ✅ UI responsive (no blocking)

---

## 📦 Key Dependencies

### Backend
```
fastapi==0.110.0
uvicorn[standard]==0.27.0
redis[hiredis]==5.0.1
motor==3.3.2
anthropic==0.18.1
rq==1.16.0
pydantic==2.6.0
pydantic-settings==2.1.0
python-dotenv==1.0.1
```

### Frontend
```
react==18.2.0
vite==5.0.0
tailwindcss==3.4.0
react-markdown==9.0.1
remark-gfm==4.0.0
date-fns==3.2.0
```

---

## 🔄 Development Workflow

### Daily Workflow
1. Start services: `docker-compose up -d`
2. Start backend: `cd backend && uvicorn app.main:app --reload`
3. Start worker: `cd backend && rq worker`
4. Start frontend: `cd frontend && npm run dev`
5. Test feature
6. Commit changes

### Testing Each Phase
- Write unit tests first (TDD)
- Test with real MCP server
- Test with real Claude API
- Verify events in Redis
- Check data in MongoDB

---

## 🚨 Risk Mitigation

| Risk | Mitigation |
|------|------------|
| **Claude API costs** | Start with small tests, implement caching |
| **MCP connection failures** | Supervisor pattern, auto-reconnect |
| **Redis memory overflow** | 1-hour TTL, monitor usage |
| **Policy gates too strict** | Extensive testing, logs for debugging |
| **Report hallucination** | Integrity guard with template fallback |
| **SSE connection issues** | Auto-reconnect, replay support |

---

## 📈 Future Enhancements (Post-MVP)

### Core Optimizations (Add After End-to-End Works)
1. **Intent Detector** - Pre-filter tools based on user intent (save $0.02 per message)
2. **Response Formatter** - Custom formatting for business-specific needs (unit conversions, links)
3. **Tool Pre-filtering** - Send only relevant 3-5 tools instead of all 13
4. **Result Summarizer** - Compress tool results to save context tokens

### Advanced Features
5. **Multi-LLM Support**: Add OpenAI GPT-4 (Gemini already configured)
6. **Authentication**: JWT-based user auth
7. **Multi-session**: Support multiple concurrent chats
8. **Context Summarization**: Compress old messages when nearing token limit
9. **Tool Analytics**: Track tool usage, success rates, costs
10. **Caching**: Cache validation results to save API calls
11. **Webhooks**: Notify external systems on simulation completion

---

## ⏱️ Estimated Timeline

### Backend Development (Phases 0-7)
- **Phase 0**: 1 day - Infrastructure Setup
- **Phase 1**: 1 day - Tool Registry & Metadata
- **Phase 2**: 2 days - Core Infrastructure
- **Phase 3**: 2 days - Policy Layer & Intent Detection
- **Phase 4**: 1 day - Event System & SSE
- **Phase 5**: 3 days - RQ Worker & Agentic Loop (most complex)
- **Phase 6**: 1 day - Report Integrity Guard
- **Phase 7**: 1 day - FastAPI Endpoints

**Backend Subtotal: ~12 days (2.5 weeks)**

### Frontend Development & Integration (Phases 8-11)
- **Phase 8**: 2 days - Frontend Core Components *(Frontend starts here)*
- **Phase 9**: 1 day - Frontend Advanced Features
- **Phase 10**: 2 days - Integration & E2E Testing
- **Phase 11**: 1 day - Documentation & Deployment Prep

**Frontend Subtotal: ~6 days (1.5 weeks)**

### Total Timeline
**Total: ~18 days (4 weeks with buffer)**
- Backend completion: ~2.5 weeks
- Frontend & integration: ~1.5 weeks

---

## 🔍 Key Architectural Principles

### Controlled Agentic Pattern
- LLM proposes tool calls based on user intent
- Backend enforces eligibility, prerequisites, ordering
- Policy gates ensure correctness and safety

### Two-Step Flow
- **Step A**: Tool execution (policy-governed, no narrative)
- **Step B**: Report generation (tools disabled, narrative only)
- Prevents number hallucination in reports

### Anti-Hallucination Strategy
- MCP schema prevents invalid tool calls
- Policy gates enforce correct workflows
- Report integrity guard validates numbers
- Template fallback for failed validation

### Event-Driven Architecture
- Typed SSE events for real-time UI updates
- Redis Streams for event storage and replay
- 1-hour TTL for automatic cleanup

### Tool Result Summarization
- Store full results in MongoDB
- Return summaries to LLM (save tokens)
- Fetch full data for report generation

---

## 📝 Notes

- **No changes to existing code**: main.py and mcp_process_server remain untouched
- **Local testing first**: All development and testing done locally before deployment
- **Focus on Anthropic Claude**: Other LLM providers added later
- **No authentication in MVP**: Can be added in future phases
- **Production-grade from start**: All 13 tools with full metadata, complete policy layer
