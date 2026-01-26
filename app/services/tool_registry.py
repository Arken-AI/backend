"""
Tool Registry Service

Centralized registry for managing MCP tool metadata with intelligent filtering capabilities.
Provides methods to register, query, and filter tools based on domain, category, risk level, and equipment type.
"""

from typing import List, Optional, Dict
from app.models.tool_metadata import ToolMetadata


class ToolRegistry:
    """
    Centralized registry for MCP tools with metadata and filtering capabilities.
    
    The ToolRegistry maintains a collection of all available MCP tools with their metadata,
    enabling intelligent filtering and policy enforcement. It supports querying tools by:
    - Domain (discovery, sugar, generic)
    - Category (discovery, validate, simulate, lookup, compare)
    - Risk level (safe, gated)
    - Equipment type (for equipment-specific tools)
    
    This registry is used by the chat backend to determine which tools to offer to Claude
    based on the current conversation context and workflow state.
    """
    
    def __init__(self):
        """Initialize an empty tool registry."""
        self._tools: Dict[str, ToolMetadata] = {}
    
    def register_tool(self, tool: ToolMetadata) -> None:
        """
        Register a tool in the registry.
        
        Args:
            tool: ToolMetadata instance to register
            
        Raises:
            ValueError: If a tool with the same name already exists
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        
        self._tools[tool.name] = tool
    
    def get_all_tools(self) -> List[ToolMetadata]:
        """
        Get all registered tools.
        
        Returns:
            List of all ToolMetadata instances in the registry
        """
        return list(self._tools.values())
    
    def get_tool(self, name: str) -> Optional[ToolMetadata]:
        """
        Get a specific tool by name.
        
        Args:
            name: Tool name to retrieve
            
        Returns:
            ToolMetadata instance if found, None otherwise
        """
        return self._tools.get(name)
    
    def filter_by_domain(self, domain: str) -> List[ToolMetadata]:
        """
        Filter tools by domain.
        
        Args:
            domain: Domain to filter by ("discovery", "sugar", "generic")
            
        Returns:
            List of tools matching the specified domain
        """
        return [tool for tool in self._tools.values() if tool.domain == domain]
    
    def filter_by_category(self, category: str) -> List[ToolMetadata]:
        """
        Filter tools by category.
        
        Args:
            category: Category to filter by ("discovery", "validate", "simulate", "lookup", "compare")
            
        Returns:
            List of tools matching the specified category
        """
        return [tool for tool in self._tools.values() if tool.category == category]
    
    def filter_by_risk(self, risk_level: str) -> List[ToolMetadata]:
        """
        Filter tools by risk level.
        
        Args:
            risk_level: Risk level to filter by ("safe" or "gated")
            
        Returns:
            List of tools matching the specified risk level
        """
        return [tool for tool in self._tools.values() if tool.risk_level == risk_level]
    
    def filter_by_equipment(self, equipment_type: str) -> List[ToolMetadata]:
        """
        Filter tools by equipment type.
        
        Returns tools that either:
        1. Have equipment_types=None (apply to all equipment)
        2. Have the specified equipment_type in their equipment_types list
        
        Args:
            equipment_type: Equipment type to filter by (e.g., "mill", "heater")
            
        Returns:
            List of tools that apply to the specified equipment type
        """
        return [
            tool for tool in self._tools.values()
            if tool.applies_to_equipment(equipment_type)
        ]
    
    def filter_by_server(self, server: str) -> List[ToolMetadata]:
        """
        Filter tools by source MCP server.
        
        Args:
            server: Server to filter by ("process" or "dynamic")
            
        Returns:
            List of tools from the specified server
        """
        return [
            tool for tool in self._tools.values()
            if tool.source_server == server
        ]
    
    def get_process_server_tools(self) -> List[ToolMetadata]:
        """
        Get all tools from the MCP Process Server.
        
        Returns:
            List of tools with source_server="process"
        """
        return self.filter_by_server("process")
    
    def get_dynamic_server_tools(self) -> List[ToolMetadata]:
        """
        Get all tools from the MCP Dynamic Server.
        
        Returns:
            List of tools with source_server="dynamic"
        """
        return self.filter_by_server("dynamic")
    
    def get_safe_tools(self) -> List[ToolMetadata]:
        """
        Get all safe (non-gated) tools.
        
        Returns:
            List of tools with risk_level="safe"
        """
        return self.filter_by_risk("safe")
    
    def get_gated_tools(self) -> List[ToolMetadata]:
        """
        Get all gated tools that require validation.
        
        Returns:
            List of tools with risk_level="gated"
        """
        return self.filter_by_risk("gated")
    
    def get_tools_with_prerequisites(self) -> List[ToolMetadata]:
        """
        Get all tools that have prerequisites.
        
        Returns:
            List of tools that have at least one prerequisite
        """
        return [tool for tool in self._tools.values() if tool.has_prerequisites()]
    
    def get_prerequisite_chain(self, tool_name: str) -> List[str]:
        """
        Get the full prerequisite chain for a tool.
        
        This returns all tools that must be executed before the specified tool,
        including transitive dependencies.
        
        Args:
            tool_name: Name of the tool to get prerequisites for
            
        Returns:
            List of tool names in execution order (prerequisites first)
            
        Raises:
            ValueError: If tool not found or circular dependency detected
        """
        tool = self.get_tool(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' not found in registry")
        
        chain = []
        visited = set()
        
        def _traverse(name: str):
            if name in visited:
                raise ValueError(f"Circular dependency detected for tool '{name}'")
            
            visited.add(name)
            current_tool = self.get_tool(name)
            
            if current_tool is None:
                raise ValueError(f"Prerequisite tool '{name}' not found in registry")
            
            # Recursively get prerequisites
            for prereq in current_tool.prerequisites:
                if prereq not in chain:
                    _traverse(prereq)
            
            # Add current tool to chain if not already present
            if name not in chain and name != tool_name:
                chain.append(name)
        
        # Build the chain
        for prereq in tool.prerequisites:
            _traverse(prereq)
        
        return chain
    
    def count(self) -> int:
        """
        Get the total number of registered tools.
        
        Returns:
            Number of tools in the registry
        """
        return len(self._tools)
    
    def clear(self) -> None:
        """Clear all tools from the registry."""
        self._tools.clear()
    
    def __repr__(self) -> str:
        """String representation of the registry."""
        return f"<ToolRegistry: {self.count()} tools registered>"
    
    def load_default_tools(self) -> None:
        """
        Load all default MCP tools with their metadata.
        
        This method registers tools from both MCP servers with complete
        metadata including domains, categories, risk levels, prerequisites, and source_server.
        
        Process Server Tools (Sugar Industry):
        - Discovery (6): list_industries, list_processes, get_process, get_equipment_types,
                        get_process_equipment_schema, get_stream_schema
        - Validation (3): validate_process_inputs, validate_process_connections, validate_equipment_inputs
        - Simulation (2): simulate_process, simulate_equipment
        - Run Tools (3): get_process_run, compare_process_runs, list_process_runs
        
        Dynamic Server Tools (Generic Simulations):
        - Discovery (3): list_dynamic_processes, get_process_template, get_dynamic_equipment_schema
        - Validation (5): validate_compounds, validate_equipment_params, validate_feed_streams,
                         validate_dynamic_connections, validate_flowsheet
        - Building (1): build_flowsheet
        - Simulation (1): simulate_dynamic
        - Run Tools (3): get_dynamic_run, compare_dynamic_runs, list_dynamic_runs
        """
        
        # ========================================
        # PROCESS SERVER - Discovery Tools (6)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="list_industries",
            description="List all available industries in the system",
            input_schema={
                "type": "object",
                "properties": {},
                "required": []
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "industries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "industry_id": {"type": "string"},
                                "name": {"type": "string"},
                                "description": {"type": "string"}
                            }
                        }
                    },
                    "count": {"type": "integer"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="list_processes",
            description="List all processes available for a specific industry",
            input_schema={
                "type": "object",
                "properties": {
                    "industry_id": {
                        "type": "string",
                        "description": "Industry identifier (e.g., 'sugar', 'ethanol')"
                    }
                },
                "required": ["industry_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "industry": {"type": "string"},
                    "processes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "process_id": {"type": "string"},
                                "display_name": {"type": "string"},
                                "description": {"type": "string"},
                                "version": {"type": "string"}
                            }
                        }
                    },
                    "count": {"type": "integer"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="get_process",
            description="Get detailed information about a specific process including equipment sequence",
            input_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "Process identifier (e.g., 'sugar_factory')"
                    },
                    "version": {
                        "type": "string",
                        "description": "Process version (optional, defaults to latest)"
                    }
                },
                "required": ["process_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "process_id": {"type": "string"},
                    "version": {"type": "string"},
                    "display_name": {"type": "string"},
                    "industry": {"type": "string"},
                    "description": {"type": "string"},
                    "sequence": {"type": "array"},
                    "equipment_count": {"type": "integer"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="get_equipment_types",
            description="List all equipment types available for an industry",
            input_schema={
                "type": "object",
                "properties": {
                    "industry_id": {
                        "type": "string",
                        "description": "Industry identifier (optional, defaults to 'sugar')"
                    }
                },
                "required": []
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "industry": {"type": "string"},
                    "equipment_types": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "equipment_type": {"type": "string"},
                                "display_name": {"type": "string"},
                                "description": {"type": "string"},
                                "version": {"type": "string"}
                            }
                        }
                    },
                    "count": {"type": "integer"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="get_process_equipment_schema",
            description="Get parameter schema and requirements for a specific equipment type (Process Server)",
            input_schema={
                "type": "object",
                "properties": {
                    "equipment_type": {
                        "type": "string",
                        "description": "Equipment type identifier (e.g., 'mill', 'heater')"
                    },
                    "version": {
                        "type": "string",
                        "description": "Schema version (optional, defaults to latest)"
                    }
                },
                "required": ["equipment_type"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "equipment_type": {"type": "string"},
                    "display_name": {"type": "string"},
                    "version": {"type": "string"},
                    "description": {"type": "string"},
                    "parameters_schema": {"type": "object"},
                    "inlet_stream_types": {"type": "array"},
                    "outlet_stream_types": {"type": "array"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="get_stream_schema",
            description="Get property schema for a specific stream type",
            input_schema={
                "type": "object",
                "properties": {
                    "stream_type": {
                        "type": "string",
                        "description": "Stream type identifier (e.g., 'juice', 'syrup')"
                    },
                    "version": {
                        "type": "string",
                        "description": "Schema version (optional, defaults to latest)"
                    }
                },
                "required": ["stream_type"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "stream_type": {"type": "string"},
                    "version": {"type": "string"},
                    "industry": {"type": "string"},
                    "schema": {"type": "object"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        # ========================================
        # PROCESS SERVER - Validation Tools (3)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="validate_process_inputs",
            description="Validate process input parameters (feeds and node parameters) before simulation",
            input_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "Process identifier to validate against"
                    },
                    "feeds": {
                        "type": "object",
                        "description": "Feed stream definitions (inlet conditions)"
                    },
                    "node_params": {
                        "type": "object",
                        "description": "Equipment node parameters (operating conditions)"
                    }
                },
                "required": ["process_id", "feeds", "node_params"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"},
                    "warnings": {"type": "array"}
                }
            },
            domain="sugar",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="validate_process_connections",
            description="Validate process equipment connections and sequence (Process Server)",
            input_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "Process identifier to validate connections for"
                    }
                },
                "required": ["process_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "sequence": {"type": "array"},
                    "message": {"type": "string"}
                }
            },
            domain="sugar",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="validate_equipment_inputs",
            description="Validate standalone equipment input parameters before simulation",
            input_schema={
                "type": "object",
                "properties": {
                    "equipment_type": {
                        "type": "string",
                        "description": "Equipment type identifier"
                    },
                    "inlet_stream": {
                        "type": "object",
                        "description": "Inlet stream properties"
                    },
                    "operating_params": {
                        "type": "object",
                        "description": "Equipment operating parameters"
                    }
                },
                "required": ["equipment_type", "inlet_stream", "operating_params"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"},
                    "warnings": {"type": "array"}
                }
            },
            domain="sugar",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        # ========================================
        # PROCESS SERVER - Simulation Tools (2)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="simulate_process",
            description="Simulate a complete process with all equipment units. Returns simulation results and run ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "Process identifier to simulate"
                    },
                    "feeds": {
                        "type": "object",
                        "description": "Feed stream definitions"
                    },
                    "node_params": {
                        "type": "object",
                        "description": "Equipment node parameters"
                    },
                    "solver_settings": {
                        "type": "object",
                        "description": "Solver settings for recycle convergence (optional)"
                    }
                },
                "required": ["process_id", "feeds", "node_params"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "calc_run_id": {"type": "string"},
                    "outputs": {"type": "object"},
                    "execution_time_ms": {"type": "number"}
                }
            },
            domain="sugar",
            category="simulate",
            prerequisites=["validate_process_inputs"],
            risk_level="gated",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="simulate_equipment",
            description="Simulate a standalone equipment unit. Returns simulation results and run ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "equipment_type": {
                        "type": "string",
                        "description": "Equipment type identifier"
                    },
                    "inlet_stream": {
                        "type": "object",
                        "description": "Inlet stream properties"
                    },
                    "operating_params": {
                        "type": "object",
                        "description": "Equipment operating parameters"
                    }
                },
                "required": ["equipment_type", "inlet_stream", "operating_params"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "calc_run_id": {"type": "string"},
                    "outputs": {"type": "object"},
                    "execution_time_ms": {"type": "number"}
                }
            },
            domain="sugar",
            category="simulate",
            prerequisites=["validate_equipment_inputs"],
            risk_level="gated",
            source_server="process"
        ))
        
        # ========================================
        # PROCESS SERVER - Run Tools (3)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="get_process_run",
            description="Retrieve results from a previous Process Server simulation run by run ID",
            input_schema={
                "type": "object",
                "properties": {
                    "calc_run_id": {
                        "type": "string",
                        "description": "Calculation run identifier from a previous simulation"
                    }
                },
                "required": ["calc_run_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "calc_run_id": {"type": "string"},
                    "outputs": {"type": "object"},
                    "created_at": {"type": "string"}
                }
            },
            domain="generic",
            category="lookup",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="compare_process_runs",
            description="Compare results from two Process Server simulation runs side by side",
            input_schema={
                "type": "object",
                "properties": {
                    "run_id_a": {
                        "type": "string",
                        "description": "First run identifier to compare"
                    },
                    "run_id_b": {
                        "type": "string",
                        "description": "Second run identifier to compare"
                    }
                },
                "required": ["run_id_a", "run_id_b"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "run_a": {"type": "object"},
                    "run_b": {"type": "object"},
                    "differences": {"type": "object"}
                }
            },
            domain="generic",
            category="compare",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        self.register_tool(ToolMetadata(
            name="list_process_runs",
            description="List all Process Server simulation runs in the current conversation",
            input_schema={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "Conversation identifier"
                    }
                },
                "required": ["conversation_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "conversation_id": {"type": "string"},
                    "run_ids": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "count": {"type": "integer"},
                    "newest": {"type": "string"},
                    "oldest": {"type": "string"}
                }
            },
            domain="generic",
            category="lookup",
            prerequisites=[],
            risk_level="safe",
            source_server="process"
        ))
        
        # ========================================
        # DYNAMIC SERVER - Discovery Tools (3)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="list_dynamic_processes",
            description="List all process templates available on the Dynamic Server",
            input_schema={
                "type": "object",
                "properties": {},
                "required": []
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "processes": {"type": "array"},
                    "count": {"type": "integer"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="get_process_template",
            description="Get detailed template information for a dynamic process",
            input_schema={
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "description": "Template identifier (e.g., 'ipa_recovery', 'ethanol_water_distillation')"
                    }
                },
                "required": ["template_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "template": {"type": "object"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="get_dynamic_equipment_schema",
            description="Get parameter schema for equipment on the Dynamic Server",
            input_schema={
                "type": "object",
                "properties": {
                    "equipment_type": {
                        "type": "string",
                        "description": "Equipment type identifier"
                    }
                },
                "required": ["equipment_type"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "equipment_type": {"type": "string"},
                    "schema": {"type": "object"}
                }
            },
            domain="discovery",
            category="discovery",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        # ========================================
        # DYNAMIC SERVER - Validation Tools (5)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="validate_compounds",
            description="Validate compound selection and property package compatibility",
            input_schema={
                "type": "object",
                "properties": {
                    "compounds": {"type": "array", "description": "List of compound names"},
                    "property_package": {"type": "string", "description": "Property package name"}
                },
                "required": ["compounds"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"}
                }
            },
            domain="generic",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="validate_equipment_params",
            description="Validate equipment parameters for dynamic simulation",
            input_schema={
                "type": "object",
                "properties": {
                    "equipment_type": {"type": "string"},
                    "params": {"type": "object"}
                },
                "required": ["equipment_type", "params"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"}
                }
            },
            domain="generic",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="validate_feed_streams",
            description="Validate feed stream definitions for dynamic flowsheet",
            input_schema={
                "type": "object",
                "properties": {
                    "feeds": {"type": "object", "description": "Feed stream definitions"}
                },
                "required": ["feeds"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"}
                }
            },
            domain="generic",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="validate_dynamic_connections",
            description="Validate flowsheet connections on the Dynamic Server",
            input_schema={
                "type": "object",
                "properties": {
                    "connections": {"type": "array", "description": "List of equipment connections"}
                },
                "required": ["connections"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"}
                }
            },
            domain="generic",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="validate_flowsheet",
            description="Comprehensive validation of entire dynamic flowsheet",
            input_schema={
                "type": "object",
                "properties": {
                    "flowsheet": {"type": "object", "description": "Complete flowsheet definition"}
                },
                "required": ["flowsheet"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "errors": {"type": "array"},
                    "warnings": {"type": "array"}
                }
            },
            domain="generic",
            category="validate",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        # ========================================
        # DYNAMIC SERVER - Building Tools (1)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="build_flowsheet",
            description="Construct a complete flowsheet from user input with conversational guidance",
            input_schema={
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "Base template to use"},
                    "compounds": {"type": "array", "description": "Compounds in the system"},
                    "equipment": {"type": "array", "description": "Equipment units"},
                    "connections": {"type": "array", "description": "Equipment connections"},
                    "feeds": {"type": "object", "description": "Feed stream definitions"}
                },
                "required": ["compounds", "equipment"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "flowsheet": {"type": "object"},
                    "validation": {"type": "object"}
                }
            },
            domain="generic",
            category="build",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        # ========================================
        # DYNAMIC SERVER - Simulation Tools (1)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="simulate_dynamic",
            description="Execute dynamic simulation via calculation engine",
            input_schema={
                "type": "object",
                "properties": {
                    "flowsheet": {"type": "object", "description": "Flowsheet definition"},
                    "solver_settings": {"type": "object", "description": "Solver settings (optional)"}
                },
                "required": ["flowsheet"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "run_id": {"type": "string"},
                    "outputs": {"type": "object"},
                    "execution_time_ms": {"type": "number"}
                }
            },
            domain="generic",
            category="simulate",
            prerequisites=["validate_flowsheet"],
            risk_level="gated",
            source_server="dynamic"
        ))
        
        # ========================================
        # DYNAMIC SERVER - Run Tools (3)
        # ========================================
        
        self.register_tool(ToolMetadata(
            name="get_dynamic_run",
            description="Retrieve results from a previous Dynamic Server simulation run",
            input_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Run identifier"}
                },
                "required": ["run_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "run_id": {"type": "string"},
                    "outputs": {"type": "object"}
                }
            },
            domain="generic",
            category="lookup",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="compare_dynamic_runs",
            description="Compare results from two Dynamic Server simulation runs",
            input_schema={
                "type": "object",
                "properties": {
                    "run_id_a": {"type": "string"},
                    "run_id_b": {"type": "string"}
                },
                "required": ["run_id_a", "run_id_b"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "differences": {"type": "object"}
                }
            },
            domain="generic",
            category="compare",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
        
        self.register_tool(ToolMetadata(
            name="list_dynamic_runs",
            description="List all Dynamic Server simulation runs in the current conversation",
            input_schema={
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"}
                },
                "required": ["conversation_id"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "run_ids": {"type": "array"},
                    "count": {"type": "integer"}
                }
            },
            domain="generic",
            category="lookup",
            prerequisites=[],
            risk_level="safe",
            source_server="dynamic"
        ))
