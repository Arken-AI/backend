# Implementation Plan: Run Results API & Frontend Integration

**Scope**: Enable frontend to fetch and display simulation results via `run_id`  
**Focus**: `mcp_dynamic_server` only (ignoring `mcp_process_server`)  
**Last Updated**: 29 January 2026

---

## Overview

### Current State
- Frontend has `/results/:runId` route with `ResultsPage` component
- `ResultsPage` uses mock data from `response.json` via `mockSimulationData.js`
- `mcp_dynamic_server` stores full simulation responses in MongoDB (`response_payload` field)
- Backend returns `run_ids` array in `ChatResponse` but no API to fetch run data
- No clickable links in chat messages to view results
- No header button to access latest simulation

### Target State
- Backend exposes `GET /api/runs/{runId}` to fetch simulation results
- Frontend `ResultsPage` fetches real data from API
- Chat messages include clickable links to view results
- Header shows "View Flowsheet" button after simulation runs

---

## Phase 1: Backend API - `/api/runs/{runId}`

### Step 1.1: Create Pydantic Models

**File**: `backend/app/models/requests.py`

**Add models**:

- `RunResultResponse` - Response model for fetching a single run result
  - `run_id: str` - Unique run identifier
  - `process_id: Optional[str]` - Process template ID
  - `status: str` - Run status (success, failed, timeout)
  - `created_at: datetime` - When the run was created
  - `execution_time_s: Optional[float]` - Execution time in seconds
  - `data: Dict[str, Any]` - Full simulation response (response_payload)
  - `metadata: Optional[Dict[str, Any]]` - Additional metadata

- `RunListItem` - Summary model for run list items
  - `run_id: str`
  - `process_id: Optional[str]`
  - `status: str`
  - `created_at: datetime`
  - `execution_time_s: Optional[float]`
  - `equipment_count: Optional[int]`

- `RunListResponse` - Response model for listing runs
  - `runs: List[RunListItem]`
  - `total: int`

---

### Step 1.2: Create Runs Router

**File**: `backend/app/api/runs.py` (NEW FILE)

