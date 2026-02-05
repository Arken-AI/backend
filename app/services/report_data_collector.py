"""
Report Data Collector Service

Responsible for collecting and transforming simulation run data from MongoDB
into structured formats suitable for report generation.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.models.report import (
    SimulationRunData,
    StreamTableData,
    EquipmentTableData,
    MassBalanceSummary,
    EnergyBalanceSummary,
)


def format_display_name(raw_id: str, raw_name: str = None) -> str:
    """
    Format a raw ID or name into a human-readable display name.
    
    Module-level function for use across the codebase.
    
    Examples:
        ethanolwaterbinary -> Ethanol Water Binary
        dilute_ethanol_feed -> Dilute Ethanol Feed
        e01_column_to_cooler -> Column to Cooler
        concentration_column -> Concentration Column
    """
    import re
    
    # Use provided name if it's meaningfully different from ID
    if raw_name and raw_name != raw_id and not raw_name.startswith(raw_id[:3] if len(raw_id) >= 3 else raw_id):
        return raw_name
    
    name = raw_id
    
    # Remove common prefixes like e01_, s01_, etc.
    name = re.sub(r'^[es]\d+_', '', name)
    
    # Handle camelCase (insert space before capitals)
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    
    # Handle run-together words like "ethanolwaterbinary"
    # Common chemical/process terms to split
    name = re.sub(r'(ethanol)(water)', r'\1 \2', name, flags=re.IGNORECASE)
    name = re.sub(r'(water)(binary)', r'\1 \2', name, flags=re.IGNORECASE)
    name = re.sub(r'(methanol)(water)', r'\1 \2', name, flags=re.IGNORECASE)
    name = re.sub(r'(distillation)(column)', r'\1 \2', name, flags=re.IGNORECASE)
    
    # Replace underscores with spaces
    name = name.replace('_', ' ')
    
    # Title case each word
    name = ' '.join(word.capitalize() for word in name.split())
    
    return name


class ReportDataCollector:
    """
    Collects and transforms simulation run data for report generation.
    
    This service:
    - Fetches simulation run data from MongoDB
    - Transforms streams into table format
    - Builds equipment summaries
    - Calculates mass and energy balances
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize the data collector.
        
        Args:
            db: AsyncIOMotorDatabase instance for MongoDB queries
        """
        self.db = db
        self.runs_collection = db.runs
        self.processes_collection = db.processes
        self.industries_collection = db.industries
    
    async def collect_run_data(self, run_id: str) -> SimulationRunData:
        """
        Fetch complete simulation run data from MongoDB.
        
        Searches both calc_simulation_runs and runs collections.
        
        Args:
            run_id: ID of the simulation run to fetch
            
        Returns:
            SimulationRunData object with all run information
            
        Raises:
            ValueError: If run_id is not found in database
        """
        # First try calc_simulation_runs collection (calc engine)
        run_doc = await self.db.calc_simulation_runs.find_one({"run_id": run_id})
        source = "calc_engine"
        
        # If not found, try runs collection (process server)
        if not run_doc:
            run_doc = await self.runs_collection.find_one({"run_id": run_id})
            source = "process_server"
        
        if not run_doc:
            raise ValueError(f"Simulation run not found: {run_id}")
        
        # Extract basic metadata based on source
        if source == "calc_engine":
            # calc_simulation_runs structure
            # Note: process_name may be in process_id field
            process_name = run_doc.get("process_name", run_doc.get("process_id", "Unknown Process"))
            process_type = run_doc.get("process_type", "generic")
            created_at = run_doc.get("created_at", datetime.utcnow())
            
            # The calc engine stores results in a nested structure:
            # run_doc.result.result contains the actual flowsheet results
            api_response = run_doc.get("result", {})
            flowsheet_results = api_response.get("result", {})
            
            # Also check for input data
            input_data = api_response.get("input", {})
            
            # Equipment list - calc engine uses "node_results"
            equipment_list = []
            equipment_results = flowsheet_results.get("node_results", flowsheet_results.get("equipment_results", {}))
            for eq_id, eq_data in equipment_results.items():
                # Extract equipment metadata if available
                metadata = eq_data.get("metadata", {})
                equipment_list.append({
                    "id": eq_id,
                    "name": metadata.get("name", eq_id),
                    "type": metadata.get("type", eq_data.get("type", "unknown")),
                    "data": eq_data
                })
            
            # Stream data - calc engine uses "stream_results"
            streams = []
            stream_results = flowsheet_results.get("stream_results", flowsheet_results.get("streams", {}))
            for stream_id, stream_data in stream_results.items():
                streams.append({
                    "id": stream_id,
                    "data": stream_data
                })
            
            # Input parameters - may be in payload_snapshot or result.input
            input_parameters = run_doc.get("payload_snapshot", input_data)
            calculation_results = flowsheet_results
            
        else:
            # runs collection structure (process server / sugar)
            process_name = run_doc.get("process_name", run_doc.get("process_id", "Unknown Process"))
            process_type = run_doc.get("industry", "sugar")
            created_at = run_doc.get("created_at", datetime.utcnow())
            
            # Extract from outputs
            outputs = run_doc.get("outputs", {})
            
            # Equipment list
            equipment_list = []
            equipment_results = outputs.get("equipment_results", outputs.get("equipment", {}))
            if isinstance(equipment_results, dict):
                for eq_id, eq_data in equipment_results.items():
                    equipment_list.append({
                        "id": eq_id,
                        "name": eq_data.get("name", eq_id),
                        "type": eq_data.get("type", "unknown"),
                        "data": eq_data
                    })
            
            # Stream data
            streams = []
            stream_results = outputs.get("streams", {})
            if isinstance(stream_results, dict):
                for stream_id, stream_data in stream_results.items():
                    streams.append({
                        "id": stream_id,
                        "data": stream_data
                    })
            
            # Input parameters and results
            input_parameters = run_doc.get("inputs", {})
            calculation_results = outputs
        
        return SimulationRunData(
            run_id=run_id,
            process_name=process_name,
            process_type=process_type,
            created_at=created_at,
            equipment_list=equipment_list,
            streams=streams,
            input_parameters=input_parameters,
            calculation_results=calculation_results,
            industry=process_type,  # Use process_type as industry for AI narratives
            status="completed",     # Default status for completed runs
            timestamp=created_at,   # Alias for narrative templates
        )
    
    @staticmethod
    def format_display_name(raw_id: str, raw_name: str = None) -> str:
        """Delegate to module-level function."""
        return format_display_name(raw_id, raw_name)
    
    @staticmethod
    def classify_stream_type(stream_id: str) -> str:
        """Classify stream as Feed, Product, Intermediate, or Waste."""
        sid_lower = stream_id.lower()
        
        if any(kw in sid_lower for kw in ['feed', 'inlet', 'input', 'raw']):
            return "feed"
        elif any(kw in sid_lower for kw in ['product', 'distillate', 'output']):
            return "product"
        elif any(kw in sid_lower for kw in ['waste', 'bottoms', 'reject', 'purge']):
            return "waste"
        elif '_to_' in sid_lower:
            return "intermediate"
        else:
            return "product"
    
    @staticmethod
    def infer_equipment_type(eq_id: str) -> str:
        """
        Infer equipment type from equipment ID.
        
        Examples:
            concentration_column -> Distillation Column
            product_cooler -> Cooler
            feed_heater -> Heater
            ethanol_pump -> Pump
            flash_drum -> Flash Drum
        """
        eq_lower = eq_id.lower()
        
        # Check for specific equipment types
        if 'column' in eq_lower or 'distill' in eq_lower:
            return "Distillation Column"
        elif 'cooler' in eq_lower:
            return "Cooler"
        elif 'heater' in eq_lower:
            return "Heater"
        elif 'exchanger' in eq_lower or 'hx' in eq_lower:
            return "Heat Exchanger"
        elif 'pump' in eq_lower:
            return "Pump"
        elif 'compressor' in eq_lower:
            return "Compressor"
        elif 'flash' in eq_lower or 'drum' in eq_lower:
            return "Flash Drum"
        elif 'reactor' in eq_lower:
            return "Reactor"
        elif 'tank' in eq_lower or 'vessel' in eq_lower:
            return "Tank"
        elif 'mixer' in eq_lower:
            return "Mixer"
        elif 'splitter' in eq_lower:
            return "Splitter"
        elif 'valve' in eq_lower:
            return "Valve"
        elif 'evaporator' in eq_lower:
            return "Evaporator"
        elif 'condenser' in eq_lower:
            return "Condenser"
        elif 'reboiler' in eq_lower:
            return "Reboiler"
        elif 'absorber' in eq_lower:
            return "Absorber"
        elif 'stripper' in eq_lower:
            return "Stripper"
        else:
            # Fallback: format the ID nicely
            return ReportDataCollector.format_display_name(eq_id)
    
    @staticmethod
    def truncate_name(name: str, max_length: int = 15) -> str:
        """
        Truncate a display name to fit in table columns.
        Uses smart abbreviation for common words.
        
        Examples:
            Dilute Ethanol Feed -> Dilute Eth. Feed (if > max_length)
            Distillate Cooler Outlet -> Dist. Cooler Out.
        """
        if len(name) <= max_length:
            return name
        
        # Common word abbreviations
        abbreviations = {
            'Ethanol': 'Eth.',
            'Distillate': 'Dist.',
            'Outlet': 'Out.',
            'Inlet': 'In.',
            'Product': 'Prod.',
            'Column': 'Col.',
            'Cooler': 'Cool.',
            'Heater': 'Heat.',
            'Temperature': 'Temp.',
            'Pressure': 'Press.',
            'Concentration': 'Conc.',
            'Water': 'H2O',
        }
        
        # Try abbreviating words one at a time until it fits
        words = name.split()
        for i, word in enumerate(words):
            if word in abbreviations:
                words[i] = abbreviations[word]
                abbreviated = ' '.join(words)
                if len(abbreviated) <= max_length:
                    return abbreviated
        
        # If still too long, just truncate with ellipsis
        abbreviated = ' '.join(words)
        if len(abbreviated) > max_length:
            return abbreviated[:max_length-2] + '..'
        return abbreviated
    
    def build_stream_table(
        self, 
        streams: List[Dict[str, Any]],
        flow_basis: str = "mass"
    ) -> StreamTableData:
        """
        Transform stream data into table format for PDF generation.
        
        Args:
            streams: List of stream dictionaries from simulation run
            flow_basis: "mass" or "molar" for flow rate basis
            
        Returns:
            StreamTableData object ready for table formatting
        """
        if not streams:
            return StreamTableData(
                stream_ids=[],
                components=[],
                data={},
                units={}
            )
        
        # Extract stream IDs and create display names (truncated for table columns)
        stream_ids = []
        stream_display_names = []
        for s in streams:
            raw_id = str(s.get("id", ""))
            stream_data = s.get("data", {})
            raw_name = stream_data.get("name", raw_id)
            
            stream_ids.append(raw_id)
            # Format and truncate for table column width
            display_name = self.format_display_name(raw_id, raw_name)
            stream_display_names.append(self.truncate_name(display_name, max_length=18))
        
        # Collect all unique components across all streams
        all_components = set()
        for stream in streams:
            stream_data = stream.get("data", {})
            composition = stream_data.get("composition", {})
            all_components.update(composition.keys())
        
        # Format component names for display
        components = sorted(list(all_components))
        component_display_names = [self.format_display_name(c) for c in components]
        
        # Build data dictionary
        data = {}
        
        # Temperature row
        temperatures = []
        for stream in streams:
            stream_data = stream.get("data", {})
            temp_K = stream_data.get("temperature_K")
            if temp_K is not None:
                temp_C = temp_K - 273.15
                temperatures.append(temp_C)
            else:
                temperatures.append(None)
        data["temperature"] = temperatures
        
        # Pressure row
        pressures = []
        for stream in streams:
            stream_data = stream.get("data", {})
            pressure_Pa = stream_data.get("pressure_Pa")
            if pressure_Pa is not None:
                pressure_bar = pressure_Pa / 100000.0
                pressures.append(pressure_bar)
            else:
                pressures.append(None)
        data["pressure"] = pressures
        
        # Flow rate row
        flow_rates = []
        for stream in streams:
            stream_data = stream.get("data", {})
            flow_rate = stream_data.get("flow_rate")
            flow_rates.append(flow_rate)
        data["flow_rate"] = flow_rates
        
        # Vapor fraction row
        vapor_fractions = []
        for stream in streams:
            stream_data = stream.get("data", {})
            phase = stream_data.get("phase", "unknown")
            if phase == "vapor":
                vapor_fractions.append(1.0)
            elif phase == "liquid":
                vapor_fractions.append(0.0)
            else:
                vapor_fractions.append(None)
        data["vapor_fraction"] = vapor_fractions
        
        # Component rows
        for component in components:
            component_flows = []
            for stream in streams:
                stream_data = stream.get("data", {})
                composition = stream_data.get("composition", {})
                flow_rate = stream_data.get("flow_rate", 0)
                
                if component in composition and flow_rate is not None:
                    component_flow = composition[component] * flow_rate
                    component_flows.append(component_flow)
                else:
                    component_flows.append(None)
            
            data[component] = component_flows
        
        # Define units
        units = {
            "temperature": "°C",
            "pressure": "bar",
            "flow_rate": "kg/hr" if flow_basis == "mass" else "kmol/hr",
            "vapor_fraction": "-"
        }
        
        # Add units for components (same as flow rate)
        for component in components:
            units[component] = units["flow_rate"]
        
        return StreamTableData(
            stream_ids=stream_ids,
            stream_names=stream_display_names,  # Add display names
            components=components,
            component_names=component_display_names,  # Add display names
            data=data,
            units=units
        )
    
    def build_equipment_table(
        self, 
        equipment_list: List[Dict[str, Any]]
    ) -> EquipmentTableData:
        """
        Build equipment summary table data.
        
        Args:
            equipment_list: List of equipment dictionaries from simulation run
            
        Returns:
            EquipmentTableData object ready for table formatting
        """
        if not equipment_list:
            return EquipmentTableData(
                equipment_ids=[],
                equipment_names=[],
                equipment_types=[],
                data={},
                units={}
            )
        
        equipment_ids = []
        equipment_names = []
        equipment_types = []
        duties = []
        efficiencies = []
        
        for equipment in equipment_list:
            eq_id = equipment.get("id", "")
            eq_name = equipment.get("name", eq_id)
            eq_type = equipment.get("type", "")
            eq_data = equipment.get("data", {})
            
            equipment_ids.append(eq_id)
            # Format display names using the helper
            equipment_names.append(self.format_display_name(eq_id, eq_name))
            
            # Infer equipment type from ID if not provided
            if eq_type and eq_type != "unknown":
                equipment_types.append(self.format_display_name(eq_type))
            else:
                equipment_types.append(self.infer_equipment_type(eq_id))
            
            # Extract duty from various sources
            duty = None
            
            # Check energy_streams (calc engine format for columns)
            energy_streams = eq_data.get("energy_streams", {})
            if energy_streams:
                # Sum all duties (reboiler + condenser)
                total_duty = 0
                for stream_name, stream_data in energy_streams.items():
                    duty_kW = stream_data.get("duty_kW", 0) or 0
                    total_duty += abs(duty_kW)
                if total_duty > 0:
                    duty = total_duty
            
            # Check metadata (calc engine format for coolers/heaters)
            if duty is None:
                metadata = eq_data.get("metadata", {})
                if metadata:
                    duty_info = metadata.get("duty", {})
                    if isinstance(duty_info, dict):
                        duty = duty_info.get("total_duty_kW") or duty_info.get("duty_removed_kW")
                    # Also check direct metadata fields
                    if duty is None:
                        duty = metadata.get("condenser_duty_kW") or metadata.get("reboiler_duty_kW")
            
            # Fallback: direct eq_data fields
            if duty is None:
                duty = eq_data.get("duty_kW") or eq_data.get("heat_duty") or eq_data.get("power")
            
            duties.append(duty)
            
            # Extract efficiency from various sources
            efficiency = eq_data.get("efficiency") or eq_data.get("thermal_efficiency")
            if efficiency is None:
                metadata = eq_data.get("metadata", {})
                efficiency = metadata.get("efficiency")
            
            if efficiency is not None and efficiency <= 1.0:
                # Convert fraction to percentage
                efficiency = efficiency * 100.0
            efficiencies.append(efficiency)
        
        return EquipmentTableData(
            equipment_ids=equipment_ids,
            equipment_names=equipment_names,
            equipment_types=equipment_types,
            data={
                "duty": duties,
                "efficiency": efficiencies
            },
            units={
                "duty": "kW",
                "efficiency": "%"
            }
        )
    
    def build_mass_balance_summary(
        self, 
        calculation_results: Dict[str, Any],
        streams: List[Dict[str, Any]] = None
    ) -> MassBalanceSummary:
        """
        Calculate mass balance summary from calculation results.
        
        Args:
            calculation_results: Calculation results dictionary
            streams: Optional list of streams (if not in calculation_results)
            
        Returns:
            MassBalanceSummary object with totals and closure
        """
        # Try to get mass balance from calculation results directly
        mass_balance = calculation_results.get("mass_balance", {})
        
        if mass_balance:
            return MassBalanceSummary(
                total_mass_in=mass_balance.get("total_in", 0.0),
                total_mass_out=mass_balance.get("total_out", 0.0),
                closure_percentage=mass_balance.get("closure", 100.0)
            )
        
        # Fallback: Calculate from streams
        total_mass_in = 0.0
        total_mass_out = 0.0
        
        # Get streams from calculation_results (try both keys) or parameter
        # Calc engine uses "stream_results", process server uses "streams"
        stream_data = calculation_results.get("stream_results", calculation_results.get("streams", {}))
        if not stream_data and streams:
            stream_data = {s.get("id", f"s{i}"): s.get("data", {}) for i, s in enumerate(streams)}
        
        # Sum flow rates based on stream naming conventions
        # Inputs: feed, inlet, in, raw, input
        # Outputs: product, outlet, out, waste, output, distillate, bottoms
        input_keywords = ["feed", "inlet", "in", "raw", "input"]
        output_keywords = ["product", "outlet", "out", "waste", "output", "distillate", "bottoms", "cooler"]
        
        for stream_id, data in stream_data.items():
            flow_rate = data.get("flow_rate", 0) or 0
            stream_lower = stream_id.lower()
            
            # Check if it's an input stream
            is_input = any(kw in stream_lower for kw in input_keywords)
            is_output = any(kw in stream_lower for kw in output_keywords)
            
            # Internal streams (e.g., column_to_cooler) should be skipped
            is_internal = "_to_" in stream_lower and not is_output
            
            if is_input and not is_internal:
                total_mass_in += flow_rate
            elif is_output or (not is_input and not is_internal):
                # Only count terminal streams as outputs
                # Skip intermediate streams
                if is_output:
                    total_mass_out += flow_rate
        
        # If no categorization worked, use equipment-based approach
        if total_mass_in == 0 and total_mass_out == 0:
            # Get total from first/last streams
            stream_list = list(stream_data.values())
            if stream_list:
                total_mass_in = stream_list[0].get("flow_rate", 0) or 0
                total_mass_out = sum(s.get("flow_rate", 0) or 0 for s in stream_list[1:])
        
        # Calculate closure percentage
        if total_mass_in > 0:
            closure_percentage = (total_mass_out / total_mass_in) * 100.0
        else:
            closure_percentage = 100.0 if total_mass_out == 0 else 0.0
        
        return MassBalanceSummary(
            total_mass_in=total_mass_in,
            total_mass_out=total_mass_out,
            closure_percentage=min(closure_percentage, 100.0)
        )
    
    def build_energy_balance_summary(
        self, 
        calculation_results: Dict[str, Any],
        equipment_list: List[Dict[str, Any]] = None
    ) -> EnergyBalanceSummary:
        """
        Calculate energy balance summary from calculation results.
        
        Args:
            calculation_results: Calculation results dictionary
            equipment_list: Optional list of equipment (if not in calculation_results)
            
        Returns:
            EnergyBalanceSummary object with totals and closure
        """
        # Try to get energy balance from calculation results directly
        energy_balance = calculation_results.get("energy_balance", {})
        
        if energy_balance:
            return EnergyBalanceSummary(
                total_heat_input=energy_balance.get("heat_in", 0.0),
                total_heat_output=energy_balance.get("heat_out", 0.0),
                total_power=energy_balance.get("power", 0.0),
                closure_percentage=energy_balance.get("closure", 100.0)
            )
        
        # Fallback: Calculate from equipment
        total_heat_input = 0.0
        total_heat_output = 0.0
        total_power = 0.0
        
        # Get equipment from calculation_results (try both keys) or parameter
        # Calc engine uses "node_results", process server uses "equipment_results"
        equip_results = calculation_results.get("node_results", calculation_results.get("equipment_results", {}))
        if not equip_results and equipment_list:
            equip_results = {e.get("id", f"e{i}"): e.get("data", {}) for i, e in enumerate(equipment_list)}
        
        # Sum heat duties from equipment
        for eq_id, eq_data in equip_results.items():
            # Check for energy_streams (calc engine format)
            energy_streams = eq_data.get("energy_streams", {})
            if energy_streams:
                for stream_name, stream_data in energy_streams.items():
                    duty_kW = stream_data.get("duty_kW", 0) or 0
                    energy_type = stream_data.get("energy_type", "")
                    
                    if energy_type == "heat" or duty_kW > 0:
                        total_heat_input += abs(duty_kW)
                    elif energy_type == "cooling" or duty_kW < 0:
                        total_heat_output += abs(duty_kW)
                continue
            
            # Check metadata for duty (cooler, heater, etc.)
            metadata = eq_data.get("metadata", {})
            if metadata:
                duty_info = metadata.get("duty", {})
                if isinstance(duty_info, dict):
                    duty_removed = duty_info.get("duty_removed_kW", 0) or 0
                    if duty_removed > 0:
                        total_heat_output += duty_removed
                    duty_added = duty_info.get("duty_added_kW", 0) or 0
                    if duty_added > 0:
                        total_heat_input += duty_added
            
            # Fallback: Get duty directly from eq_data
            duty = eq_data.get("duty_kW") or eq_data.get("heat_duty") or eq_data.get("duty") or 0.0
            if duty:
                if duty > 0:
                    total_heat_input += duty
                else:
                    total_heat_output += abs(duty)
            
            # Get power consumption
            power = eq_data.get("power") or eq_data.get("power_kW") or 0.0
            total_power += abs(power)
        
        # Calculate closure percentage
        total_energy_in = total_heat_input + total_power
        if total_energy_in > 0:
            closure_percentage = (total_heat_output / total_energy_in) * 100.0
        else:
            closure_percentage = 100.0 if total_heat_output == 0 else 0.0
        
        return EnergyBalanceSummary(
            total_heat_input=total_heat_input,
            total_heat_output=total_heat_output,
            total_power=total_power,
            closure_percentage=min(closure_percentage, 100.0)
        )
