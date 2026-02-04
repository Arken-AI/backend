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
        
        Args:
            run_id: ID of the simulation run to fetch
            
        Returns:
            SimulationRunData object with all run information
            
        Raises:
            ValueError: If run_id is not found in database
        """
        # Query the runs collection
        run_doc = await self.runs_collection.find_one({"_id": ObjectId(run_id)})
        
        if not run_doc:
            raise ValueError(f"Simulation run not found: {run_id}")
        
        # Extract basic metadata
        process_name = run_doc.get("process_name", "Unknown Process")
        process_type = run_doc.get("process_type", "generic")
        created_at = run_doc.get("created_at", datetime.utcnow())
        
        # Extract equipment list from run result
        equipment_list = []
        if "result" in run_doc and isinstance(run_doc["result"], dict):
            equipment_results = run_doc["result"].get("equipment_results", {})
            for eq_id, eq_data in equipment_results.items():
                equipment_list.append({
                    "id": eq_id,
                    "name": eq_data.get("name", eq_id),
                    "type": eq_data.get("type", "unknown"),
                    "data": eq_data
                })
        
        # Extract stream data from run result
        streams = []
        if "result" in run_doc and isinstance(run_doc["result"], dict):
            stream_results = run_doc["result"].get("streams", {})
            for stream_id, stream_data in stream_results.items():
                streams.append({
                    "id": stream_id,
                    "data": stream_data
                })
        
        # Extract input parameters
        input_parameters = run_doc.get("input_parameters", {})
        
        # Extract calculation results
        calculation_results = run_doc.get("result", {})
        
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
        run_data: SimulationRunData
    ) -> MassBalanceSummary:
        """
        Calculate mass balance summary from run data.
        
        Args:
            run_data: Complete simulation run data
            
        Returns:
            MassBalanceSummary object with totals and closure
        """
        # This is a simplified version - actual implementation would need
        # to identify inlet vs outlet streams from the flowsheet topology
        
        total_mass_in = 0.0
        total_mass_out = 0.0
        
        # For now, assume first stream is inlet, rest are outlets
        # In real implementation, would query flowsheet structure
        for idx, stream in enumerate(run_data.streams):
            stream_data = stream.get("data", {})
            flow_rate = stream_data.get("flow_rate", 0)
            
            if idx == 0:
                total_mass_in += flow_rate
            else:
                total_mass_out += flow_rate
        
        # Calculate closure percentage
        if total_mass_in > 0:
            closure_percentage = (total_mass_out / total_mass_in) * 100.0
        else:
            closure_percentage = 0.0
        
        return MassBalanceSummary(
            total_mass_in=total_mass_in,
            total_mass_out=total_mass_out,
            closure_percentage=closure_percentage
        )
    
    def build_energy_balance_summary(
        self, 
        run_data: SimulationRunData
    ) -> EnergyBalanceSummary:
        """
        Calculate energy balance summary from run data.
        
        Args:
            run_data: Complete simulation run data
            
        Returns:
            EnergyBalanceSummary object with totals and closure
        """
        total_heat_input = 0.0
        total_heat_output = 0.0
        total_power = 0.0
        
        # Sum heat duties from equipment
        for equipment in run_data.equipment_list:
            eq_data = equipment.get("data", {})
            
            # Get duty
            duty = eq_data.get("duty_kW") or eq_data.get("heat_duty") or 0.0
            
            # Positive duty = heat input, negative = heat output
            if duty > 0:
                total_heat_input += duty
            else:
                total_heat_output += abs(duty)
            
            # Get power consumption
            power = eq_data.get("power") or 0.0
            total_power += power
        
        # Calculate closure percentage
        total_energy_in = total_heat_input + total_power
        if total_energy_in > 0:
            closure_percentage = (total_heat_output / total_energy_in) * 100.0
        else:
            closure_percentage = 0.0
        
        return EnergyBalanceSummary(
            total_heat_input=total_heat_input,
            total_heat_output=total_heat_output,
            total_power=total_power,
            closure_percentage=closure_percentage
        )
