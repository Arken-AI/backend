"""
Table Formatter Service

Responsible for formatting stream and equipment data into 2D arrays
suitable for PDF table rendering with proper number formatting and pagination.
"""

from typing import List, Dict, Any, Optional, Tuple
import math

from app.models.report import StreamTableData, EquipmentTableData


# =============================================================================
# FORMATTING CONSTANTS
# =============================================================================

# Decimal places for different data types
DECIMAL_PLACES = {
    "temperature": 1,
    "pressure": 2,
    "flow_rate": 2,
    "vapor_fraction": 3,
    "mass_flow": 2,
    "molar_flow": 4,
    "duty": 2,
    "efficiency": 1,
    "default": 4,
}

# Row labels for stream table
STREAM_ROW_LABELS = {
    "temperature": "Temperature",
    "pressure": "Pressure",
    "flow_rate": "Mass Flow",
    "vapor_fraction": "Vapor Frac",
}


class StreamTableFormatter:
    """
    Formats stream data into 2D arrays for PDF table generation.
    
    Handles:
    - Number formatting with appropriate decimal places
    - Unit display
    - Missing value handling
    - Table splitting for wide tables
    """
    
    def __init__(self, max_streams_per_table: int = 10):
        """
        Initialize the formatter.
        
        Args:
            max_streams_per_table: Maximum number of streams before splitting table
        """
        self.max_streams_per_table = max_streams_per_table
    
    def format_stream_table(
        self,
        stream_data: StreamTableData,
        include_units: bool = True
    ) -> List[List[str]]:
        """
        Convert StreamTableData into a 2D list of formatted strings.
        
        Output format (like DOE report):
        [
            ["", "#1", "#2", "#3", "#4"],
            ["Temperature °C", "30.0", "78.0", "100.0", "35.0"],
            ["Pressure bar", "1.00", "1.00", "1.00", "1.00"],
            ["Mass Flow kg/hr", "1000.00", "150.00", "850.00", "150.00"],
            ["Vapor Frac", "0.000", "1.000", "0.000", "0.000"],
            ["H2O kg/hr", "900.00", "10.50", "841.50", "10.50"],
            ["Ethanol kg/hr", "100.00", "139.50", "8.50", "139.50"],
        ]
        
        Args:
            stream_data: StreamTableData object with stream information
            include_units: Whether to include units in row labels
            
        Returns:
            2D list of strings ready for PDF table
        """
        if not stream_data.stream_ids:
            return []
        
        table = []
        
        # Header row with stream names (use display names if available)
        header_row = ["Property"]
        for i, stream_id in enumerate(stream_data.stream_ids):
            # Use display name if available, otherwise fall back to ID
            display_name = stream_data.get_stream_display_name(i)
            header_row.append(display_name)
        table.append(header_row)
        
        # Standard property rows
        standard_rows = ["temperature", "pressure", "flow_rate", "vapor_fraction"]
        
        for row_key in standard_rows:
            if row_key in stream_data.data:
                label = STREAM_ROW_LABELS.get(row_key, row_key.replace("_", " ").title())
                unit = stream_data.units.get(row_key, "")
                
                if include_units and unit:
                    row_label = f"{label} {unit}"
                else:
                    row_label = label
                
                row = [row_label]
                for value in stream_data.data[row_key]:
                    row.append(format_number(value, row_key))
                table.append(row)
        
        # Component rows (use display names if available)
        for i, component in enumerate(stream_data.components):
            if component in stream_data.data:
                unit = stream_data.units.get(component, "")
                
                # Use display name if available
                component_display = stream_data.get_component_display_name(i)
                
                if include_units and unit:
                    row_label = f"{component_display} {unit}"
                else:
                    row_label = component_display
                
                row = [row_label]
                for value in stream_data.data[component]:
                    row.append(format_number(value, "mass_flow"))
                table.append(row)
        
        return table
    
    def format_equipment_table(
        self,
        equipment_data: EquipmentTableData,
        include_units: bool = True
    ) -> List[List[str]]:
        """
        Convert EquipmentTableData into a 2D list of formatted strings.
        
        Output format:
        [
            ["Equipment", "Type", "Duty kW", "Efficiency %"],
            ["Ethanol Column", "Distillation", "500.00", "85.0"],
            ["Distillate Cooler", "Heat Exchanger", "50.00", "95.0"],
        ]
        
        Args:
            equipment_data: EquipmentTableData object
            include_units: Whether to include units in headers
            
        Returns:
            2D list of strings ready for PDF table
        """
        if not equipment_data.equipment_ids:
            return []
        
        table = []
        
        # Build header row
        header = ["Equipment", "Type"]
        
        # Add data columns with units
        if "duty" in equipment_data.data:
            unit = equipment_data.units.get("duty", "kW")
            header.append(f"Duty {unit}" if include_units else "Duty")
        
        if "efficiency" in equipment_data.data:
            unit = equipment_data.units.get("efficiency", "%")
            header.append(f"Efficiency {unit}" if include_units else "Efficiency")
        
        table.append(header)
        
        # Build data rows
        num_equipment = len(equipment_data.equipment_ids)
        
        for idx in range(num_equipment):
            row = [
                equipment_data.equipment_names[idx],
                equipment_data.equipment_types[idx]
            ]
            
            # Add duty
            if "duty" in equipment_data.data:
                duty = equipment_data.data["duty"][idx]
                row.append(format_number(duty, "duty"))
            
            # Add efficiency
            if "efficiency" in equipment_data.data:
                efficiency = equipment_data.data["efficiency"][idx]
                row.append(format_number(efficiency, "efficiency"))
            
            table.append(row)
        
        return table
    
    def split_wide_table(
        self,
        table: List[List[str]],
        max_columns: Optional[int] = None
    ) -> List[List[List[str]]]:
        """
        Split a wide table into multiple tables that fit on a page.
        
        The first column (row labels) is preserved in each split.
        
        Args:
            table: 2D list representing the table
            max_columns: Maximum columns per table (including label column).
                        Uses self.max_streams_per_table + 1 if not specified.
                        
        Returns:
            List of tables (each table is a 2D list)
        """
        if not table:
            return []
        
        if max_columns is None:
            max_columns = self.max_streams_per_table + 1  # +1 for label column
        
        # Get total number of columns
        num_columns = len(table[0]) if table else 0
        
        # If table fits, return as single table
        if num_columns <= max_columns:
            return [table]
        
        # Need to split
        tables = []
        
        # Number of data columns per split (excluding label column)
        data_cols_per_split = max_columns - 1
        
        # Calculate number of splits needed
        num_data_cols = num_columns - 1  # Exclude label column
        num_splits = math.ceil(num_data_cols / data_cols_per_split)
        
        for split_idx in range(num_splits):
            split_table = []
            
            # Calculate column indices for this split
            start_col = 1 + (split_idx * data_cols_per_split)
            end_col = min(start_col + data_cols_per_split, num_columns)
            
            for row in table:
                # Always include first column (labels) plus the split columns
                new_row = [row[0]] + row[start_col:end_col]
                split_table.append(new_row)
            
            tables.append(split_table)
        
        return tables


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_number(
    value: Any,
    format_type: str = "default"
) -> str:
    """
    Format a number with appropriate decimal places.
    
    Args:
        value: The value to format (can be number, None, or other)
        format_type: Type of data for determining decimal places
        
    Returns:
        Formatted string representation
    """
    # Handle None or missing values
    if value is None:
        return "-"
    
    # Handle NaN
    if isinstance(value, float) and math.isnan(value):
        return "-"
    
    # Handle non-numeric values
    if not isinstance(value, (int, float)):
        return str(value)
    
    # Get decimal places for this format type
    decimal_places = DECIMAL_PLACES.get(format_type, DECIMAL_PLACES["default"])
    
    # Format the number
    try:
        formatted = f"{value:.{decimal_places}f}"
        return formatted
    except (ValueError, TypeError):
        return str(value)


