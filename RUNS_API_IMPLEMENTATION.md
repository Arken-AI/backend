# Implementation Plan: Run Results API & Frontend Integration

**Scope**: Enable frontend to fetch and display simulation results via `run_id`  
**Supports**: Both `mcp_calculation_engine_server` and `mcp_process_server` (unified API)  
**Last Updated**: 31 January 2026

---

## Overview

### Current State
- Frontend has `/results/:runId` route with `ResultsPage` component
- `ResultsPage` uses mock data from `response.json` via `mockSimulationData.js`
- Both MCP servers store simulation runs in MongoDB (same database, different collections)
- Backend returns `run_ids` array in `ChatResponse` but no API to fetch run data
- No clickable links in chat messages to view results
- No header button to access latest simulation

### Target State
- Backend exposes `GET /api/runs/{runId}` to fetch simulation results from either MCP server
- Frontend `ResultsPage` fetches real data from API
- Chat messages include clickable links to view results
- Header shows "View Flowsheet" button after simulation runs

---

## Database Architecture

Both MCP servers share the **same MongoDB database** (`arken_process_db`) but use different collections:

| MCP Server | Collection | Document Schema | Response Field |
|------------|------------|-----------------|----------------|
| `mcp_calculation_engine_server` | `calc_simulation_runs` | `SimulationRunDocument` | `result` |
| `mcp_process_server` | `runs` | `RunDocument` | `outputs` |

### Schema Comparison

| Field | calc_engine (`calc_simulation_runs`) | process_server (`runs`) |
|-------|-------------------------------------|-------------------------|
| `run_id` | ✅ | ✅ |
| `user_id` | ✅ | ✅ (default: "default_user") |
| `process_id` | ✅ | ✅ |
| `status` | success/failed/error | success/error/pending |
| Response data | `result: Dict` | `outputs: Dict` |
| Request data | `payload_snapshot: Dict` | `inputs: Dict` |
| Execution time | `execution_time_ms: int` | `execution_time_ms: float` |
| `created_at` | ✅ | ✅ |
| `industry` | ❌ (derived from process) | ✅ |
| `version_used` | ✅ (int) | ❌ |
| `run_name` | ✅ (optional) | ❌ |
| `completed_at` | ✅ | ❌ |
| `metadata` | ❌ | ✅ (RunMetadata) |

---

## Phase 1: Backend API - `/api/runs/{runId}`

### Step 1.1: Create Pydantic Models

**File**: `backend/app/models/runs.py` (NEW FILE)

**Add 4 models**:

- `RunSource` - StrEnum for run source
  - `calc_engine = "calc_engine"` - From mcp_calculation_engine_server
  - `process_server = "process_server"` - From mcp_process_server

- `RunResultResponse` - Response model for fetching a single run result
  - `run_id: str` - Unique run identifier
  - `source: RunSource` - Which MCP server stored this run
  - `user_id: Optional[str]` - User who created the run
  - `process_id: Optional[str]` - Process template ID
  - `status: str` - Run status (success, failed, error)
  - `error: Optional[str]` - Error message if status is failed/error
  - `created_at: datetime` - When the run was created
  - `execution_time_ms: Optional[int]` - Execution time in milliseconds
  - `data: Dict[str, Any]` - Full simulation response (normalized)
  - `metadata: Optional[Dict[str, Any]]` - Additional metadata

- `RunListItem` - Summary model for run list items
  - `run_id: str`
  - `source: RunSource`
  - `user_id: Optional[str]`
  - `process_id: Optional[str]`
  - `status: str`
  - `created_at: datetime`
  - `execution_time_ms: Optional[int]`

- `RunListResponse` - Response model for listing runs
  - `runs: List[RunListItem]`
  - `has_more: bool` - Whether more results exist (replaces `total` for efficiency)

---

### Step 1.2: Create Runs Router

**File**: `backend/app/api/runs.py` (NEW FILE)

