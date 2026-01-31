"""
Runs API Endpoints

REST API endpoints for accessing stored simulation results:
- GET /runs/{run_id} - Fetch full run results by ID
- GET /runs - List runs with pagination

These endpoints query two MongoDB collections:
- calc_simulation_runs (from mcp_calculation_engine_server)
- runs (from mcp_process_server)

Both collections now use a unified flowsheet format, simplifying data normalization.
"""

import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Query, status
from pymongo import DESCENDING

from app.models.runs import (
    RunResultResponse,
    RunListResponse,
    RunListItem,
    RunSource,
    RunStatus
)
from app.dependencies import get_mongo_client
from app.core.mongo_client import MongoClient

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================

def normalize_run(doc: dict, source: str) -> dict:
    """
    Normalize run document from either collection.
    
    Args:
        doc: MongoDB document from either calc_simulation_runs or runs collection
        source: "calc_engine" or "process_server"
    
    Returns:
        Normalized dictionary matching RunResultResponse schema
    """
    if source == "calc_engine":
        return {
            "run_id": doc["run_id"],
            "source": "calc_engine",
            "user_id": doc.get("user_id"),
            "process_id": doc.get("process_id"),
            "status": doc["status"],
            "error": doc.get("error"),
            "created_at": doc["created_at"],
            "execution_time_ms": doc.get("execution_time_ms"),
            "data": doc.get("result", {}),  # calc_engine uses "result" field
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
            "error": doc.get("error"),
            "created_at": doc["created_at"],
            "execution_time_ms": int(doc.get("execution_time_ms", 0)) if doc.get("execution_time_ms") else None,
            "data": doc.get("outputs", {}),  # process_server uses "outputs" field
            "metadata": {
                "industry": doc.get("industry"),
                "request": doc.get("inputs"),
                "run_metadata": doc.get("metadata")
            }
        }


def normalize_run_summary(doc: dict, source: str) -> dict:
    """
    Normalize run document for list view (excludes large data field).
    
    Args:
        doc: MongoDB document from either collection
        source: "calc_engine" or "process_server"
    
    Returns:
        Normalized dictionary matching RunListItem schema
    """
    if source == "calc_engine":
        return {
            "run_id": doc["run_id"],
            "source": "calc_engine",
            "user_id": doc.get("user_id"),
            "process_id": doc.get("process_id"),
            "status": doc["status"],
            "created_at": doc["created_at"],
            "execution_time_ms": doc.get("execution_time_ms")
        }
    else:  # process_server
        return {
            "run_id": doc["run_id"],
            "source": "process_server",
            "user_id": doc.get("user_id", "default_user"),
            "process_id": doc.get("process_id"),
            "status": doc["status"],
            "created_at": doc["created_at"],
            "execution_time_ms": int(doc.get("execution_time_ms", 0)) if doc.get("execution_time_ms") else None
        }


# =============================================================================
# API Endpoints
# =============================================================================

@router.get(
    "/runs/{run_id}",
    response_model=RunResultResponse,
    summary="Get Run Results",
    description="Fetch full simulation results by run ID. Searches both calc_engine and process_server collections.",
    responses={
        200: {
            "description": "Run results found and returned",
            "model": RunResultResponse
        },
        404: {
            "description": "Run ID not found in either collection"
        }
    }
)
async def get_run_results(
    run_id: str,
    mongo_client: MongoClient = Depends(get_mongo_client)
) -> RunResultResponse:
    """
    Get full simulation results for a specific run.
    
    Query strategy:
    1. First check calc_simulation_runs collection
    2. If not found, check runs collection
    3. Return 404 if not found in either
    
    Args:
        run_id: Unique run identifier
        mongo_client: Injected MongoDB client
    
    Returns:
        RunResultResponse with full simulation data
    
    Raises:
        HTTPException: 404 if run_id not found
    """
    try:
        db = mongo_client.db
        
        # Try calc_engine collection first
        doc = await db.calc_simulation_runs.find_one({"run_id": run_id})
        if doc:
            normalized = normalize_run(doc, "calc_engine")
            return RunResultResponse(**normalized)
        
        # Try process_server collection
        doc = await db.runs.find_one({"run_id": run_id})
        if doc:
            normalized = normalize_run(doc, "process_server")
            return RunResultResponse(**normalized)
        
        # Not found in either collection
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching run {run_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch run results: {str(e)}"
        )