def format_with_thousands_separator(
    value: Any,
    format_type: str = "default"
) -> str:
    """
    Format a number with thousands separators and appropriate decimals.
    
    Args:
        value: The value to format
        format_type: Type of data for determining decimal places
        
    Returns:
        Formatted string with commas for thousands
    """
    # Handle None or missing values
    if value is None:
        return "-"
    
    # Handle NaN
    if isinstance(value, float) and math.isnan(value):
        return "-"
    
    # Handle non-numeric values
    if not isinstance(value, (int, float)):
        return str(value)
    
    # Get decimal places for this format type
    decimal_places = DECIMAL_PLACES.get(format_type, DECIMAL_PLACES["default"])
    
    # Format with thousands separator
    try:
        formatted = f"{value:,.{decimal_places}f}"
        return formatted
    except (ValueError, TypeError):
        return str(value)


def create_balance_summary_table(
    mass_in: float,
    mass_out: float,
    mass_closure: float,
    heat_in: Optional[float] = None,
    heat_out: Optional[float] = None,
    power: Optional[float] = None,
    energy_closure: Optional[float] = None
) -> List[List[str]]:
    """
    Create a formatted balance summary table.
    
    Args:
        mass_in: Total mass input
        mass_out: Total mass output
        mass_closure: Mass balance closure percentage
        heat_in: Total heat input (optional)
        heat_out: Total heat output (optional)
        power: Total power consumption (optional)
        energy_closure: Energy balance closure percentage (optional)
        
    Returns:
        2D list representing the balance summary table
    """
    table = []
    
    # Mass balance section
    table.append(["Mass Balance", ""])
    table.append(["Total Mass In", f"{format_with_thousands_separator(mass_in, 'mass_flow')} kg/hr"])
    table.append(["Total Mass Out", f"{format_with_thousands_separator(mass_out, 'mass_flow')} kg/hr"])
    table.append(["Closure", f"{format_number(mass_closure, 'efficiency')}%"])
    
    # Energy balance section (if provided)
    if heat_in is not None or heat_out is not None:
        table.append(["", ""])  # Empty row separator
        table.append(["Energy Balance", ""])
        
        if heat_in is not None:
            table.append(["Total Heat Input", f"{format_with_thousands_separator(heat_in, 'duty')} kW"])
        
        if heat_out is not None:
            table.append(["Total Heat Output", f"{format_with_thousands_separator(heat_out, 'duty')} kW"])
        
        if power is not None and power > 0:
            table.append(["Total Power", f"{format_with_thousands_separator(power, 'duty')} kW"])
        
        if energy_closure is not None:
            table.append(["Closure", f"{format_number(energy_closure, 'efficiency')}%"])
    
    return table
