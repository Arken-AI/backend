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

### Backend
- **Framework**: FastAPI (async)
- **Queue**: RQ (Redis Queue)
- **Databases**: MongoDB (persistent), Redis (session + events)
- **LLM**: Anthropic Claude
- **MCP**: Existing mcp_process_server (stdio transport)

### Frontend
- **Framework**: Vite + React
- **State Management**: Context API (simple, built-in)
- **Styling**: Tailwind CSS
- **Markdown**: react-markdown + remark-gfm

### Infrastructure
- **MongoDB**: Already running in Docker
- **Redis**: To be added to docker-compose.yml
- **Authentication**: Skipped for MVP (add later)

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

### **PHASE 3: Policy Layer & Context Management** (Day 5-6)

#### Goal
Implement core policy enforcement and conversation context management (simplified approach)

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

#### Deliverables
- ✅ Context Manager storing conversation state in Redis/MongoDB
- ✅ Policy Gates enforcing validate-before-simulate
- ✅ Simple Orchestration connecting all components
- ✅ End-to-end flow working (user message → tool execution → response)
- ✅ Unit tests for core logic

#### Testing
- Context: Store/retrieve conversation state
- Policy: Test validate → simulate enforcement
- Policy: Test validation age checking (< 10 minutes = valid)
- Budget: Test max calls and timeout enforcement
- Orchestration: Test end-to-end message flow
- Multi-turn: Test "simulate again" uses previous validation

---

### **PHASE 4: Event System & SSE** (Day 7)

**Note**: This phase may be deferred until after frontend is complete and end-to-end flow is verified working. Event system can be added incrementally for real-time UI updates.

#### Goal
Build typed SSE event protocol for real-time UI updates

#### Tasks
1. **Event Models**
   - File: `backend/app/models/events.py`
   - Define Pydantic models for all event types:
     - BaseEvent (request_id, seq, ts)
     - ThinkingStartEvent, ThinkingEndEvent
     - ToolStartEvent, ToolEndEvent
     - RunStatusEvent, RunProgressEvent
     - MessageDeltaEvent, MessageFinalEvent
     - AppErrorEvent

2. **Event Emitter Service**
   - File: `backend/app/services/event_emitter.py`
   - Implement `EventEmitter` class with methods:
     - `emit_thinking_start(request_id)`
     - `emit_thinking_end(request_id)`
     - `emit_tool_start(request_id, tool_name, args)`
     - `emit_tool_end(request_id, tool_name, status, duration, summary)`
     - `emit_run_status(request_id, status, calc_run_id)`
     - `emit_run_progress(request_id, block_name, percentage)`
     - `emit_message_delta(request_id, delta)`
     - `emit_message_final(request_id, content)`
     - `emit_app_error(request_id, error, details)`
   - Store events in Redis Streams
   - Auto-increment sequence numbers

3. **SSE Generator**
   - File: `backend/app/api/chat.py`
   - Implement SSE streaming endpoint
   - Replay from last_event_id if provided
   - Stream live events
   - Format as SSE: id, event, data

#### Deliverables
- ✅ All event types defined
- ✅ Event emission working
- ✅ SSE streaming endpoint
- ✅ Replay from last event ID

#### Testing
- Emit events, verify Redis storage
- Test SSE endpoint with curl
- Test replay with last_event_id

---

### **PHASE 5: RQ Worker & Agentic Loop** (Day 8-10)

#### Goal
Build the core agentic execution engine (Step A + Step B)

#### Tasks
1. **RQ Job Infrastructure**
   - File: `backend/app/workers/__init__.py`
   - Set up RQ worker configuration
   - Redis queue setup
   - Job retry policies

2. **Agentic Loop Job (Step A)**
   - File: `backend/app/workers/agentic_loop.py`
   - Implement `run_agentic_loop_job(request_id, session_id, user_message)`
   
   **Step A Flow:**
   - Emit thinking.start
   - Load session context from Redis
   - Detect intent from user message
   - Get all MCP tools
   - Filter tools by intent (eligibility filtering)
   - Convert MCP tools to Claude format
   - Build conversation history
   - Call Claude with eligible tools
   - Loop until stop_reason == "end_turn":
     - If tool_use: enforce policy gates
     - Call tool via MCP if allowed
     - Store full result in MongoDB
     - Summarize result
     - Emit tool events
     - Return summary to Claude
   - Emit thinking.end
   - Return calc_run_id

3. **Report Generator (Step B)**
   - File: `backend/app/workers/report_generator.py`
   - Implement `generate_report(request_id, calc_run_id)`
   
   **Step B Flow:**
   - Emit thinking.start
   - Fetch full run data from MongoDB
   - Build report prompt with run data
   - Call Claude WITHOUT tools
   - Stream response with message.delta
   - Emit message.final
   - Emit thinking.end