@router.get(
    "/runs",
    response_model=RunListResponse,
    summary="List Runs",
    description="List simulation runs with pagination. Can filter by source, user, process, or status.",
    responses={
        200: {
            "description": "List of runs matching filters",
            "model": RunListResponse
        }
    }
)
async def list_runs(
    source: Optional[RunSource] = Query(
        None,
        description="Filter by run source (calc_engine or process_server). Recommended for performance."
    ),
    user_id: Optional[str] = Query(
        None,
        description="Filter by user ID"
    ),
    process_id: Optional[str] = Query(
        None,
        description="Filter by process ID (e.g., 'sugar', 'ethanol')"
    ),
    status: Optional[RunStatus] = Query(
        None,
        description="Filter by run status"
    ),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Maximum number of runs to return (1-100)"
    ),
    mongo_client: MongoClient = Depends(get_mongo_client)
) -> RunListResponse:
    """
    List simulation runs with optional filters and pagination.
    
    Query strategy:
    - If source filter provided: Query only that collection (efficient)
    - If no source filter: Query both collections, merge, and sort in memory
    
    Performance notes:
    - For large datasets, use the source filter
    - Merged queries work well for <1000 runs per user
    - Uses limit+1 technique to detect if more results exist
    
    Args:
        source: Filter by run source (optional but recommended)
        user_id: Filter by user ID (optional)
        process_id: Filter by process ID (optional)
        status: Filter by run status (optional)
        limit: Max results to return (default 20, max 100)
        mongo_client: Injected MongoDB client
    
    Returns:
        RunListResponse with list of runs and has_more flag
    """
    try:
        db = mongo_client.db
        
        # Build filter query
        filter_query = {}
        if user_id:
            filter_query["user_id"] = user_id
        if process_id:
            filter_query["process_id"] = process_id
        if status:
            filter_query["status"] = status.value
        
        all_runs = []
        
        # Query calc_engine collection
        if source is None or source == RunSource.CALC_ENGINE:
            cursor = db.calc_simulation_runs.find(
                filter_query,
                {
                    "run_id": 1,
                    "user_id": 1,
                    "process_id": 1,
                    "status": 1,
                    "created_at": 1,
                    "execution_time_ms": 1
                }
            ).sort("created_at", DESCENDING).limit(limit + 1)
            
            async for doc in cursor:
                all_runs.append(normalize_run_summary(doc, "calc_engine"))
        
        # Query process_server collection
        if source is None or source == RunSource.PROCESS_SERVER:
            cursor = db.runs.find(
                filter_query,
                {
                    "run_id": 1,
                    "user_id": 1,
                    "process_id": 1,
                    "status": 1,
                    "created_at": 1,
                    "execution_time_ms": 1
                }
            ).sort("created_at", DESCENDING).limit(limit + 1)
            
            async for doc in cursor:
                all_runs.append(normalize_run_summary(doc, "process_server"))
        
        # Sort merged results by created_at (descending)
        all_runs.sort(key=lambda x: x["created_at"], reverse=True)
        
        # Determine if more results exist
        has_more = len(all_runs) > limit
        
        # Take only the requested limit
        runs_to_return = all_runs[:limit]
        
        # Convert to RunListItem models
        run_items = [RunListItem(**run) for run in runs_to_return]
        
        return RunListResponse(
            runs=run_items,
            has_more=has_more
        )
        
    except Exception as e:
        logger.error(f"Error listing runs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list runs: {str(e)}"
        )
