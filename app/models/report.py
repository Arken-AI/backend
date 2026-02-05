"""
Report Generation Models

Data models for the detailed report generation feature.
These models define the structure for report requests, responses, and internal data.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field


# =============================================================================
# ENUMS
# =============================================================================

class ReportStatus(str, Enum):
    """Status of report generation process"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class ReportOptions(BaseModel):
    """Options for report generation - which sections to include"""
    include_executive_summary: bool = True
    include_process_description: bool = True
    include_stream_tables: bool = True
    include_equipment_tables: bool = True
    include_mass_balance: bool = True
    include_energy_balance: bool = True
    include_observations: bool = True
    include_ai_narratives: bool = True  # Enable AI-generated content (exec summary, observations)


class ReportRequest(BaseModel):
    """Request model for generating a detailed report"""
    run_id: str = Field(..., description="ID of the simulation run to generate report for")
    pfd_image_base64: str = Field(..., description="Base64 encoded PNG image of the PFD")
    options: Optional[ReportOptions] = Field(default_factory=ReportOptions, description="Report generation options")


class ReportResponse(BaseModel):
    """Response model after initiating report generation"""
    report_id: str = Field(..., description="Unique identifier for this report")
    status: ReportStatus = Field(..., description="Current status of the report")
    created_at: datetime = Field(..., description="Timestamp when report generation started")
    estimated_time_seconds: Optional[int] = Field(None, description="Estimated time to completion in seconds")


class ReportStatusResponse(BaseModel):
    """Response model for checking report status"""
    report_id: str = Field(..., description="Unique identifier for this report")
    status: ReportStatus = Field(..., description="Current status of the report")
    progress: int = Field(..., description="Progress percentage (0-100)")
    current_step: str = Field(..., description="Current processing step")
    download_url: Optional[str] = Field(None, description="URL to download the completed report")
    created_at: datetime = Field(..., description="Timestamp when report generation started")
    completed_at: Optional[datetime] = Field(None, description="Timestamp when report generation completed")
    error: Optional[str] = Field(None, description="Error message if generation failed")


# =============================================================================
# INTERNAL DATA MODELS
# =============================================================================

class ReportMetadata(BaseModel):
    """Metadata for the report"""
    title: str = Field(default="Process Simulation Report", description="Report title")
    process_name: str = Field(..., description="Name of the process being simulated")
    date_generated: datetime = Field(default_factory=datetime.utcnow, description="Date report was generated")
    run_id: str = Field(..., description="ID of the simulation run")
    author: Optional[str] = Field(default="ARKEN AI", description="Report author/generator")
    version: str = Field(default="1.0", description="Report version")


class StreamTableData(BaseModel):
    """Formatted stream data for table generation"""
    stream_ids: List[str] = Field(..., description="List of stream IDs in order")
    stream_names: Optional[List[str]] = Field(default=None, description="Display names for streams")
    components: List[str] = Field(..., description="List of component names")
    component_names: Optional[List[str]] = Field(default=None, description="Display names for components")
    data: Dict[str, List[Any]] = Field(..., description="Data organized by row type (temperature, pressure, etc.)")
    units: Dict[str, str] = Field(..., description="Units for each row type")
    
    def get_stream_display_name(self, index: int) -> str:
        """Get display name for stream at index, falling back to ID."""
        if self.stream_names and index < len(self.stream_names):
            return self.stream_names[index]
        if index < len(self.stream_ids):
            return self.stream_ids[index]
        return f"Stream {index + 1}"
    
    def get_component_display_name(self, index: int) -> str:
        """Get display name for component at index, falling back to raw name."""
        if self.component_names and index < len(self.component_names):
            return self.component_names[index]
        if index < len(self.components):
            return self.components[index]
        return f"Component {index + 1}"
    
    class Config:
        json_schema_extra = {
            "example": {
                "stream_ids": ["1", "2", "3", "4"],
                "stream_names": ["Feed", "Distillate", "Bottoms", "Product"],
                "components": ["H2O", "Ethanol"],
                "component_names": ["Water", "Ethanol"],
                "data": {
                    "temperature": [30.0, 78.0, 100.0, 35.0],
                    "pressure": [1.0, 1.0, 1.0, 1.0],
                    "mass_flow": [1000, 150, 850, 150],
                    "H2O": [900, 10.5, 841.5, 10.5],
                    "Ethanol": [100, 139.5, 8.5, 139.5]
                },
                "units": {
                    "temperature": "°C",
                    "pressure": "bar",
                    "mass_flow": "kg/hr"
                }
            }
        }


