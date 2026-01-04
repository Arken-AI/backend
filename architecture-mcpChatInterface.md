# MCP Chat Interface - Architecture Document

## Overview

This document describes the architecture for a production-grade chat interface that replicates the Claude Desktop user experience for the MCP Process Server (streaming text, structured tool panels, warnings, and progress).

Tool-calling uses a controlled agentic pattern: the LLM proposes tool calls based on user intent and available MCP tools, while the backend enforces eligibility, prerequisites, ordering (where required), budgets, and safety policies. This preserves the Claude Desktop feel without trusting the model for correctness-critical sequencing.

Key principle: the calculation engine remains the source of truth for all numeric results; the LLM is used for tool selection within constraints and for explain/summarize/recommend report writing.

---

## System Architecture

### High-Level Components

```
┌─────────────┐       SSE Events          ┌─────────────────────────────────────────┐
│   React     │ ◄─────────────────────────│              FastAPI Backend            │
│   Frontend  │   (typed event protocol)  │  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
└─────────────┘                           │  │ Chat API│  │Job Queue│  │ Report  │ │
                                          │  │         │  │  (RQ)   │  │ Writer  │ │
                                          │  └────┬────┘  └────┬────┘  └────┬────┘ │
                                          └───────┼────────────┼────────────┼──────┘
                                                  │            │            │
                    ┌─────────────────────────────┴────────────┴────────────┘
                    │
         ┌──────────┼──────────┬─────────────────┐
         ▼          ▼          ▼                 ▼
   ┌──────────┐ ┌────────┐ ┌────────┐      ┌──────────┐
   │   MCP    │ │ Redis  │ │MongoDB │      │  Claude  │
   │  Server  │ │(session│ │ (runs) │      │   API    │
   │(persistent)│ │ +queue)│ │        │      │          │
   └──────────┘ └────────┘ └────────┘      └──────────┘
         │
         ▼
   ┌──────────┐
   │  Calc    │
   │  Engine  │
   └──────────┘
```

### Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| **React Frontend** | User interface, SSE event consumption, real-time state display |
| **FastAPI Backend** | API endpoints, SSE streaming, job coordination |
| **RQ Job Queue** | Background job execution for agentic loop |
| **MCP Server** | Tool interface to calculation engine, tool discovery |
| **Redis** | Session state, event streams (replay), job queue backend |
| **MongoDB** | Persistent storage for runs, conversation history |
| **LLM Provider (Claude/OpenAI/Gemini)** | Proposes tool calls from the eligible tool set (within backend policy constraints) and generates the Step B report (tools disabled) |
| **Calc Engine** | Process simulation computations |

---

## Agentic Execution Model (Claude Desktop Style)

The core architectural pattern replicates the Claude Desktop experience while remaining correctness-safe: the LLM proposes tool calls, the backend executes approved calls via MCP, and backend policy gates enforce eligibility, prerequisites, ordering, and budgets. Narrative generation is separated into a second report-only step.

### Orchestration Contract (LLM Proposes, Backend Governs)

The system follows a two-layer contract:
- **LLM responsibilities**: interpret natural language, ask clarifying questions when required inputs are missing, propose tool calls from the eligible tool set, and generate user-facing narrative (explain/summarize/recommend).
- **Backend responsibilities**: decide which tools are eligible for the request, enforce prerequisites and ordering for simulation-class workflows (e.g., validate → simulate), enforce budgets/timeouts, and persist authoritative run data.

If the LLM proposes a disallowed tool call, the backend rejects it with a structured error that guides the model to the next valid action.

### Agentic Loop Overview