**Endpoints**:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runs/{run_id}` | GET | Fetch full run results by ID (searches both collections) |
| `/runs` | GET | List runs with pagination (merges both collections) |

**Implementation details for GET /runs/{run_id}**:
1. First query `calc_simulation_runs` collection where run_id matches
2. If not found, query `runs` collection
3. If still not found, return 404
4. Normalize response format and return RunResultResponse

**Implementation details for GET /runs**:
1. If `source` filter provided → query only that collection (efficient)
2. If no `source` filter → query both collections with `limit + 1` each
3. Merge results in memory, sort by `created_at` descending
4. Take first `limit` items, set `has_more = True` if more exist
5. Return RunListResponse

**Note**: For large datasets, recommend using `source` filter. Merged queries work for reasonable sizes (<1000 runs per user).

**MongoDB Query Details**:
- Database: `arken_process_db` (shared by both MCP servers)
- Collections:
  - `calc_simulation_runs` (calc_engine) - Key fields: `run_id`, `result`, `payload_snapshot`
  - `runs` (process_server) - Key fields: `run_id`, `outputs`, `inputs`

**Response Normalization**:
```python
def normalize_run(doc: dict, source: str) -> dict:
    """Normalize run document from either collection."""
    if source == "calc_engine":
        return {
            "run_id": doc["run_id"],
            "source": "calc_engine",
            "user_id": doc.get("user_id"),
            "process_id": doc.get("process_id"),
            "status": doc["status"],
            "error": doc.get("error"),  # Include error message if failed
            "created_at": doc["created_at"],
            "execution_time_ms": doc.get("execution_time_ms"),
            "data": doc.get("result", {}),  # calc_engine uses "result"
            "metadata": {
                "version_used": doc.get("version_used", 0),
                "run_name": doc.get("run_name"),
                "completed_at": doc.get("completed_at"),
                "request": doc.get("payload_snapshot")
            }
        }
    else:  # process_server
        return {
            "run_id": doc["run_id"],
            "source": "process_server",
            "user_id": doc.get("user_id", "default_user"),
            "process_id": doc.get("process_id"),
            "status": doc["status"],
            "error": doc.get("error"),  # Include error message if failed
            "created_at": doc["created_at"],
            "execution_time_ms": int(doc.get("execution_time_ms", 0)) if doc.get("execution_time_ms") else None,
            "data": doc.get("outputs", {}),  # process_server uses "outputs"
            "metadata": {
                "industry": doc.get("industry"),
                "request": doc.get("inputs"),
                "run_metadata": doc.get("metadata")
            }
        }
```

---

### Step 1.3: Register Router and Models

**File**: `backend/app/main.py`

**Changes**:

1. Add import at top: `from app.api import runs`
2. Add router registration: `app.include_router(runs.router, prefix="/api", tags=["runs"])`

**File**: `backend/app/models/__init__.py`

**Changes**:

1. Add export: `from .runs import RunSource, RunResultResponse, RunListItem, RunListResponse`

---

### Step 1.4: MongoDB Connection (Already Configured)

**File**: `backend/app/config.py`

Both MCP servers use the **same database** (`arken_process_db`), so the existing `mongodb_url` setting works for both collections.

**No additional configuration needed** - just access the two collections:
- `db.calc_simulation_runs` - For calc_engine runs
- `db.runs` - For process_server runs

---

## Phase 2: Frontend API Client

### Step 2.1: Add API Function

**File**: `frontend/src/api/client.js`

**Add functions**:

- `getRunResults(runId)` - Fetch simulation run results by ID
  - Calls `GET /api/runs/{runId}`
  - Returns run result with `data` field containing full simulation response
  - Response includes `source` field indicating which MCP server stored it
  - Throws "Run not found" error for 404

- `listRuns(limit, offset, userId, processId, source)` - List simulation runs with pagination
  - Calls `GET /api/runs` with query parameters
  - Optional `source` filter: `"calc_engine"` | `"process_server"` | `null` (both)
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
4. Add import of `transformProcessServerData` (new function, see Step 3.3b)
5. Use `useMemo` to derive `equipmentData` from `apiResponse`:
   ```javascript
   const equipmentData = useMemo(() => {
     if (!apiResponse?.data) return [];
     // Use appropriate transformer based on source
     return apiResponse.source === 'calc_engine'
       ? transformEquipmentData(apiResponse.data)
       : transformProcessServerData(apiResponse.data);
   }, [apiResponse]);
   ```
6. Use `useMemo` to derive `warningsData` from `equipmentData` and `apiResponse` using `getWarningsData()`
7. Return empty arrays/objects when `apiResponse` is null

---

### Step 3.3b: Add Process Server Data Transformer

**File**: `frontend/src/data/mockSimulationData.js`

**Add new function**:

```javascript
/**
 * Transform process_server (sugar industry) outputs to equipment display format.
 * The structure differs from calc_engine flowsheet format.
 */