**Endpoints**:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runs/{run_id}` | GET | Fetch full run results by ID |
| `/runs` | GET | List runs with pagination |

**Implementation details for GET /runs/{run_id}**:
1. Query MongoDB `runs` collection where run_id matches
2. If not found, return 404
3. Return RunResultResponse with data = doc["response_payload"]

**Implementation details for GET /runs**:
1. Query MongoDB with optional process_id filter
2. Sort by created_at descending (newest first)
3. Apply pagination (skip/limit)
4. Return RunListResponse

**MongoDB Query Details**:
- Database: `arken_dynamic_db` (same as mcp_dynamic_server)
- Collection: `runs`
- Key fields:
  - `run_id`: Unique identifier
  - `response_payload`: Full simulation response (this becomes `data` in API response)
  - `request_payload`: Original request
  - `status`: success/failed/timeout
  - `created_at`: Timestamp
  - `process_id`: Process template ID
  - `execution_time_s`: Duration

---

### Step 1.3: Register Router in Main App

**File**: `backend/app/main.py`

**Changes**:

1. Add import at top: `from app.api import runs`
2. Add router registration: `app.include_router(runs.router, prefix="/api", tags=["runs"])`

---

### Step 1.4: Verify MongoDB Connection

**File**: `backend/app/config.py`

**Verify/Add**:
- Ensure backend can connect to `mcp_dynamic_server`'s MongoDB
- May need to add `DYNAMIC_MONGODB_URI` if different from main MongoDB
- Database name: `arken_dynamic_db`

**Check existing config**:
- Existing setting: `mongodb_url` (used for conversations)
- May need to add: `dynamic_mongodb_url` if mcp_dynamic_server uses different DB

---

## Phase 2: Frontend API Client

### Step 2.1: Add API Function

**File**: `frontend/src/api/client.js`

**Add functions**:

- `getRunResults(runId)` - Fetch simulation run results by ID
  - Calls `GET /api/runs/{runId}`
  - Returns run result with `data` field containing full simulation response
  - Throws "Run not found" error for 404

- `listRuns(limit, offset, processId)` - List simulation runs with pagination
  - Calls `GET /api/runs` with query parameters
  - Returns `{ runs: [], total: number }`

---

## Phase 3: ResultsPage Integration

### Step 3.1: Add State Variables

**File**: `frontend/src/pages/ResultsPage.jsx`

**Add state variables**:

- `apiResponse` - null initially, stores the simulation response
- `loading` - true initially, tracks fetch status
- `error` - null initially, stores error message if fetch fails

---

### Step 3.2: Add Data Fetching Effect

**File**: `frontend/src/pages/ResultsPage.jsx`

**Add useEffect for data fetching**:

1. On mount (and when `runId` changes), call `getRunResults(runId)`
2. Set `loading=true` at start
3. On success: set `apiResponse` to `result.data`, set `loading=false`
4. On error: set `error` to error message, set `loading=false`
5. Use cleanup function to prevent state updates on unmounted component

---

### Step 3.3: Replace Mock Data Usage

**File**: `frontend/src/pages/ResultsPage.jsx`

**Changes**:

1. Remove import of `mockEquipmentData` and `mockWarningsData`
2. Keep import of `transformEquipmentData` and `getWarningsData` functions
3. Add import of `getRunResults` from API client
4. Use `useMemo` to derive `equipmentData` from `apiResponse` using `transformEquipmentData()`
5. Use `useMemo` to derive `warningsData` from `equipmentData` and `apiResponse` using `getWarningsData()`
6. Return empty arrays/objects when `apiResponse` is null

---

### Step 3.4: Add Loading and Error UI

**File**: `frontend/src/pages/ResultsPage.jsx`

**Add conditional rendering**:

1. **Loading state** (`loading === true`):
   - Show centered spinner with "Loading simulation results..." text

2. **Error state** (`error !== null`):
   - Show error icon and message
   - Different message for "Run not found" vs other errors
   - Include "Back to Chat" link

3. **No data state** (`apiResponse === null` after loading):
   - Show "No simulation data available" message

4. **Normal render**:
   - Use derived `equipmentData` and `warningsData` in existing JSX

---

## Phase 4: Clickable Links in Chat

### Step 4.1: Update System Prompt

**File**: `backend/app/services/orchestration_service.py`

**Location**: `_build_system_prompt()` method or system prompt construction

**Add instruction to system prompt**:

Add guidance for the LLM to include clickable links when simulations complete:

- When a simulation completes successfully and returns a run_id, always include a clickable link
- Use markdown format: `[View Flowsheet Results](http://localhost:5173/results/{run_id})`
- Replace `{run_id}` with the actual run_id from the simulation response
- Example: "Your simulation completed successfully! [View Flowsheet Results](http://localhost:5173/results/run_20260129_143022_abc123)"

---

### Step 4.2: Verify Markdown Link Rendering

**File**: `frontend/src/components/chat/ChatMessage.jsx` (or equivalent)

**Check**:
1. Markdown renderer parses links correctly
2. Links are clickable and styled appropriately

**If using react-markdown**:
- Create a custom link component that checks if link is internal (starts with `/` or contains `localhost:5173`)
- For internal links: extract pathname and use React Router's `Link` component
- For external links: use standard `<a>` tag with `target="_blank"`
- Pass custom component to ReactMarkdown via `components={{ a: CustomLink }}`

---

## Phase 5: Header Flowsheet Button

### Step 5.1: Add latestRunId to Context

**File**: `frontend/src/context/ChatContext.jsx`

**Add derived value**:

1. Inside ChatProvider component, use `useMemo` to derive `latestRunId`
2. Value should be `currentContext?.run_ids?.[0]` or `null` if no runs exist
3. Dependency array: `[currentContext]`
4. Add `latestRunId` to the context value object

---

### Step 5.2: Add Button to Header/Layout

**File**: `frontend/src/components/layout/Layout.jsx` or `Header.jsx`

**Add button**:

1. Import `useChatContext` and React Router's `Link`
2. Get `latestRunId` from context
3. Conditionally render "View Flowsheet" button when `latestRunId` exists
4. Link destination: `/results/${latestRunId}`
5. Style: Button with flowsheet icon and "View Flowsheet" text

**Button behavior**:
- Only visible when `latestRunId` exists (after first simulation)
- Always links to the most recent simulation
- Updates automatically when new simulations run

---

## Phase 6: Configuration

### Step 6.1: Add Frontend URL Config

**File**: `backend/app/config.py`

**Add setting**:

1. In `Settings` class, add `frontend_url` field
2. Default value: `"http://localhost:5173"`
3. Description: "Frontend URL for generating result links"

**Environment variable**: Add `FRONTEND_URL=http://localhost:5173` to `.env` file

**Usage**: Import settings in orchestration_service.py and use `settings.frontend_url` when constructing result links in system prompt

---

## Testing Checklist

### Backend Tests

- [ ] `GET /api/runs/{run_id}` returns 200 with valid run
- [ ] `GET /api/runs/{run_id}` returns 404 for non-existent run
- [ ] Response `data` field contains full simulation response matching `response.json` structure
- [ ] `GET /api/runs` returns paginated list
- [ ] `GET /api/runs?process_id=xyz` filters correctly
- [ ] MongoDB connection to `mcp_dynamic_server` database works

### Frontend Tests

- [ ] ResultsPage shows loading spinner on mount
- [ ] ResultsPage displays error for invalid run_id
- [ ] ResultsPage renders correctly with API data
- [ ] `transformEquipmentData()` works with API response structure
- [ ] FlowCanvas renders equipment nodes
- [ ] Equipment details panel shows correct data
- [ ] Warnings panel shows warnings

### Integration Tests

- [ ] Run simulation via chat → response includes `run_ids`
- [ ] Chat message contains clickable link with correct run_id
- [ ] Clicking link navigates to `/results/{runId}`
- [ ] ResultsPage loads correct simulation data
- [ ] Header "View Flowsheet" button appears after simulation
- [ ] Header button links to latest run_id
- [ ] Multiple simulations → header button updates to latest

---

## File Changes Summary

| File | Action | Phase |
|------|--------|-------|
| `backend/app/models/requests.py` | Add 3 new Pydantic models | 1.1 |
| `backend/app/api/runs.py` | **Create new file** | 1.2 |
| `backend/app/main.py` | Add router import and registration | 1.3 |
| `backend/app/config.py` | Add `frontend_url` setting | 6.1 |
| `frontend/src/api/client.js` | Add `getRunResults()`, `listRuns()` | 2.1 |
| `frontend/src/pages/ResultsPage.jsx` | Replace mock data with API integration | 3.1-3.4 |
| `backend/app/services/orchestration_service.py` | Update system prompt for links | 4.1 |
| `frontend/src/components/chat/ChatMessage.jsx` | Add custom link handler (if needed) | 4.2 |
| `frontend/src/context/ChatContext.jsx` | Add `latestRunId` derived value | 5.1 |
| `frontend/src/components/layout/Layout.jsx` | Add header Flowsheet button | 5.2 |

---

## API Response Format

### GET /api/runs/{run_id}

**Response fields**:
- `run_id`: Unique run identifier (e.g., "run_20260129_143022_abc123")
- `process_id`: Process template ID (e.g., "benzene_toluene_separation")
- `status`: Run status ("success", "failed", "timeout")
- `created_at`: ISO timestamp
- `execution_time_s`: Execution duration in seconds
- `data`: Full simulation response containing:
  - `status`: Simulation status
  - `flowsheet_id`: Flowsheet identifier
  - `input`: Original input (name, compounds, property_package, feed_streams, equipment, edges, solver_options)
  - `result`: Simulation results (converged, iterations, execution_order, node_results, stream_results, equipment_inputs)
- `metadata`: Additional info (compounds, equipment_count, edge_count)

### GET /api/runs

**Response fields**:
- `runs`: Array of run summaries, each containing:
  - `run_id`
  - `process_id`
  - `status`
  - `created_at`
  - `execution_time_s`
  - `equipment_count`
- `total`: Total count of runs (for pagination)

---

## Estimated Effort

| Phase | Tasks | Effort |
|-------|-------|--------|
| Phase 1 | Backend API | 1-2 hours |
| Phase 2 | Frontend API Client | 30 mins |
| Phase 3 | ResultsPage Integration | 1-2 hours |
| Phase 4 | Chat Links | 1 hour |
| Phase 5 | Header Button | 30 mins |
| Phase 6 | Configuration | 15 mins |
| **Total** | | **4-6 hours** |

---

## Dependencies

**No new packages required**

Backend:
- FastAPI (existing)
- Motor - MongoDB async client (existing)
- Pydantic (existing)

Frontend:
- react-router-dom (existing)
- react-markdown (existing)

---

## Notes

1. **MongoDB Access**: Backend queries `mcp_dynamic_server`'s MongoDB directly for performance. This couples backend to MCP schema but avoids round-trip through MCP tool calls.

2. **Frontend URL**: Using `localhost:5173` for development. Update `FRONTEND_URL` environment variable for production deployment.

3. **Data Transformation**: The existing `transformEquipmentData()` function in `mockSimulationData.js` works with the API response structure because `mcp_dynamic_server` stores the full `response_payload`.

4. **Backward Compatibility**: Mock data files (`response.json`, `mockSimulationData.js`) can remain for development/testing purposes.