```
┌─────────────────────────────────────────────────────────────┐
│                CONTROLLED AGENTIC + TWO-STEP FLOW           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Step A — Tool Execution (policy-governed)                  │
│  1. User sends message                                      │
│  2. Backend selects eligible tool set (filtering)           │
│  3. LLM proposes a tool call (or asks for missing inputs)   │
│  4. Backend checks policy gates (eligibility/prereqs/order) │
│     ├─► Allowed: execute via MCP and emit tool.start/end    │
│     └─► Denied: return structured error to LLM              │
│  5. Repeat until backend marks Step A "complete"            │
│  6. Persist authoritative run JSON (calc_run_id) to MongoDB │
│                                                             │
│  Step B — Report Generation (tools disabled)                │
│  7. LLM receives stored run data (or safe subset)           │
│  8. LLM streams narrative (message.delta)                   │
│  9. Complete response (message.final, thinking.end)         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Why Agentic (Like Claude Desktop)?

| Benefit | Explanation |
|---------|-------------|
| **Flexibility** | Claude handles any request without hardcoded workflows |
| **Natural UX** | Identical to Claude Desktop experience |
| **Extensibility** | Add new MCP tools, Claude automatically discovers them |
| **Less per-tool hardcoding** | No need to write per-tool routing logic; tools are discovered dynamically and governed by a generic policy layer |
| **Conversational** | Claude can ask clarifying questions mid-flow |

---

## Event Protocol

### SSE Event Framing

All events use Server-Sent Events with typed event names:

```
id: {redis_stream_id}
event: {event_type}
data: {json_payload}
```

Every payload includes: `{request_id, seq, ts}` for correlation and debugging.

### Event Types

| Event | When | Purpose |
|-------|------|---------|
| `thinking.start` | Loop start | Show global spinner, agentic loop begins |
| `thinking.end` | Loop end | Hide spinner, response complete |
| `tool.start` | Claude requests tool | Show tool execution card with name and args |
| `tool.end` | Tool completes | Update tool card with status and duration |
| `run.status` | During simulation | Simulation status changes |
| `run.progress` | During simulation | Progress bar updates |
| `run.cancelling` | Cancel requested | Cancellation initiated |
| `run.cancelled` | Cancel complete | Cancellation confirmed |
| `message.delta` | Claude streaming | Streaming text chunks (plain text) |
| `message.final` | Response complete | Complete markdown for rendering |
| `state.sync` | Reconnection | Full state snapshot for recovery |
| `app.error` | Any error | Application errors (not transport errors) |

### Event Flow Example

User: "Simulate sugar factory with 15000 kg/hr"

--- Step A: Tool Execution (no narrative streaming) ---
1.  thinking.start  
2.  tool.start {validate_process_inputs}  
3.  tool.end {success}  
4.  tool.start {simulate_process}  
5.  run.status {running}  
6.  run.progress {mill, 20%}  
7.  run.progress {evaporator, 60%}  
8.  run.progress {crystallizer, 90%}  
9.  tool.end {success, calc_run_id: "run_xxx"}  
10. thinking.end  

--- Step B: Report Generation (tools disabled) ---
11. thinking.start  
12. message.delta "## Sugar Factory Results..."  
13. message.delta "**Feed Conditions:** ..."  
14. message.delta "**Key KPIs:** ..."  
15. message.final {complete markdown}  
16. thinking.end

### How Claude Decides Tool Order

The LLM proposes tool calls using the available tool schemas and descriptions, but tool sequencing correctness does not rely on prompt text.

Ordering is enforced by the backend policy layer:
- For simulation-class workflows, the backend requires prerequisites (e.g., validation success) before allowing gated simulation tools.
- The backend may reject or request a corrective step when the LLM proposes an invalid order.

Tool descriptions remain useful for improving LLM behavior, but policy gates are authoritative.

---

## API Endpoints

### REST Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/chat` | Submit message, returns `request_id`, queues job |
| GET | `/chat/{request_id}/stream` | SSE endpoint for real-time events |
| POST | `/chat/{request_id}/cancel` | Cancel running simulation |
| GET | `/chat/{request_id}` | Fetch final state (late-join/recovery) |

### Request Flow

```
┌──────────┐      POST /chat       ┌──────────┐
│ Frontend │ ───────────────────►  │ FastAPI  │
│          │ ◄─────────────────── │          │
│          │   {request_id}        │          │
│          │                       │          │
│          │   GET /stream         │          │
│          │ ───────────────────►  │          │
│          │ ◄════════════════════ │          │
│          │   SSE Events          │          │
└──────────┘                       └──────────┘
```

---

## Data Storage Architecture

### Redis (Session + Events)

```
session:{session_id}:messages    → Conversation history (List)
session:{session_id}:last_run_id → Quick reference (String)
session:{session_id}:state       → Current parameters (Hash)
job:{request_id}:status          → Job state (String)
job:{request_id}:progress        → Progress percentage (String)
chat:{request_id}:events         → Event stream (Stream) - 1hr TTL
```

