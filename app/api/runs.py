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
    TemplateType,
    FlowsheetResponse
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
            "compound_mapping": doc.get("compound_mapping"),  # Generic → real compound mapping
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


# =============================================================================
# Flowsheet Helpers
# =============================================================================

MAX_CHAIN_DEPTH = 20


def _get_result_inner(data: dict) -> dict:
    """
    Navigate into the result portion of a run's data.
    
    Handles both nested ``{result: {node_results, ...}}`` and flat
    ``{node_results, ...}`` structures returned by the calc engine.
    """
    result = data.get("result", {})
    if isinstance(result, dict) and "node_results" not in result and "result" in result:
        return result.get("result", {})
    return result


def _get_input(data: dict) -> dict:
    """Get the input portion of a run's data."""
    return data.get("input", {})


def _merge_flowsheet(ordered_runs: list, warnings: list) -> dict:
    """
    Merge multiple ordered runs into a single unified flowsheet data object.
    
    Args:
        ordered_runs: List of (run_id, doc) tuples in root→leaf order
        warnings: List to append warning messages to
    
    Returns:
        Merged data dict with same shape as a single run's data:
        { input: { feed_streams, equipment, edges }, 
          result: { node_results, stream_results, equipment_inputs, execution_order } }
    """
    merged_feed_streams = []
    merged_equipment = []
    merged_edges = []
    merged_node_results = {}
    merged_stream_results = {}
    merged_equipment_inputs = {}
    merged_execution_order = []
    equipment_id_set = set()
    run_map = {}

    # Index runs by run_id for chain_metadata lookups
    run_by_id = {run_id: doc for run_id, doc in ordered_runs}

    # Track per-run renames: (run_id, original_id) → final_id
    # Needed so synthetic edges can map source_equipment_id to its final name
    global_equip_renames = {}

    for idx, (run_id, doc) in enumerate(ordered_runs):
        data = doc.get("result", {})
        inp = _get_input(data)
        res = _get_result_inner(data)

        chain_meta = doc.get("chain_metadata")
        is_root = (idx == 0)

        # --- Phase 1: Detect equipment ID collisions, build rename map ---
        rename_map = {}
        for equip in inp.get("equipment", []):
            equip_id = equip.get("id", f"equip_{idx}")
            if equip_id in equipment_id_set:
                new_id = f"{equip_id}_{idx}"
                rename_map[equip_id] = new_id
                warnings.append(
                    f"Equipment ID collision: '{equip_id}' renamed to '{new_id}' "
                    f"(run {run_id})"
                )

        def _rename(eid):
            """Resolve equipment ID through this run's rename map."""
            return rename_map.get(eid, eid)

        # --- Phase 2: Merge equipment (with renames) ---
        for equip in inp.get("equipment", []):
            original_id = equip.get("id", f"equip_{idx}")
            final_id = _rename(original_id)
            if final_id != original_id:
                equip = {**equip, "id": final_id}
            equipment_id_set.add(final_id)
            merged_equipment.append(equip)
            run_map[final_id] = run_id
            global_equip_renames[(run_id, original_id)] = final_id

        # --- Feed streams (update target_equipment if renamed) ---
        if is_root:
            for feed in inp.get("feed_streams", []):
                target = feed.get("target_equipment")
                if target and target in rename_map:
                    feed = {**feed, "target_equipment": rename_map[target]}
                merged_feed_streams.append(feed)
        else:
            for feed in inp.get("feed_streams", []):
                stream_id = feed.get("stream_id", "")
                if not stream_id.startswith("chained_feed_"):
                    target = feed.get("target_equipment")
                    if target and target in rename_map:
                        feed = {**feed, "target_equipment": rename_map[target]}
                    merged_feed_streams.append(feed)

        # --- Internal edges (apply renames to source/target) ---
        for edge in inp.get("edges", []):
            if rename_map:
                edge = {**edge}
                if edge.get("source") in rename_map:
                    edge["source"] = rename_map[edge["source"]]
                if edge.get("target") in rename_map:
                    edge["target"] = rename_map[edge["target"]]
            merged_edges.append(edge)

        # --- Node results (apply renames to keys) ---
        for nid, nval in res.get("node_results", {}).items():
            merged_node_results[_rename(nid)] = nval

        # --- Stream results ---
        merged_stream_results.update(res.get("stream_results", {}))

        # --- Equipment inputs (apply renames to keys) ---
        for eid, einp in res.get("equipment_inputs", {}).items():
            merged_equipment_inputs[_rename(eid)] = einp

        # --- Execution order (apply renames, skip pseudo-nodes like FEED/PRODUCT) ---
        for eid in res.get("execution_order", []):
            renamed_eid = _rename(eid)
            if renamed_eid not in merged_execution_order:
                merged_execution_order.append(renamed_eid)

        # --- Synthetic edge for chain connections ---
        if chain_meta and not is_root:
            source_run_id = chain_meta.get("source_run_id")
            source_equipment_id = chain_meta.get("source_equipment_id")
            source_port = chain_meta.get("source_port")
            extracted_stream = chain_meta.get("extracted_stream", {})

            if source_run_id and source_run_id in run_by_id:
                source_doc = run_by_id[source_run_id]
                source_data = source_doc.get("result", {})
                source_res = _get_result_inner(source_data)

                # Resolve source equipment's final name (may have been renamed
                # in a previous iteration due to collision)
                source_equip_final = global_equip_renames.get(
                    (source_run_id, source_equipment_id), source_equipment_id
                )

                # Get the outlet stream_id from the source equipment's node_results
                source_node = source_res.get("node_results", {}).get(source_equipment_id, {})
                outlet = source_node.get("outlets", {}).get(source_port, {})
                outlet_stream_id = outlet.get(
                    "stream_id",
                    f"{source_equipment_id}_{source_port}"
                )

                # Identify target equipment and chained feed stream ID
                target_equip_id = None
                chained_feed_stream_id = None
                chained_feed_target_port = None

                for feed in inp.get("feed_streams", []):
                    sid = feed.get("stream_id", "")
                    if sid.startswith("chained_feed_"):
                        chained_feed_stream_id = sid
                        target_equip_id = feed.get("target_equipment")
                        chained_feed_target_port = feed.get("target_port")
                        break

                if not target_equip_id:
                    equip_list = inp.get("equipment", [])
                    target_equip_id = equip_list[0].get("id") if equip_list else f"unknown_{idx}"

                # Apply rename to target equipment ID
                target_equip_id = _rename(target_equip_id)

                # Create synthetic edge connecting upstream outlet → downstream inlet
                synthetic_edge = {
                    "id": outlet_stream_id,
                    "source": source_equip_final,
                    "source_port": source_port,
                    "target": target_equip_id,
                    "target_port": chained_feed_target_port or "feed_inlet",
                    "is_recycle": False,
                }
                merged_edges.append(synthetic_edge)

                # Add stream_results entry for the synthetic edge stream
                if outlet_stream_id not in merged_stream_results:
                    merged_stream_results[outlet_stream_id] = {
                        **extracted_stream,
                        "stream_id": outlet_stream_id,
                        "name": f"{source_equip_final} {source_port}",
                    }

                # Update downstream equipment's inlet_ports to reference
                # the synthetic edge ID instead of the chained_feed_* ID
                if chained_feed_stream_id and target_equip_id in merged_equipment_inputs:
                    equip_input = merged_equipment_inputs[target_equip_id]
                    inlet_ports = equip_input.get("inlet_ports", [])
                    equip_input["inlet_ports"] = [
                        outlet_stream_id if p == chained_feed_stream_id else p
                        for p in inlet_ports
                    ]

    # Preserve any extra top-level keys from the root run's data (e.g., metadata)
    root_data = ordered_runs[0][1].get("result", {}) if ordered_runs else {}
    merged_data = {**root_data}
    merged_data["input"] = {
        "feed_streams": merged_feed_streams,
        "equipment": merged_equipment,
        "edges": merged_edges,
    }
    # Preserve any extra keys from root input (e.g., compounds)
    root_input = _get_input(root_data)
    for key in root_input:
        if key not in ("feed_streams", "equipment", "edges"):
            merged_data["input"][key] = root_input[key]

    merged_data["result"] = {
        "node_results": merged_node_results,
        "stream_results": merged_stream_results,
        "equipment_inputs": merged_equipment_inputs,
        "execution_order": merged_execution_order,
    }
    # Preserve any extra keys from root result
    root_result = _get_result_inner(root_data)
    for key in root_result:
        if key not in ("node_results", "stream_results", "equipment_inputs", "execution_order"):
            merged_data["result"][key] = root_result[key]

    return merged_data, run_map


