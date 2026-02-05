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
            process_name = run_doc.get("process_name", run_doc.get("process_id", "Unknown Process"))
            process_type = run_doc.get("process_type", "generic")
            created_at = run_doc.get("created_at", datetime.utcnow())
            
            # Extract from flowsheet_results
            flowsheet_results = run_doc.get("flowsheet_results", {})
            
            # Equipment list
            equipment_list = []
            equipment_results = flowsheet_results.get("equipment_results", {})
            for eq_id, eq_data in equipment_results.items():
                equipment_list.append({
                    "id": eq_id,
                    "name": eq_data.get("name", eq_id),
                    "type": eq_data.get("type", "unknown"),
                    "data": eq_data
                })
            
            # Stream data
            streams = []
            stream_results = flowsheet_results.get("streams", {})
            for stream_id, stream_data in stream_results.items():
                streams.append({
                    "id": stream_id,
                    "data": stream_data
                })
            
            # Input parameters and results
            input_parameters = run_doc.get("input_parameters", {})
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
            calculation_results=calculation_results
        )
    
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
        
        # Extract stream IDs in order
        stream_ids = [str(s.get("id", "")) for s in streams]
        
        # Collect all unique components across all streams
        all_components = set()
        for stream in streams:
            stream_data = stream.get("data", {})
            composition = stream_data.get("composition", {})
            all_components.update(composition.keys())
        
        components = sorted(list(all_components))
        
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
            components=components,
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
            eq_type = equipment.get("type", "unknown")
            eq_data = equipment.get("data", {})
            
            equipment_ids.append(eq_id)
            equipment_names.append(eq_name)
            equipment_types.append(eq_type)
            
            # Extract duty (heat or power)
            duty = eq_data.get("duty_kW") or eq_data.get("heat_duty") or eq_data.get("power")
            duties.append(duty)
            
            # Extract efficiency
            efficiency = eq_data.get("efficiency") or eq_data.get("thermal_efficiency")
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
        
        # Get streams from calculation_results or parameter
        stream_data = calculation_results.get("streams", {})
        if not stream_data and streams:
            stream_data = {s.get("id", f"s{i}"): s.get("data", {}) for i, s in enumerate(streams)}
        
        # Sum flow rates (simplified - actual implementation would use topology)
        for stream_id, data in stream_data.items():
            flow_rate = data.get("flow_rate", 0) or 0
            # Simple heuristic: streams with "in" or "feed" are inputs
            if any(x in stream_id.lower() for x in ["in", "feed", "inlet", "raw"]):
                total_mass_in += flow_rate
            else:
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
        
        # Get equipment from calculation_results or parameter
        equip_results = calculation_results.get("equipment_results", {})
        if not equip_results and equipment_list:
            equip_results = {e.get("id", f"e{i}"): e.get("data", {}) for i, e in enumerate(equipment_list)}
        
        # Sum heat duties from equipment
        for eq_id, eq_data in equip_results.items():
            # Get duty
            duty = eq_data.get("duty_kW") or eq_data.get("heat_duty") or eq_data.get("duty") or 0.0
            
            # Positive duty = heat input, negative = heat output
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