### MongoDB (Persistent)

**conversations collection:**
- session_id, user_id
- messages array
- created_at, updated_at

**runs collection:**
- calc_run_id (primary key)
- session_id, request_id (references)
- inputs (feeds, node_params, solver_settings)
- outputs (full simulation JSON)
- warnings, kpis
- report_markdown
- engine_version, schema_version
- timestamp

---

## Worker Architecture

### RQ Job Execution Model (Agentic Loop)

```
┌─────────────────────────────────────────────────────────────┐
│                     RQ WORKER (Sync)                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  run_agentic_loop_job(request_id, session_id, user_msg)     │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Event Loop Wrapper (for async MCP/LLM calls)          │  │
│  │                                                       │  │
│  │  1. Discover tools + apply eligibility filtering      │  │
│  │  2. Step A: call LLM with eligible tools              │  │
│  │  3. On tool_use: enforce policy, execute tool,        │  │
│  │     store full result, return summary                 │  │
│  │  4. Loop until completion criteria met                │  │
│  │  5. Persist calc_run_id + run JSON                    │  │
│  │  6. Step B: call LLM without tools and stream         │  │
│  │     message.delta → message.final                     │  │
│  │                                                       │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Event Emission: emit_sse_event_sync() → Redis Streams      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Key Principles

- **LLM proposes tools**: The LLM proposes tool calls from the eligible set based on user intent
- **Backend governs execution**: Eligibility, prerequisites, ordering, and budgets are enforced before any tool runs
- **Two-step completion**: Step A ends when an authoritative run is persisted; Step B streams the final report with tools disabled
- **FastAPI endpoints**: Async (non-blocking)
- **RQ workers**: Sync wrapper around async operations
- **Event emission in workers**: Synchronous Redis client
- **MCP client**: Persistent per worker process (or small pool), supervised and re-initialized on failure

---

## MCP Connection Strategy

### Connection Management

```
┌─────────────────────────────────────────────────────────────┐
│                   MCP CLIENT POOL                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  • One connection per worker process (or small pool)        │
│  • Async lock serializes concurrent tool calls              │
│  • Supervisor pattern: restart on crash, re-handshake       │
│  • Future: Networked MCP for horizontal scaling             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Tool Discovery for the LLM

At the start of each agentic loop:
1. Worker calls `mcp_client.list_tools()` to get available tools
2. Tools are converted to the active provider's tool format (Anthropic/OpenAI/Gemini) with name, description, input_schema
3. The LLM receives only the eligible tools for the request and may propose calls within policy constraints

### Tool Call Serialization

All tool calls through a single MCP connection are serialized via async lock to prevent race conditions when multiple sessions invoke tools concurrently.

---

## Anti-Hallucination Strategy

**Core Principle**: MCP schema prevents invalid calls; **correctness is enforced by backend policy gates + report integrity guard**.

### How MCP Tool Definitions Prevent Hallucination

Claude can **only call tools that exist in the MCP schema**. This eliminates several hallucination risks:

| Risk | Prevented By |
|------|-------------|
| Inventing tool names | MCP schema defines all valid tools |
| Wrong parameter types | JSON Schema enforces types |
| Missing required params | JSON Schema `required` field |
| Invalid enum values | JSON Schema `enum` constraints |

### What Requires Additional Controls

| Risk | Mitigation |
|------|------------|
| Wrong tool or wrong order | Backend eligibility filtering + policy gates (prerequisites + ordering) |
| Skipping required validation | Backend prerequisite enforcement for gated tools |
| Guessing parameter values | Schemas + enums + clarifying questions; backend rejects missing required params |
| Inventing numbers in the report | Two-step flow + report integrity guard + deterministic fallback |

### Best Practices for MCP Tool Descriptions

**Good description (guides Claude correctly):**
```
validate_process_inputs:
  description: >
    MUST be called before simulate_process.
    Validates process inputs against schema and thermodynamic constraints.
    Returns validation errors if any.
    If validation fails, DO NOT proceed to simulation.
```

**Bad description (ambiguous, risky):**
```
validate_process_inputs:
  description: "Validates a process"
```

### Using Enums to Constrain Values

Instead of free-text parameters, use enums:

