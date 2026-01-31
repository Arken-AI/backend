# MCP Calculation Engine Server Integration Plan

## Overview

Integrate `mcp_calculation_engine_server` into the backend alongside the existing `mcp_process_server`.

---

## Phase 1: Backend Configuration

**Goal**: Add settings for the new MCP server

1. Add environment variables for `mcp_calculation_engine_server`:
   - `MCP_CALC_ENGINE_COMMAND` (how to start the server)
   - `MCP_CALC_ENGINE_CWD` (directory path)
   - `MCP_CALC_ENGINE_ENABLED` (on/off toggle)

2. Update `config.py` to read these new variables

3. Update `.env.example` with the new variables

---

## Phase 2: MCP Client Updates

**Goal**: Support connecting to two MCP servers

4. Modify `mcp_client.py` to accept a server name/identifier parameter

5. Create a factory or registry pattern to manage multiple MCP clients:
   - `process_server` client
   - `calc_engine` client

6. Add connection health check for both servers

---

## Phase 3: Orchestration Service Updates

**Goal**: Route tool calls to the correct server

7. Initialize both MCP clients on startup (in `orchestration_service.py`)

8. Merge tool lists from both servers when LLM asks for available tools

9. Add routing logic in `execute_tool()`:
   - If tool name starts with `calc_` → use calc_engine client
   - Otherwise → use process_server client

10. Handle the case when one server is down but other is available

---

## Phase 4: Policy Engine Updates

**Goal**: Teach policy engine about new tools

11. Add calc engine tools to the known tools list

12. Define tool prerequisites for calc engine:
    - `calc_simulate_process` requires `validate_parameters` first
    - `edit_parameters` requires `get_editable_parameters` first

13. Update intent detection to recognize calc engine queries:
    - "ethanol distillation" → calc_engine
    - "sugar factory" → process_server

---

## Phase 5: Context Manager Updates

**Goal**: Track which server is being used in conversation

14. Add `active_server` field to conversation context

15. Track run_ids separately by server:
    ```
    run_ids: {
      process: [...],
      calc_engine: [...]
    }
    ```

16. Store `server` field from tool responses in context

---

## Phase 6: Runs API Implementation

**Goal**: Unified API to query runs from both servers

17. Create `GET /api/runs` endpoint:
    - Query both MongoDB collections
    - Merge results sorted by timestamp
    - Return with `server` field indicating source

18. Create `GET /api/runs/{run_id}` endpoint:
    - Check `run_id` format or query both collections
    - Return full run details with source

19. Create `GET /api/conversations/{id}/runs` endpoint:
    - Get all runs for a specific conversation
    - Query both collections by `conversation_id`

---

## Phase 7: Testing

**Goal**: Verify everything works together

20. Unit tests for dual MCP client initialization

21. Unit tests for tool routing logic

22. Integration test: Call process_server tool → verify correct routing

23. Integration test: Call calc_engine tool → verify correct routing

24. Integration test: List tools → verify merged list

25. Integration test: Runs API → verify unified results

---

## Phase 8: Documentation & Cleanup

**Goal**: Production readiness

26. Update `README.md` with dual-server setup instructions

27. Update `ENV_VARIABLES.md` with new configuration

28. Update `architecture-mcpChatInterface.md` with dual-server diagram

29. Add logging for server routing decisions

30. Add metrics/monitoring for each server's health

---

## Execution Order

| Step | Description | Depends On |
|------|-------------|------------|
| 1-3 | Configuration | Nothing |
| 4-6 | MCP Client | Steps 1-3 |
| 7-10 | Orchestration | Steps 4-6 |
| 11-13 | Policy Engine | Steps 7-10 |
| 14-16 | Context Manager | Steps 7-10 |
| 17-19 | Runs API | Steps 7-10 |
| 20-25 | Testing | All above |
| 26-30 | Documentation | All above |

---

## Estimated Effort

| Phase | Complexity | Time Estimate |
|-------|------------|---------------|
| Phase 1 | Low | 30 mins |
| Phase 2 | Medium | 1-2 hours |
| Phase 3 | Medium | 1-2 hours |
| Phase 4 | Low | 1 hour |
| Phase 5 | Low | 1 hour |
| Phase 6 | Medium | 2 hours |
| Phase 7 | Medium | 2-3 hours |
| Phase 8 | Low | 1 hour |

**Total: ~10-12 hours of work**