class EquipmentTableData(BaseModel):
    """Formatted equipment data for table generation"""
    equipment_ids: List[str] = Field(..., description="List of equipment IDs")
    equipment_names: List[str] = Field(..., description="List of equipment names")
    equipment_types: List[str] = Field(..., description="List of equipment types")
    data: Dict[str, List[Any]] = Field(..., description="Data organized by column (duty, efficiency, etc.)")
    units: Dict[str, str] = Field(..., description="Units for each data type")
    
    class Config:
        json_schema_extra = {
            "example": {
                "equipment_ids": ["E1", "E2"],
                "equipment_names": ["Ethanol Column", "Distillate Cooler"],
                "equipment_types": ["Distillation", "Heat Exchanger"],
                "data": {
                    "duty": [500.0, 50.0],
                    "efficiency": [85.0, 95.0]
                },
                "units": {
                    "duty": "kW",
                    "efficiency": "%"
                }
            }
        }


class MassBalanceSummary(BaseModel):
    """Mass balance summary data"""
    total_mass_in: float = Field(..., description="Total mass input (kg/hr)")
    total_mass_out: float = Field(..., description="Total mass output (kg/hr)")
    closure_percentage: float = Field(..., description="Mass balance closure percentage")
    component_balances: Optional[Dict[str, Dict[str, float]]] = Field(None, description="Per-component balances")


class EnergyBalanceSummary(BaseModel):
    """Energy balance summary data"""
    total_heat_input: float = Field(..., description="Total heat input (kW)")
    total_heat_output: float = Field(..., description="Total heat output (kW)")
    total_power: float = Field(0.0, description="Total power consumption (kW)")
    closure_percentage: float = Field(..., description="Energy balance closure percentage")


class ReportSection(BaseModel):
    """A section of the report with title and content"""
    section_number: str = Field(..., description="Section number (e.g., '1', '2.1')")
    title: str = Field(..., description="Section title")
    content: str = Field(..., description="Section content (plain text or markdown)")
    section_type: str = Field(..., description="Type of section (text, table, image)")


class ReportData(BaseModel):
    """Complete data package for report generation"""
    metadata: ReportMetadata
    pfd_image_base64: str
    stream_table: Optional[StreamTableData] = None
    equipment_table: Optional[EquipmentTableData] = None
    mass_balance: Optional[MassBalanceSummary] = None
    energy_balance: Optional[EnergyBalanceSummary] = None
    executive_summary: Optional[str] = None  # AI-generated (Phase 2)
    process_description_sections: Optional[List[ReportSection]] = None  # AI-generated (Phase 2)
    observations: Optional[str] = None  # AI-generated (Phase 2)


class SimulationRunData(BaseModel):
    """Simulation run data extracted from MongoDB"""
    run_id: str
    process_name: str
    process_type: str
    created_at: datetime
    equipment_list: List[Dict[str, Any]]
    streams: List[Dict[str, Any]]
    input_parameters: Dict[str, Any]
    calculation_results: Dict[str, Any]
    # Additional fields for AI narrative generation
    industry: Optional[str] = None  # e.g., "Sugar", "Chemical", etc.
    status: Optional[str] = "completed"  # Run status
    timestamp: Optional[datetime] = None  # Alias for created_at, for narrative templates
    
    def __init__(self, **data):
        super().__init__(**data)
        # Set timestamp to created_at if not provided
        if self.timestamp is None:
            object.__setattr__(self, 'timestamp', self.created_at)