```json
{
  "name": "simulate_process",
  "inputSchema": {
    "properties": {
      "process_id": {
        "type": "string",
        "enum": ["sugar_evaporator", "milk_pasteurizer", "juice_concentrator"],
        "description": "Must be one of the predefined process types"
      }
    }
  }
}
```

Claude **cannot invent a process_id** — it must pick from the list.

---

## Tool Policy Layer (Production Controls)

For production systems with 20+ tools, **schema alone is insufficient**. The backend must enforce correctness through policy gates, tool filtering, and result validation.

### 1. Tool Registry Metadata

Each MCP tool includes extended metadata (stored alongside the MCP schema):

**Metadata Fields:**
- `name`: MCP tool name
- `domain`: "sugar" | "hydrogen" | "generic"
- `category`: "validate" | "simulate" | "lookup" | "compare" | "report"
- `risk_level`: "safe" | "gated" (gated = triggers compute runs or state changes)

**Example tool registry:**

| Tool Name | Domain | Category | Risk Level |
|-----------|--------|----------|------------|
| `validate_sugar_inputs` | sugar | validate | safe |
| `simulate_sugar_process` | sugar | simulate | gated |
| `get_run` | generic | lookup | safe |
| `compare_runs` | generic | compare | safe |
| `list_previous_runs` | generic | lookup | safe |

**Benefits**:
- Enables programmatic tool filtering (not just prompt engineering)
- Makes system auditable and debuggable
- Supports per-domain/category access control

### 2. Tool Eligibility Filtering (OPTIONAL - Post-MVP Optimization)

**Current Approach (MVP)**: Send all 13 tools to Claude every request.
- Simple implementation
- Let Claude handle intent detection naturally
- No pre-filtering complexity
- Get end-to-end working first

**Future Optimization**: After MVP is working and you have usage data showing costs are high, add intent-based filtering.

#### Intent-Based Filtering Rules (Future)

| User Intent (Keywords) | Eligible Tools | Token Savings |
|------------------------|----------------|---------------|
| "simulate", "run", "estimate", "calculate" | validate + simulate + lookup (domain-specific) | ~60% reduction |
| "explain", "summarize", "recommend", "analyze" | NO TOOLS (report-only mode) | ~100% reduction |
| "get previous", "show run", "compare" | lookup + compare only | ~80% reduction |
| "list", "what runs", "history" | lookup only | ~85% reduction |

**When to implement**: After you have real cost data showing tool schema tokens are a significant expense (likely $50+/month).

**Current tool count (13)** is small enough that filtering may not provide meaningful savings. Reconsider if tool count grows to 30+.

### 3. Backend Policy Gates

Even if Claude proposes a tool call, the backend enforces correctness:

#### Hard Gates (Always Enforced)

**Gate Rules:**
1. **Validation Prerequisite**: `simulate_*` tools require prior `validate_*` success for same process_id
2. **Required Parameters**: Enforce presence of required identifiers (e.g., process_id)
3. **Eligibility Check**: Only tools from eligible set can be called
4. **Report-Only Mode**: For explain/summarize/recommend intent, tools are disabled and only stored run data is used

**Return**: (allowed: bool, reason_if_denied: str)

#### Order Gates (Workflow Enforcement)

**Workflow Definitions:**
- Sugar process: validate_sugar_inputs → simulate_sugar_process → get_run
- Hydrogen process: validate_hydrogen_inputs → simulate_hydrogen_process → get_run

**Enforcement Logic:**
- For each tool call, verify all previous workflow steps were executed
- Tools not in workflow are allowed (no ordering constraint)
- Reject if prerequisite tools missing from execution history

#### Budget Gates (Resource Limits)

**Resource Limits:**
- Maximum tool calls per request: 6
- Maximum simulation time: 300 seconds (5 minutes)

**Enforcement:**
- Track tool call count in request context
- Track elapsed time since request start
- Reject tool execution if limits exceeded
- Return error message to Claude

### 4. Two-Step Response Flow

To ensure **explain/summarize/recommend** uses only stored data (not LLM-generated numbers):