@router.get(
    "/runs/{run_id}/flowsheet",
    response_model=FlowsheetResponse,
    summary="Get Unified Flowsheet",
    description=(
        "Returns a unified flowsheet merging all chained runs. "
        "Walks chain_metadata upward to find the root, then downstream_runs "
        "downward (BFS) to collect every run in the chain graph. "
        "Merges equipment, edges, feed_streams, and results into one object "
        "that the frontend can render directly."
    ),
    responses={
        200: {"description": "Unified flowsheet", "model": FlowsheetResponse},
        404: {"description": "Run ID not found"},
    },
)
async def get_run_flowsheet(
    run_id: str,
    mongo_client: MongoClient = Depends(get_mongo_client),
) -> FlowsheetResponse:
    """
    Get unified flowsheet for a run, merging all chained runs in
    the chain graph.
    
    Algorithm:
      1. Load the requested run (leaf).
      2. Walk UP via chain_metadata.source_run_id to find the root.
      3. From the root, walk DOWN via downstream_runs[] (BFS) to
         collect every run in the flowsheet.
      4. Merge all runs' data into a single flowsheet.
      5. Build run_map (equipment_id → run_id) for the detail panel.
    
    For standalone (unchained) runs, returns the same data wrapped
    in FlowsheetResponse.
    """
    try:
        db = mongo_client._client[mongo_client.database_name]
        collection = db.calc_simulation_runs
        warnings = []

        # ── Step 1: Load the requested run ────────────────────────────
        leaf_doc = await collection.find_one({"run_id": run_id}, {"_id": 0})
        if not leaf_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run with ID '{run_id}' not found",
            )

        # ── Step 2: Walk UP to find root ──────────────────────────────
        visited = {run_id}
        chain_to_root = [leaf_doc]
        current = leaf_doc
        depth = 0

        while depth < MAX_CHAIN_DEPTH:
            cm = current.get("chain_metadata")
            if not cm:
                break  # current is the root

            source_run_id = cm.get("source_run_id") if isinstance(cm, dict) else getattr(cm, "source_run_id", None)
            if not source_run_id or source_run_id in visited:
                if source_run_id in visited:
                    warnings.append(f"Circular chain detected at run '{source_run_id}'. Stopping walk.")
                break

            visited.add(source_run_id)
            parent_doc = await collection.find_one({"run_id": source_run_id}, {"_id": 0})
            if not parent_doc:
                warnings.append(
                    f"Upstream run '{source_run_id}' not found (deleted?). "
                    "Partial flowsheet returned."
                )
                break

            chain_to_root.append(parent_doc)
            current = parent_doc
            depth += 1

        if depth >= MAX_CHAIN_DEPTH:
            warnings.append(f"Chain depth limit ({MAX_CHAIN_DEPTH}) reached. Flowsheet may be incomplete.")

        # Root is the last element; reverse to get root-first order
        chain_to_root.reverse()
        root_doc = chain_to_root[0]
        root_run_id = root_doc["run_id"]

        # ── Step 3: Walk DOWN from root via downstream_runs (BFS) ─────
        # This discovers branches the user hasn't navigated to yet
        all_docs_map = {doc["run_id"]: doc for doc in chain_to_root}
        bfs_queue = [root_run_id]
        bfs_visited = {root_run_id}
        ordered_run_ids = [root_run_id]

        while bfs_queue:
            current_id = bfs_queue.pop(0)
            doc = all_docs_map.get(current_id)
            if not doc:
                doc = await collection.find_one({"run_id": current_id}, {"_id": 0})
                if not doc:
                    warnings.append(f"Downstream run '{current_id}' not found (deleted?).")
                    continue
                all_docs_map[current_id] = doc

            downstream = doc.get("downstream_runs") or []
            for ref in downstream:
                ds_run_id = ref.get("run_id") if isinstance(ref, dict) else None
                if ds_run_id and ds_run_id not in bfs_visited:
                    if len(bfs_visited) >= MAX_CHAIN_DEPTH:
                        warnings.append(f"Chain breadth limit ({MAX_CHAIN_DEPTH}) reached.")
                        break
                    bfs_visited.add(ds_run_id)
                    ordered_run_ids.append(ds_run_id)
                    bfs_queue.append(ds_run_id)

        # Ensure the requested run is always included
        if run_id not in bfs_visited:
            ordered_run_ids.append(run_id)

        # Build ordered list of (run_id, doc)
        ordered_runs = []
        for rid in ordered_run_ids:
            doc = all_docs_map.get(rid)
            if not doc:
                doc = await collection.find_one({"run_id": rid}, {"_id": 0})
                if doc:
                    all_docs_map[rid] = doc
            if doc:
                ordered_runs.append((rid, doc))

        # ── Step 4: Merge ─────────────────────────────────────────────
        merged_data, run_map = _merge_flowsheet(ordered_runs, warnings)

        # ── Step 5: Build response ────────────────────────────────────
        # Determine overall status
        statuses = [doc.get("status", "unknown") for _, doc in ordered_runs]
        if all(s == "success" for s in statuses):
            overall_status = "success"
        elif any(s == "error" for s in statuses):
            overall_status = "error"
        else:
            overall_status = statuses[-1] if statuses else "unknown"

        # Get chain_metadata from the requested run (for banner)
        leaf_chain_meta = leaf_doc.get("chain_metadata")

        # Resolve source + process_id + template_type from leaf doc
        # (same logic as normalize_run — calc_engine vs process_server)
        leaf_source = "calc_engine" if leaf_doc.get("process_id") or leaf_doc.get("template_type") or "calc_simulation_runs" in str(collection.name) else "process_server"
        leaf_process_id = leaf_doc.get("process_id")
        leaf_template_type = leaf_doc.get("template_type")
        # Resolve template_type from templates collection if missing
        if not leaf_template_type and leaf_process_id and leaf_source == "calc_engine":
            tmpl = await db.calc_process_templates.find_one(
                {"process_id": leaf_process_id}, {"template_type": 1}
            )
            if tmpl:
                leaf_template_type = tmpl.get("template_type", "process")

        # Lookup conversation_id — check any run in the chain
        conversation = await db.conversations.find_one(
            {"run_ids": {"$in": [rid for rid, _ in ordered_runs]}},
            {"conversation_id": 1},
        )
        conversation_id = conversation["conversation_id"] if conversation else None

        # Resolve compound_mapping from the leaf run document
        leaf_compound_mapping = leaf_doc.get("compound_mapping")

        return FlowsheetResponse(
            run_id=run_id,
            root_run_id=root_run_id,
            all_run_ids=[rid for rid, _ in ordered_runs],
            run_map=run_map,
            status=overall_status,
            data=merged_data,
            chain_metadata=leaf_chain_meta,
            conversation_id=conversation_id,
            process_id=leaf_process_id,
            source=leaf_source,
            template_type=leaf_template_type,
            compound_mapping=leaf_compound_mapping,
            warnings=warnings,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building flowsheet for run {run_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build flowsheet: {str(e)}",
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