4. **Cancellation Support**
   - Implement cancellation flag checking
   - Check job status before expensive operations
   - Graceful abort with partial results

5. **Integration**
   - Connect all pieces:
     - MCP client for tool calls
     - Policy gates before each tool call
     - Result summarizer after each tool call
     - Event emitter throughout
     - MongoDB storage for runs

#### Deliverables
- ✅ Complete agentic loop working
- ✅ Two-step flow (A + B) implemented
- ✅ Policy enforcement integrated
- ✅ Event emission throughout
- ✅ Cancellation support

#### Testing
- Test with real user message: "Simulate sugar factory with 15000 kg/hr"
- Verify tool calls are policy-compliant
- Verify events are emitted correctly
- Test cancellation mid-simulation
- Verify MongoDB stores full results

---

### **PHASE 6: Report Integrity Guard** (Day 11)

#### Goal
Prevent LLM from inventing numbers in reports

#### Tasks
1. **Number Extraction Utility**
   - File: `backend/app/workers/integrity_guard.py`
   - Implement `extract_numbers(text: str)` → List[float]
   - Use regex to find all numbers in text
   - Include units-aware extraction
   - Return normalized float values

2. **Integrity Validator**
   - File: `backend/app/workers/integrity_guard.py`
   - Implement `validate_report(report_text, source_data)` → (bool, List[str])
   - Extract all numbers from report
   - Extract all numbers from source JSON
   - Compare with 1% rounding tolerance
   - Return validation result + invented numbers list

3. **Template Report Generator**
   - File: `backend/app/workers/integrity_guard.py`
   - Implement `generate_template_report(run_data)` → str
   - Deterministic markdown generation
   - Process metadata section
   - KPIs table from run JSON
   - Warnings list from run JSON
   - No LLM involved

4. **Integration into Report Generator**
   - Modify report generator with retry logic
   - Max 2 retries for validation
   - Fallback to template if validation fails

#### Deliverables
- ✅ Report integrity validation working
- ✅ Template report as fallback
- ✅ Integration with report generator
- ✅ Unit tests for validation logic

#### Testing
- Test with valid report (all numbers match)
- Test with invalid report (invented numbers)
- Verify fallback to template report
- Test edge cases (percentages, scientific notation)

---

### **PHASE 7: FastAPI Endpoints** (Day 12)

**⚠️ LAST BACKEND-ONLY PHASE - Frontend development begins in Phase 8**

#### Goal
Build REST API endpoints for chat interface

#### Tasks
1. **Request/Response Models**
   - File: `backend/app/models/requests.py`
   - Define models:
     - ChatRequest (session_id, message, client_message_id)
     - ChatResponse (request_id, status)
     - ChatStateResponse (request_id, status, messages, last_run_id)

2. **Chat Endpoints**
   - File: `backend/app/api/chat.py`
   - Implement endpoints:
   
   **POST /chat**
   - Validate request
   - Check idempotency
   - Generate request_id
   - Queue RQ job
   - Return request_id and status
   
   **GET /chat/{request_id}/stream**
   - Replay from last_event_id if provided
   - Stream live events from Redis Streams
   - Format as SSE
   
   **POST /chat/{request_id}/cancel**
   - Set cancellation flag in Redis
   - Return cancellation status
   
   **GET /chat/{request_id}**
   - Fetch final state from Redis + MongoDB
   - Return status, messages, last_run_id

3. **Health Endpoint**
   - File: `backend/app/api/health.py`
   - Check MongoDB connection
   - Check Redis connection
   - Check MCP server connection
   - Return service health status

4. **Main FastAPI App**
   - File: `backend/app/main.py`
   - Create FastAPI app
   - Add CORS middleware
   - Register routers
   - Startup/shutdown events
   - Error handlers

#### Deliverables
- ✅ All REST endpoints working
- ✅ Idempotency support
- ✅ SSE streaming endpoint
- ✅ Health check endpoint
- ✅ OpenAPI documentation

#### Testing
- Test POST /chat → returns request_id
- Test GET /stream → receives SSE events
- Test POST /cancel → cancels job
- Test GET /chat/{id} → returns final state
- Test idempotency with duplicate requests

---

### **PHASE 8: Frontend - Core Components** (Day 13-14)

**🎨 FRONTEND DEVELOPMENT STARTS HERE**

**Prerequisites**: 
- ✅ All backend endpoints functional (Phase 7 complete)
- ✅ SSE streaming working and tested
- ✅ API documentation complete
- ✅ Backend running stably with all services

#### Goal
Build React UI with real-time event handling

#### Tasks
1. **API Client**
   - File: `frontend/src/utils/api.js`
   - Implement functions:
     - `sendMessage(sessionId, message)`
     - `cancelRequest(requestId)`
     - `getChatState(requestId)`

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
- ✅ Production checklist
- ✅ Deployment guide

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