```
┌─────────────────────────────────────────────────────────────┐
│                    TWO-STEP FLOW                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Step A: Agentic Tool Execution                             │
│  ────────────────────────────────                           │
│  • Claude decides which tools to call                       │
│  • Backend enforces policy gates                            │
│  • Tools execute, results stored in MongoDB                 │
│  • NO narrative generation yet                              │
│                                                             │
│                          ↓                                  │
│                                                             │
│  Step B: Report Generation (Tools DISABLED)                 │
│  ──────────────────────────────────────────                 │
│  • Claude receives stored run JSON (or safe subset)         │
│  • Tool access DISABLED                                     │
│  • Generates explain/summarize/recommend                    │
│  • Streams as message.delta → message.final                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Implementation Flow:**

**Step A: Tool Execution (Agentic)**
- Call Claude with user message + eligible tools (filtered)
- Loop until stop_reason == "end_turn":
  - If stop_reason == "tool_use":
    - For each tool call: apply policy gates
    - If rejected: return error to Claude
    - If allowed: execute tool via MCP, store full result in MongoDB
    - Return SUMMARY only to Claude (see section 6)
  - Add tool results to conversation history

**Step B: Report Generation (Tools DISABLED)**
- Build report prompt with stored run JSON and analysis requirements
- Call Claude WITHOUT tools (empty tools array)
- Stream response with message.delta events
- Emit message.final when complete

**Key Points:**
- Step A: Tools enabled, stores data
- Step B: Tools disabled, generates narrative from stored data
- Separation prevents number hallucination

### 5. Report Integrity Guard

**Problem**: LLM might invent numbers not present in run JSON.

**Solution**: Validate report against source data.

**Validation Strategy:**

1. **Number Extraction**: Extract all numbers from report text using regex
2. **Source Verification**: Extract all numbers from run JSON
3. **Comparison**: Check each report number exists in source (allow 1% rounding tolerance)
4. **Retry on Mismatch**: If invented numbers detected, retry up to 2 times
5. **Deterministic Fallback**: If validation fails after retries, use template-based report

**Template Report Structure:**
- Process metadata (process_id, calc_run_id, status)
- Key Performance Indicators (formatted table from run JSON)
- Warnings (formatted list from run JSON)
- Reference to full run data

**Benefits:**
- Catches LLM-invented numbers before showing to user
- Provides reliable fallback when validation fails
- Ensures all displayed numbers are from source data

### 6. Tool Result Summarization (OPTIONAL - Post-MVP Optimization)

**Current Approach (MVP)**: Return full tool results to Claude.
- Simple implementation
- Let Claude handle all the data
- Get working first, optimize later

**Future Optimization**: If conversations approach token limits or costs are high, add result summarization.

**Problem with full results**:
- Returning full 50KB run JSON to Claude in agentic loop
- Wastes context tokens
- Makes conversation history bloated
- But: Simpler to implement initially

**Solution (Future)**:
- Store full result in MongoDB with calc_run_id
- Return only summary to Claude (status, key metrics, run_id)
- Fetch full data only for report generation
- Reduces 50KB → 500 bytes per tool call

**When to implement**: After MVP works and you see token usage approaching limits or costs are significant.

### Policy Layer Summary

| Control | Status | Purpose | Impact |
|---------|--------|---------|--------|
| Tool registry metadata | ✅ MVP | Programmatic tool access | Foundation for all policy |
| Context Manager | ✅ MVP | Track conversation state | Remember validation, parameters |
| Hard gates | ✅ MVP | Prevent invalid operations | Stop bad tool calls before execution |
| Order gates | ✅ MVP | Enforce workflows | Ensure validate → simulate sequence |
| Budget gates | ✅ MVP | Resource limits | Prevent runaway loops |
| Two-step flow | ✅ MVP | Separate tools from narrative | Prevent number hallucination |
| Report integrity guard | ✅ MVP | Validate report accuracy | Catch invented numbers |
| **Eligibility filtering** | 🔮 Future | Intent-based tool selection | Reduce tools from 13 → 3-5 |
| **Result summarization** | 🔮 Future | Reduce context bloat | Keep conversation manageable |
| **Response Formatter** | 🔮 Future | Custom formatting | Business-specific output |

---

## SSE Reconnection & Replay

### Redis Streams for Replay

```
┌─────────────────────────────────────────────────────────────┐
│                  RECONNECTION FLOW                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Client disconnects (network issue, tab switch, etc.)    │
│                                                             │
│  2. Client reconnects with Last-Event-ID header             │
│                                                             │
│  3. Server checks Redis Stream:                             │
│     ├─ Events exist → Replay from Last-Event-ID             │
│     └─ Events expired → Emit state.sync with current state  │
│                                                             │
│  4. Continue following live stream                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Event ID Strategy