export function transformProcessServerData(outputs) {
  if (!outputs) return [];
  
  // Process server outputs have industry-specific structure
  // Example for sugar: { mill_results: {...}, evaporator_results: {...}, ... }
  const equipment = [];
  
  // Map each equipment result to display format
  Object.entries(outputs).forEach(([key, value]) => {
    if (typeof value === 'object' && value !== null) {
      equipment.push({
        id: key,
        type: inferEquipmentType(key),  // e.g., 'mill_results' -> 'mill'
        name: formatEquipmentName(key),
        data: value,
        // Add any warnings from the equipment result
        warnings: value.warnings || []
      });
    }
  });
  
  return equipment;
}

function inferEquipmentType(key) {
  // Remove common suffixes to get equipment type
  return key.replace(/_results?$|_output$|_data$/i, '');
}

function formatEquipmentName(key) {
  // Convert snake_case to Title Case
  return key
    .replace(/_results?$|_output$|_data$/i, '')
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
```

**Note**: This transformer may need adjustment based on actual `process_server` output structure. Review the `outputs` field from `mcp_process_server` runs to refine the mapping.

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

**Prerequisites**: Phase 6.1 must be completed first (add `frontend_url` to config)

**Add instruction to system prompt**:

Add guidance for the LLM to include clickable links when simulations complete:

- Import settings: `from app.config import settings`
- When a simulation completes successfully and returns a run_id, always include a clickable link
- Use markdown format: `[View Flowsheet Results]({settings.frontend_url}/results/{run_id})`
- Replace `{run_id}` with the actual run_id from the simulation response
- Example: "Your simulation completed successfully! [View Flowsheet Results](http://localhost:5173/results/run_20260129_143022_abc123)"

**Note**: The `frontend_url` comes from environment config, not hardcoded.

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

**Prerequisites**: Verify that `run_ids` is stored in context state when backend returns it.

**Option A - If run_ids already in context**:
1. Inside ChatProvider component, use `useMemo` to derive `latestRunId`
2. Value should be `currentContext?.run_ids?.[0]` or `null` if no runs exist
3. Dependency array: `[currentContext]`
4. Add `latestRunId` to the context value object

**Option B - If run_ids not in context** (more likely):
1. Add `latestRunId` state: `const [latestRunId, setLatestRunId] = useState(null)`
2. Update `latestRunId` when chat response includes `run_ids` array
3. Add `latestRunId` and `setLatestRunId` to context value object
4. Call `setLatestRunId(response.run_ids[0])` after successful simulation in chat handler

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

- [ ] `GET /api/runs/{run_id}` returns 200 with valid run from `calc_simulation_runs`
- [ ] `GET /api/runs/{run_id}` returns 200 with valid run from `runs` collection
- [ ] `GET /api/runs/{run_id}` returns 404 for non-existent run
- [ ] Response includes correct `source` field (`calc_engine` or `process_server`)
- [ ] Response `data` field is normalized correctly for both sources
- [ ] `GET /api/runs` returns merged list from both collections
- [ ] `GET /api/runs?source=calc_engine` filters correctly
- [ ] `GET /api/runs?source=process_server` filters correctly
- [ ] `GET /api/runs?user_id=xyz` filters correctly
- [ ] Pagination works correctly with merged results

### Frontend Tests

- [ ] ResultsPage shows loading spinner on mount
- [ ] ResultsPage displays error for invalid run_id
- [ ] ResultsPage renders correctly with calc_engine API data
- [ ] ResultsPage renders correctly with process_server API data
- [ ] `transformEquipmentData()` works with both response structures
- [ ] FlowCanvas renders equipment nodes
- [ ] Equipment details panel shows correct data
- [ ] Warnings panel shows warnings

### Integration Tests

- [ ] Run calc_engine simulation via chat → response includes `run_ids`
- [ ] Run process_server simulation via chat → response includes `run_ids`
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
| `backend/app/models/runs.py` | **Create new file** with 4 Pydantic models | 1.1 |
| `backend/app/models/__init__.py` | Add exports for new models | 1.3 |
| `backend/app/api/runs.py` | **Create new file** with router | 1.2 |
| `backend/app/main.py` | Add router import and registration | 1.3 |
| `backend/app/config.py` | Add `frontend_url` setting | 6.1 |
| `frontend/src/api/client.js` | Add `getRunResults()`, `listRuns()` | 2.1 |
| `frontend/src/pages/ResultsPage.jsx` | Replace mock data with API integration | 3.1-3.4 |
| `frontend/src/data/mockSimulationData.js` | Add `transformProcessServerData()` function | 3.3 |
| `backend/app/services/orchestration_service.py` | Update system prompt for links | 4.1 |
| `frontend/src/components/chat/ChatMessage.jsx` | Add custom link handler (if needed) | 4.2 |
| `frontend/src/context/ChatContext.jsx` | Add `latestRunId` state | 5.1 |
| `frontend/src/components/layout/Layout.jsx` | Add header Flowsheet button | 5.2 |

---

## API Response Format

### GET /api/runs/{run_id}

**Response fields** (unified format for both sources):
- `run_id`: Unique run identifier (e.g., "run_20260129_143022_abc123")
- `source`: Which MCP server stored this run (`"calc_engine"` or `"process_server"`)
- `user_id`: User who created the run
- `process_id`: Process template ID (e.g., "benzene_toluene_separation" or "sugar_factory")
- `status`: Run status ("success", "failed", "error")
- `error`: Error message if status is failed/error (null otherwise)
- `created_at`: ISO timestamp
- `execution_time_ms`: Execution duration in milliseconds
- `data`: Full simulation response (normalized):
  - For `calc_engine`: Contains flowsheet results with `node_results`, `stream_results`, etc.
  - For `process_server`: Contains sugar/process-specific outputs
- `metadata`: Source-specific additional info:
  - For `calc_engine`: `{ version_used, run_name, completed_at, request }`
  - For `process_server`: `{ industry, request, run_metadata }`

### GET /api/runs

**Query parameters**:
- `user_id` (optional): Filter by user
- `process_id` (optional): Filter by process
- `source` (optional): `"calc_engine"` | `"process_server"` | omit for both (recommended to specify for performance)
- `limit` (optional): Max results (default: 50)
- `offset` (optional): Pagination offset (default: 0)

**Response fields**:
- `runs`: Array of run summaries, each containing:
  - `run_id`
  - `source`
  - `user_id`
  - `process_id`
  - `status`
  - `created_at`
  - `execution_time_ms`
- `has_more`: Boolean indicating if more results exist (more efficient than total count)

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

1. **Shared Database**: Both MCP servers use the same MongoDB database (`arken_process_db`) but different collections:
   - `calc_simulation_runs` for `mcp_calculation_engine_server` (dynamic flowsheets)
   - `runs` for `mcp_process_server` (sugar/industry-specific processes)

2. **Unified API**: The `/api/runs` endpoint provides a single interface that searches both collections and normalizes the response format. The `source` field identifies which server stored the run.

3. **Response Normalization**: Since the two servers use different field names (`result` vs `outputs`, `payload_snapshot` vs `inputs`), the API normalizes these into a consistent `data` and `metadata` structure.

4. **Frontend URL**: Using `localhost:5173` for development. Update `FRONTEND_URL` environment variable for production deployment. The system prompt should reference `settings.frontend_url`, not hardcoded values.

5. **Data Transformation - IMPORTANT**: 
   - The existing `transformEquipmentData()` function in `mockSimulationData.js` works with `calc_engine` responses (flowsheet format).
   - For `process_server` responses (sugar industry format), you MUST add a `transformProcessServerData()` function.
   - In `ResultsPage.jsx`, check `apiResponse.source` and call the appropriate transformer:
     ```javascript
     const equipmentData = useMemo(() => {
       if (!apiResponse) return [];
       if (apiResponse.source === 'calc_engine') {
         return transformEquipmentData(apiResponse.data);
       } else {
         return transformProcessServerData(apiResponse.data);
       }
     }, [apiResponse]);
     ```

6. **Pagination Strategy**: For efficiency, always use the `source` query parameter when listing runs. Merged queries (without source filter) work for small datasets but are not optimal for production with many runs.

7. **Backward Compatibility**: Mock data files (`response.json`, `mockSimulationData.js`) can remain for development/testing purposes.
