"""
Runs API Models

Pydantic models for the runs API that provides access to stored simulation results.
These models normalize data from two MongoDB collections:
- calc_simulation_runs (from mcp_calculation_engine_server)
- runs (from mcp_process_server)

Both collections now use a unified response format with:
- input.equipment[] - Equipment configuration
- result.node_results{} - Node-level results
- result.stream_results{} - Stream-level results
- result.equipment_inputs{} - Equipment-level inputs
"""

from datetime import datetime
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class RunSource(str, Enum):
    """Source of the simulation run"""
    CALC_ENGINE = "calc_engine"
    PROCESS_SERVER = "process_server"


class RunStatus(str, Enum):
    """Status of the simulation run"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


class TemplateType(str, Enum):
    """Type of process template"""
    PROCESS = "process"
    SINGLE_EQUIPMENT = "single_equipment"


class RunResultResponse(BaseModel):
    """
    Complete run result with full simulation data.
    
    This model normalizes data from both collections into a unified format.
    The `data` field contains the full simulation response in the unified
    flowsheet format.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    run_id: str = Field(description="Unique identifier for the run")
    conversation_id: Optional[str] = Field(default=None, description="Conversation that created this run")
    source: RunSource = Field(description="Which MCP server created this run")
    user_id: Optional[str] = Field(default=None, description="User who initiated the run")
    process_id: Optional[str] = Field(default=None, description="Process ID (sugar, ethanol, etc.)")
    template_type: Optional[str] = Field(
        default=None,
        description="Template type: 'process' or 'single_equipment'. Only set for calc_engine runs."
    )
    status: RunStatus = Field(description="Current status of the run")
    error: Optional[str] = Field(default=None, description="Error message if status is failed/error")
    created_at: datetime = Field(description="When the run was created")
    execution_time_ms: Optional[int] = Field(default=None, description="Execution time in milliseconds")
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Full simulation response in unified flowsheet format (input, result, metadata)"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional metadata (version, run_name, industry, etc.)"
    )
    chain_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Chain provenance metadata for equipment-chained runs. "
                    "Contains source_run_id, source_equipment_id, source_port, extracted_stream."
    )


class RunListItem(BaseModel):
    """
    Summary model for run list items.
    
    Contains only essential fields for displaying run lists in the UI.
    Excludes the large `data` field for performance.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    run_id: str = Field(description="Unique identifier for the run")
    conversation_id: Optional[str] = Field(default=None, description="Conversation that created this run")
    source: RunSource = Field(description="Which MCP server created this run")
    user_id: Optional[str] = Field(default=None, description="User who initiated the run")
    process_id: Optional[str] = Field(default=None, description="Process ID (sugar, ethanol, etc.)")
    template_type: Optional[str] = Field(
        default=None,
        description="Template type: 'process' or 'single_equipment'. Only set for calc_engine runs."
    )
    status: RunStatus = Field(description="Current status of the run")
    created_at: datetime = Field(description="When the run was created")
    execution_time_ms: Optional[int] = Field(default=None, description="Execution time in milliseconds")
    has_chain_metadata: bool = Field(
        default=False,
        description="Whether this run has chain provenance metadata (is a chained run)"
    )


class FlowsheetResponse(BaseModel):
    """
    Unified flowsheet merging all chained runs.
    
    Walks the chain graph (upstream via chain_metadata, downstream via downstream_runs)
    and merges equipment, edges, feed_streams, and results into a single flowsheet
    that the frontend can render without knowing about individual runs.
    
    The `data` field has the exact same shape as RunResultResponse.data so that
    transformEquipmentData and useFlowLayout work without changes.
    """
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(description="The requested (leaf) run ID")
    root_run_id: str = Field(description="The root run of the chain (no chain_metadata)")
    all_run_ids: List[str] = Field(description="All run IDs in this flowsheet, root-first order")
    run_map: Dict[str, str] = Field(
        description="Mapping of equipment_id → run_id so the frontend knows "
                    "which run each equipment belongs to (for detail panel)"
    )
    status: str = Field(description="Overall status (success if all runs succeeded)")
    data: Dict[str, Any] = Field(
        description="Merged flowsheet data in unified format: "
                    "{ input: { feed_streams, equipment, edges }, "
                    "result: { node_results, stream_results, equipment_inputs, execution_order } }"
    )
    chain_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Chain metadata of the requested run (for banner display)"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Warnings encountered during chain traversal or merge"
    )


class RunListResponse(BaseModel):
    """
    Response model for listing runs.
    
    Uses cursor-based pagination with `has_more` flag instead of total count
    for better performance with large datasets.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    runs: List[RunListItem] = Field(default_factory=list, description="List of run summaries")
    has_more: bool = Field(
        default=False,
        description="Whether more results exist beyond the current limit"
    )