- Redis Stream IDs used as SSE `id:` field
- Format: `{milliseconds}-{pid_counter_suffix}`
- Collision avoidance across multiple workers
- 1-hour TTL on event streams

---

## Cancellation Flow

### Cancel + Modify Scenario

```
┌──────────┐         ┌──────────────┐         ┌─────────┐
│ Frontend │         │   Backend    │         │  Worker │
└────┬─────┘         └──────┬───────┘         └────┬────┘
     │                      │                      │
     │ run.progress 60%     │                      │
     │◄─────────────────────┼──────────────────────│
     │                      │                      │
     │ POST /cancel         │                      │
     │─────────────────────►│                      │
     │                      │ set status=cancelling│
     │                      │─────────────────────►│
     │ run.cancelling       │                      │
     │◄─────────────────────│                      │
     │                      │                      │
     │                      │ (job checks status)  │
     │                      │                      │
     │ run.cancelled        │                      │
     │◄─────────────────────┼──────────────────────│
     │                      │                      │
     │ POST /chat (new msg) │                      │
     │─────────────────────►│                      │
     │                      │ (new job with        │
     │                      │  modified params)    │
```

### Cancellation Mechanism

- Redis flag: `job:{request_id}:status = "cancelling"`
- Worker checks flag before each simulation block
- Graceful abort with partial results reported
- Frontend receives `run.cancelling` → `run.cancelled` sequence

---

## Frontend Components

### Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ChatThread                             │
│  (Manages conversation state, EventSource connection)       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ ThinkingIndicator│  │ ToolExecutionCard│ (per tool)      │
│  │ (thinking.start/ │  │ (tool.start/end) │                 │
│  │  thinking.end)   │  │ - name, args     │                 │
│  └─────────────────┘  │ - spinner/status │                 │
│                       │ - duration       │                 │
│                       └─────────────────┘                   │
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ RunProgressBar  │  │ StreamingBubble │                   │
│  │ (run.progress)  │  │ (message.delta) │                   │
│  │ - block name    │  │ - plain text    │                   │
│  │ - percentage    │  │ - append only   │                   │
│  └─────────────────┘  └─────────────────┘                   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │               MarkdownRenderer                       │    │
│  │ (message.final) - tables, KPIs, warnings, code       │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Rendering Strategy

| Phase | Display |
|-------|---------|
| Streaming (`message.delta`) | Plain text, no markdown parsing |
| Complete (`message.final`) | Full markdown with tables, syntax highlighting |

This prevents flickering tables and broken formatting during streaming.

---

## Error Handling Strategy

### Error Categories

| Category | Event | Handling |
|----------|-------|----------|
| Tool execution failure | `tool.end` (status: error) + `app.error` | Stop Phase 1, show error |
| Simulation divergence | `run.status` (failed) + `app.error` | Report convergence issue |
| MCP connection failure | `app.error` | Retry with backoff, show error |
| LLM API error | `app.error` | Fallback to template report |
| Storage error | `app.error` (warning) | Log, continue if possible |
| Job failure (all retries) | `app.error` | Mark failed, notify user |

### Error Event vs Transport Error

- **`app.error` event**: Application-level errors, displayed in UI
- **`EventSource.onerror`**: Transport-level errors, trigger reconnection

---

## Context Management

### Token Budget

```
┌─────────────────────────────────────────────┐
│          Claude Context (200K tokens)       │
├─────────────────────────────────────────────┤
│ System prompt + Tools schema:    ~5K tokens │
│ Available for conversation:    ~180K tokens │
│ Reserve for response:           ~15K tokens │
├─────────────────────────────────────────────┤
│ Typical message sizes:                      │
│   User message:         50-200 tokens       │
│   Assistant response:   200-2000 tokens     │
│   Tool result summary:  100-500 tokens      │
└─────────────────────────────────────────────┘
```

### Context Strategy (Phased)

**MVP**: Send full conversation history each request

**Production**: When approaching 150K tokens:
- Summarize older messages
- Keep recent 10 turns in full
- Tool results always summarized (reference stored JSON)

