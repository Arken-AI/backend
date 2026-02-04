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
    components: List[str] = Field(..., description="List of component names")
    data: Dict[str, List[Any]] = Field(..., description="Data organized by row type (temperature, pressure, etc.)")
    units: Dict[str, str] = Field(..., description="Units for each row type")
    
    class Config:
        json_schema_extra = {
            "example": {
                "stream_ids": ["1", "2", "3", "4"],
                "components": ["H2O", "Ethanol"],
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
