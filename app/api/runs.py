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
    RunStatus,
    TemplateType
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
            "template_type": doc.get("template_type"),
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
            },
            "chain_metadata": doc.get("chain_metadata")
        }
    else:  # process_server
        return {
            "run_id": doc["run_id"],
            "source": "process_server",
            "user_id": doc.get("user_id", "default_user"),
            "process_id": doc.get("process_id"),
            "template_type": None,
            "status": doc["status"],
            "error": doc.get("error"),
            "created_at": doc["created_at"],
            "execution_time_ms": int(doc.get("execution_time_ms", 0)) if doc.get("execution_time_ms") else None,
            "data": doc.get("outputs", {}),  # process_server uses "outputs" field
            "metadata": {
                "industry": doc.get("industry"),
                "request": doc.get("inputs"),
                "run_metadata": doc.get("metadata")
            },
            "chain_metadata": None
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
            "template_type": doc.get("template_type"),
            "status": doc["status"],
            "created_at": doc["created_at"],
            "execution_time_ms": doc.get("execution_time_ms"),
            "has_chain_metadata": doc.get("chain_metadata") is not None
        }
    else:  # process_server
        return {
            "run_id": doc["run_id"],
            "source": "process_server",
            "user_id": doc.get("user_id", "default_user"),
            "process_id": doc.get("process_id"),
            "template_type": None,
            "status": doc["status"],
            "created_at": doc["created_at"],
            "execution_time_ms": int(doc.get("execution_time_ms", 0)) if doc.get("execution_time_ms") else None,
            "has_chain_metadata": False
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
        db = mongo_client._client[mongo_client.database_name]
        
        # Try calc_engine collection first
        doc = await db.calc_simulation_runs.find_one({"run_id": run_id})
        source = None
        if doc:
            normalized = normalize_run(doc, "calc_engine")
            source = "calc_engine"
            
            # Resolve template_type from templates collection if not on run doc
            if not normalized.get("template_type") and doc.get("process_id"):
                tmpl = await db.calc_process_templates.find_one(
                    {"process_id": doc["process_id"]},
                    {"template_type": 1}
                )
                if tmpl:
                    normalized["template_type"] = tmpl.get("template_type", "process")
        else:
            # Try process_server collection
            doc = await db.runs.find_one({"run_id": run_id})
            if doc:
                normalized = normalize_run(doc, "process_server")
                source = "process_server"
        
        # Not found in either collection
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run with ID '{run_id}' not found"
            )
        
        # Lookup conversation_id from conversations collection
        conversation = await db.conversations.find_one(
            {"run_ids": run_id},
            {"conversation_id": 1}
        )
        normalized["conversation_id"] = conversation["conversation_id"] if conversation else None
        
        return RunResultResponse(**normalized)
        
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
    template_type: Optional[TemplateType] = Query(
        None,
        description="Filter by template type ('process' or 'single_equipment'). "
                    "Only applies to calc_engine runs. If set, process_server runs are excluded."
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
    - If template_type filter provided: Only query calc_engine collection,
      using a two-step lookup through calc_process_templates to get matching process_ids
    
    Performance notes:
    - For large datasets, use the source filter
    - Merged queries work well for <1000 runs per user
    - Uses limit+1 technique to detect if more results exist
    
    Args:
        source: Filter by run source (optional but recommended)
        user_id: Filter by user ID (optional)
        process_id: Filter by process ID (optional)
        template_type: Filter by template type (optional, calc_engine only)
        status: Filter by run status (optional)
        limit: Max results to return (default 20, max 100)
        mongo_client: Injected MongoDB client
    
    Returns:
        RunListResponse with list of runs and has_more flag
    """
    try:
        db = mongo_client._client[mongo_client.database_name]
        
        # Build filter query
        filter_query = {}
        if user_id:
            filter_query["user_id"] = user_id
        if process_id:
            filter_query["process_id"] = process_id
        if status:
            filter_query["status"] = status.value
        
        # If template_type is specified, resolve matching process_ids from templates
        # and force source to calc_engine (process_server runs don't have template_type)
        template_type_process_ids = None
        if template_type:
            template_filter = {"template_type": template_type.value}
            template_cursor = db.calc_process_templates.find(
                template_filter,
                {"process_id": 1}
            )
            template_type_process_ids = []
            async for tmpl in template_cursor:
                pid = tmpl.get("process_id")
                if pid:
                    template_type_process_ids.append(pid)
            
            # If a specific process_id was also given, intersect
            if process_id:
                if process_id not in template_type_process_ids:
                    # No match — return empty
                    return RunListResponse(runs=[], has_more=False)
                # process_id is already in filter_query
            else:
                if not template_type_process_ids:
                    return RunListResponse(runs=[], has_more=False)
                filter_query["process_id"] = {"$in": template_type_process_ids}
        
        all_runs = []
        
        # When template_type is set, skip process_server collection
        query_calc = (source is None or source == RunSource.CALC_ENGINE)
        query_process = (source is None or source == RunSource.PROCESS_SERVER) and template_type is None
        
        # Query calc_engine collection
        if query_calc:
            cursor = db.calc_simulation_runs.find(
                filter_query,
                {
                    "run_id": 1,
                    "user_id": 1,
                    "process_id": 1,
                    "template_type": 1,
                    "status": 1,
                    "created_at": 1,
                    "execution_time_ms": 1,
                    "chain_metadata": 1
                }
            ).sort("created_at", DESCENDING).limit(limit + 1)
            
            async for doc in cursor:
                all_runs.append(normalize_run_summary(doc, "calc_engine"))
        
        # Query process_server collection (skipped when template_type filter is active)
        if query_process:
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
        
        # Batch lookup conversation_ids for all runs
        run_ids = [run["run_id"] for run in runs_to_return]
        conversations_cursor = db.conversations.find(
            {"run_ids": {"$in": run_ids}},
            {"conversation_id": 1, "run_ids": 1}
        )
        
        # Build lookup map: run_id -> conversation_id
        conversation_map = {}
        async for conv in conversations_cursor:
            conv_id = conv.get("conversation_id")
            for rid in conv.get("run_ids", []):
                if rid in run_ids:
                    conversation_map[rid] = conv_id
        
        # Add conversation_id to each run
        for run in runs_to_return:
            run["conversation_id"] = conversation_map.get(run["run_id"])
        
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