---

## Idempotency and Duplicate Request Handling

To prevent accidental duplicate job execution (browser retries, refresh, network flaps), the backend should support idempotency:
- The frontend sends a `client_message_id` with each `POST /chat`.
- The backend deduplicates by `(session_id, client_message_id)` and returns the existing `request_id` when a duplicate is detected.
- Replays and late-joins use `GET /chat/{request_id}` and `GET /chat/{request_id}/stream` with Redis Streams replay.

## LLM Provider Abstraction

To allow switching between Claude, OpenAI, and Gemini without rewriting orchestration logic, implement a provider adapter boundary:
- Normalize messages into an internal format (role/content/tool call/result).
- Map internal tool schema and tool-call/result formats to each provider’s requirements.
- Keep tool policy, eligibility filtering, and two-step flow provider-agnostic.

## Observability and Debugging

Minimum operational telemetry:
- Correlation IDs in logs and events: `request_id`, `session_id`, and `calc_run_id`.
- Metrics: queue wait time, tool latency per tool, simulation runtime, report generation latency, SSE reconnect count, cancellations, failures.
- Persist a minimal tool audit trail per request (tool name, status, duration, and arguments hash).

## Markdown Rendering Safety

Because the UI renders model-produced markdown, the frontend must prevent script injection:
- Render markdown with HTML disabled, or sanitize HTML strictly.
- Treat tool outputs shown in the UI as untrusted input and escape appropriately.

---

## Security Considerations

### Authentication & Authorization

- JWT or session cookie authentication on all endpoints
- SSE stream validates `request_id` ownership
- Request IDs are UUIDv4 (not guessable)

### Data Protection

- Never expose API keys to frontend
- Log redaction for sensitive plant data
- Per-tenant rate limiting

---

## Deployment Architecture

### Local Development

Docker Compose with all services:
- FastAPI backend
- MCP server
- MongoDB
- Redis (with Streams)
- Calc engine

### Production Deployment

| Service | Scaling Strategy |
|---------|------------------|
| FastAPI backend | Horizontal (stateless) |
| RQ workers | Horizontal (by simulation load) |
| MCP server | Per-worker or pooled |
| Redis | Managed service (with Streams) |
| MongoDB | Managed service |
| Calc engine | Horizontal (compute-intensive) |

### Why Separate Services?

- **Scaling**: Chat requests and simulations have different resource profiles
- **Reliability**: Simulation spikes don't crash UI/API
- **Observability**: Separate logs and metrics per component

---

## Summary

### Key Architectural Decisions

1. **Controlled Agentic Execution**: The LLM proposes tool calls, while the backend enforces eligibility, ordering, and budgets
2. **MCP Tool Discovery**: Tools come from MCP server, Claude auto-discovers them
3. **Tool Policy Layer**: Eligibility filtering, backend gates, and integrity guards provide correctness and safety
4. **Two-Step Flow**: Agentic tools (Step A) + report-only generation (Step B) for anti-hallucination
5. **Typed SSE Protocol**: Enables rich real-time UI without WebSocket complexity
6. **Redis Streams**: Provides replay capability for reconnection
7. **RQ for Jobs**: Simple, reliable background processing for agentic loop
8. **MongoDB for Runs**: Persistent storage for audit and history
9. **Sync Workers + Async API**: Clear separation avoids event loop issues

### Implementation Phases

| Phase | Deliverable |
|-------|-------------|
| 1 | Core SSE infrastructure + event emission |
| 2 | MCP tool discovery + Anthropic format conversion |
| 3 | **Tool registry metadata** (domain/category/risk_level) |
| 4 | **Eligibility filtering** + backend policy gates |
| 5 | RQ worker with agentic loop skeleton |
| 6 | FastAPI endpoints (chat, stream, cancel) |
| 7 | Agentic loop: tool_use handling + policy enforcement |
| 8 | **Two-step flow**: tools (Step A) → report (Step B) |
| 9 | **Tool result summarization** + report integrity guard |
| 10 | React frontend with typed event handlers |
| 11 | Reconnection + state.sync |
| 12 | Error handling + cancellation |
| 13 | Production hardening + deployment |

**Critical Path**: Phases 3-4 (Tool Policy Layer) must be completed before Phase 7 (agentic loop) for production safety.
