"""
Tool metadata models for MCP tool registry.

This module defines the schema for storing metadata about each MCP tool,
including input/output schemas, domain classification, risk levels, and prerequisites.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ToolMetadata(BaseModel):
    """
    Metadata schema for an MCP tool.
    
    This model captures all necessary information about a tool for intelligent
    filtering, policy enforcement, and frontend display.
    
    Attributes:
        name: Unique identifier for the tool (e.g., "list_industries")
        description: Human-readable description of what the tool does
        input_schema: JSON schema defining required/optional parameters
        output_schema: JSON schema defining the expected return structure
        domain: High-level domain classification ("discovery", "sugar", "generic")
        category: Specific category within domain ("discovery", "validate", "simulate", "lookup", "compare")
        prerequisites: List of tool names that must be executed successfully before this tool
        risk_level: Risk classification ("safe" or "gated" - gated tools require validation)
        equipment_types: Optional list of equipment types this tool applies to (for equipment-specific tools)
    """
    
    name: str = Field(
        ...,
        description="Unique tool identifier matching MCP tool name",
        examples=["list_industries", "simulate_process"]
    )
    
    description: str = Field(
        ...,
        description="Human-readable description of tool functionality",
        examples=["Lists all available industries in the system"]
    )
    
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON schema for tool input parameters (type, properties, required fields)",
        examples=[{
            "type": "object",
            "properties": {
                "industry": {"type": "string", "description": "Industry name"}
            },
            "required": ["industry"]
        }]
    )
    
    output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON schema for tool output structure",
        examples=[{
            "type": "array",
            "items": {"type": "string"},
            "description": "List of process names"
        }]
    )
    
    domain: str = Field(
        ...,
        description="High-level domain classification",
        pattern="^(discovery|sugar|generic)$",
        examples=["discovery", "sugar", "generic"]
    )
    
    category: str = Field(
        ...,
        description="Specific category within domain",
        pattern="^(discovery|validate|simulate|lookup|compare)$",
        examples=["discovery", "validate", "simulate"]
    )
    
    prerequisites: List[str] = Field(
        default_factory=list,
        description="List of tool names that must run successfully before this tool",
        examples=[["validate_process_inputs"], []]
    )
    
    risk_level: str = Field(
        ...,
        description="Risk classification for policy enforcement",
        pattern="^(safe|gated)$",
        examples=["safe", "gated"]
    )
    
    equipment_types: Optional[List[str]] = Field(
        default=None,
        description="Equipment types this tool applies to (None means applies to all)",
        examples=[["mill", "heater"], None]
    )
    
    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "name": "simulate_process",
                "description": "Simulates a complete sugar manufacturing process with all equipment",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string"},
                        "process": {"type": "string"},
                        "inputs": {"type": "object"}
                    },
                    "required": ["industry", "process", "inputs"]
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "calc_run_id": {"type": "string"},
                        "outputs": {"type": "object"}
                    }
                },
                "domain": "sugar",
                "category": "simulate",
                "prerequisites": ["validate_process_inputs"],
                "risk_level": "gated",
                "equipment_types": None
            }
        }
    
    def has_prerequisites(self) -> bool:
        """Check if this tool has any prerequisites."""
        return len(self.prerequisites) > 0
    
    def is_safe(self) -> bool:
        """Check if this tool is safe (not gated)."""
        return self.risk_level == "safe"
    
    def is_gated(self) -> bool:
        """Check if this tool is gated and requires validation."""
        return self.risk_level == "gated"
    
    def applies_to_equipment(self, equipment_type: str) -> bool:
        """
        Check if this tool applies to a specific equipment type.
        
        Args:
            equipment_type: Equipment type to check (e.g., "mill", "heater")
            
        Returns:
            True if tool applies to this equipment type or all equipment (equipment_types is None)
        """
        if self.equipment_types is None:
            return True  # Applies to all equipment
        return equipment_type in self.equipment_types
