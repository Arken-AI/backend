"""
AI Narrative Generator Service

Generates AI-powered narrative sections for simulation reports using
direct Anthropic API calls. Returns None on failure (sections will be
skipped in the final PDF rather than showing placeholder text).

Usage:
    from app.services.narrative_generator import NarrativeGeneratorService
    
    service = NarrativeGeneratorService()
    
    # Generate individual sections
    summary = await service.generate_executive_summary(simulation_data)
    if summary is None:
        # Section will be skipped in PDF
        pass
    
    # Generate all equipment descriptions
    descriptions = await service.generate_all_equipment_descriptions(equipment_data)
"""

import logging
from typing import Optional, Dict, Any, List
from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError

from app.config import settings
from app.models.report import (
    SimulationRunData,
    StreamTableData,
    EquipmentTableData,
    MassBalanceSummary,
    EnergyBalanceSummary,
)

logger = logging.getLogger(__name__)


class NarrativeGeneratorService:
    """
    Service for generating AI-powered narrative sections for simulation reports.
    
    Uses direct Anthropic API calls (NOT MCP) to generate:
    - Executive summary
    - Process descriptions
    - Equipment descriptions
    - Observations and recommendations
    
    All methods return Optional[str] - None indicates failure and the section
    should be skipped in the final PDF (not replaced with placeholder text).
    """
    
    # =========================================================================
    # Prompt Templates
    # =========================================================================
    
    EXECUTIVE_SUMMARY_PROMPT = """You are a technical writer for a process engineering firm. Generate a concise executive summary (2-3 paragraphs) for a simulation report.

Simulation Details:
- Run ID: {run_id}
- Industry: {industry}
- Process: {process_name}
- Timestamp: {timestamp}
- Status: {status}

Key Results:
- Total Streams: {stream_count}
- Equipment Units: {equipment_count}
- Mass Balance Closure: {mass_balance_status}
- Energy Balance Closure: {energy_balance_status}

Feed Stream Summary:
{feed_summary}

Product Stream Summary:
{product_summary}

Write a professional executive summary that:
1. States the purpose of the simulation
2. Highlights key process conditions and configurations
3. Summarizes the main findings (mass/energy balance, efficiency)
4. Notes any significant observations

Write in third person, past tense. Be concise and technical."""

    PROCESS_DESCRIPTION_PROMPT = """You are a process engineer documenting a simulation study. Write a technical process description (1-2 paragraphs) for the following process section.

Process Section: {section_name}
Industry: {industry}

Equipment in this section:
{equipment_list}

Stream connections:
{stream_connections}

Operating conditions:
{operating_conditions}

Write a clear technical description that:
1. Explains the purpose of this process section
2. Describes how material flows through the equipment
3. Notes key operating parameters
4. Mentions any recycle streams or integrations

Use professional engineering language. Be specific about equipment types and stream properties."""

    EQUIPMENT_DESCRIPTION_PROMPT = """You are a process engineer documenting simulation results. Write a brief technical description (2-3 sentences) for this equipment unit.

Equipment: {equipment_name}
Type: {equipment_type}
Industry: {industry}

Inlet Streams:
{inlet_streams}

Outlet Streams:
{outlet_streams}

Operating Parameters:
{parameters}

Performance Metrics:
{performance}

Write a concise description that captures:
1. The equipment's function in the process
2. Key operating conditions
3. Notable performance metrics

Be technical and precise."""

    OBSERVATIONS_PROMPT = """You are a senior process engineer reviewing simulation results. Generate observations and recommendations (3-5 bullet points) based on the following data.

Simulation Overview:
- Industry: {industry}
- Process: {process_name}
- Run ID: {run_id}

Mass Balance Summary:
- Total Mass In: {mass_in} kg/s
- Total Mass Out: {mass_out} kg/s
- Closure Error: {mass_error}%
- Status: {mass_status}

Energy Balance Summary:
- Heat Input: {heat_in} kW
- Heat Output: {heat_out} kW
- Net Heat: {net_heat} kW
- Status: {energy_status}

Key Equipment Performance:
{equipment_performance}

Stream Quality Indicators:
{stream_quality}

Generate professional observations that:
1. Comment on mass/energy balance quality
2. Note any equipment operating near limits
3. Identify potential optimization opportunities
4. Flag any concerning stream conditions
5. Suggest areas for further investigation

Format as bullet points. Be specific and actionable."""

    def __init__(self):
        """Initialize the narrative generator service."""
        self._client: Optional[Anthropic] = None
        self._model = settings.report_llm_model
        self._max_tokens = settings.report_llm_max_tokens
        
        # Check if API key is available
        if not settings.anthropic_api_key:
            logger.warning(
                "Anthropic API key not configured. "
                "AI narrative generation will be disabled."
            )
    
    @property
    def client(self) -> Optional[Anthropic]:
        """Lazy-load the Anthropic client."""
        if self._client is None and settings.anthropic_api_key:
            self._client = Anthropic(api_key=settings.anthropic_api_key)
        return self._client
    
    @property
    def is_available(self) -> bool:
        """Check if AI generation is available."""
        return settings.anthropic_api_key is not None
    
    # =========================================================================
    # Core LLM Method
    # =========================================================================
    
    async def _call_llm(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """
        Call the Anthropic API to generate text.
        
        Args:
            prompt: The user prompt to send
            system_message: Optional system message for context
            temperature: Generation temperature (0.0-1.0)
            
        Returns:
            Generated text or None on failure
        """
        if not self.is_available:
            logger.warning("AI generation unavailable - no API key configured")
            return None
        
        if not self.client:
            logger.error("Failed to initialize Anthropic client")
            return None
        
        try:
            # Build the message request
            messages = [{"role": "user", "content": prompt}]
            
            # Call the API (synchronous, but we wrap in async context)
            response = self.client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=temperature,
                system=system_message or "You are a professional technical writer for process engineering reports.",
                messages=messages,
            )
            
            # Extract text from response
            if response.content and len(response.content) > 0:
                text_content = response.content[0].text
                if text_content and text_content.strip():
                    logger.debug(f"Generated {len(text_content)} characters")
                    return text_content.strip()
            
            logger.warning("Empty response from LLM")
            return None
            
        except RateLimitError as e:
            logger.error(f"Rate limit exceeded: {e}")
            return None
        except APIConnectionError as e:
            logger.error(f"API connection error: {e}")
            return None
        except APIError as e:
            logger.error(f"API error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error calling LLM: {e}", exc_info=True)
            return None
    
    # =========================================================================
    # Narrative Generation Methods
    # =========================================================================
    
    async def generate_executive_summary(
        self,
        run_data: SimulationRunData,
        stream_table: Optional[StreamTableData] = None,
        equipment_table: Optional[EquipmentTableData] = None,
        mass_balance: Optional[MassBalanceSummary] = None,
        energy_balance: Optional[EnergyBalanceSummary] = None,
    ) -> Optional[str]:
        """
        Generate an executive summary for the simulation report.
        
        Args:
            run_data: Simulation run metadata and results
            stream_table: Formatted stream data
            equipment_table: Formatted equipment data
            mass_balance: Mass balance summary
            energy_balance: Energy balance summary
            
        Returns:
            Executive summary text or None on failure
        """
        logger.info(f"Generating executive summary for run {run_data.run_id}")
        
        # Build feed/product summaries from stream table
        feed_summary = "Not available"
        product_summary = "Not available"
        
        if stream_table and stream_table.streams:
            feed_streams = [s for s in stream_table.streams if s.get("stream_type") == "feed"]
            product_streams = [s for s in stream_table.streams if s.get("stream_type") == "product"]
            
            if feed_streams:
                feed_lines = []
                for s in feed_streams[:5]:  # Limit to 5
                    name = s.get("name", "Unknown")
                    flow = s.get("mass_flow_kg_s", s.get("flow_rate", "N/A"))
                    temp = s.get("temperature_K", s.get("temperature", "N/A"))
                    feed_lines.append(f"  - {name}: {flow} kg/s at {temp} K")
                feed_summary = "\n".join(feed_lines)
            
            if product_streams:
                product_lines = []
                for s in product_streams[:5]:  # Limit to 5
                    name = s.get("name", "Unknown")
                    flow = s.get("mass_flow_kg_s", s.get("flow_rate", "N/A"))
                    temp = s.get("temperature_K", s.get("temperature", "N/A"))
                    product_lines.append(f"  - {name}: {flow} kg/s at {temp} K")
                product_summary = "\n".join(product_lines)
        
        # Determine balance statuses
        mass_status = "Not calculated"
        if mass_balance:
            # Calculate closure error from closure_percentage (100% = perfect closure)
            closure_error = abs(100.0 - mass_balance.closure_percentage)
            if closure_error < 0.1:
                mass_status = f"Closed ({closure_error:.3f}% error)"
            else:
                mass_status = f"Open ({closure_error:.2f}% error)"
        
        energy_status = "Not calculated"
        if energy_balance:
            net_heat = energy_balance.total_heat_input - energy_balance.total_heat_output
            energy_status = f"Net heat: {net_heat:.1f} kW"
        
        # Format the prompt
        prompt = self.EXECUTIVE_SUMMARY_PROMPT.format(
            run_id=run_data.run_id,
            industry=run_data.industry or "Process",
            process_name=run_data.process_name or "Simulation",
            timestamp=run_data.timestamp.isoformat() if run_data.timestamp else "Unknown",
            status=run_data.status or "completed",
            stream_count=len(stream_table.streams) if stream_table else 0,
            equipment_count=len(equipment_table.equipment) if equipment_table else 0,
            mass_balance_status=mass_status,
            energy_balance_status=energy_status,
            feed_summary=feed_summary,
            product_summary=product_summary,
        )
        
        return await self._call_llm(prompt, temperature=0.5)
    
    async def generate_process_description(
        self,
        section_name: str,
        industry: str,
        equipment_list: List[Dict[str, Any]],
        stream_data: List[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Generate a description for a process section.
        
        Args:
            section_name: Name of the process section
            industry: Industry type (e.g., "sugar", "chemical")
            equipment_list: List of equipment in this section
            stream_data: Stream data for connections
            
        Returns:
            Process description text or None on failure
        """
        logger.info(f"Generating process description for section: {section_name}")
        
        # Format equipment list
        equip_lines = []
        for eq in equipment_list[:10]:  # Limit to 10
            name = eq.get("name", eq.get("equipment_id", "Unknown"))
            eq_type = eq.get("type", eq.get("equipment_type", "Unknown"))
            equip_lines.append(f"  - {name} ({eq_type})")
        equipment_str = "\n".join(equip_lines) if equip_lines else "No equipment data"
        
        # Format stream connections
        connection_lines = []
        for stream in stream_data[:10]:  # Limit to 10
            name = stream.get("name", "Unknown")
            source = stream.get("source", "Unknown")
            dest = stream.get("destination", "Unknown")
            connection_lines.append(f"  - {name}: {source} → {dest}")
        connections_str = "\n".join(connection_lines) if connection_lines else "No connection data"
        
        # Extract operating conditions
        conditions = []
        for eq in equipment_list[:5]:
            params = eq.get("parameters", eq.get("params", {}))
            if params:
                for key, value in list(params.items())[:3]:
                    conditions.append(f"  - {key}: {value}")
        conditions_str = "\n".join(conditions) if conditions else "Standard conditions"
        
        prompt = self.PROCESS_DESCRIPTION_PROMPT.format(
            section_name=section_name,
            industry=industry,
            equipment_list=equipment_str,
            stream_connections=connections_str,
            operating_conditions=conditions_str,
        )
        
        return await self._call_llm(prompt, temperature=0.6)
    
    async def generate_equipment_description(
        self,
        equipment_name: str,
        equipment_type: str,
        industry: str,
        inlet_streams: List[Dict[str, Any]],
        outlet_streams: List[Dict[str, Any]],
        parameters: Dict[str, Any],
        performance: Dict[str, Any],
    ) -> Optional[str]:
        """
        Generate a description for a single equipment unit.
        
        Args:
            equipment_name: Name/ID of the equipment
            equipment_type: Type of equipment (e.g., "mill", "evaporator")
            industry: Industry type
            inlet_streams: Inlet stream data
            outlet_streams: Outlet stream data
            parameters: Operating parameters
            performance: Performance metrics
            
        Returns:
            Equipment description text or None on failure
        """
        logger.debug(f"Generating description for equipment: {equipment_name}")
        
        # Format inlet streams
        inlet_lines = []
        for s in inlet_streams[:5]:
            name = s.get("name", "Unknown")
            flow = s.get("mass_flow_kg_s", s.get("flow_rate", "N/A"))
            temp = s.get("temperature_K", s.get("temperature", "N/A"))
            inlet_lines.append(f"  - {name}: {flow} kg/s, {temp} K")
        inlet_str = "\n".join(inlet_lines) if inlet_lines else "No inlet data"
        
        # Format outlet streams
        outlet_lines = []
        for s in outlet_streams[:5]:
            name = s.get("name", "Unknown")
            flow = s.get("mass_flow_kg_s", s.get("flow_rate", "N/A"))
            temp = s.get("temperature_K", s.get("temperature", "N/A"))
            outlet_lines.append(f"  - {name}: {flow} kg/s, {temp} K")
        outlet_str = "\n".join(outlet_lines) if outlet_lines else "No outlet data"
        
        # Format parameters
        param_lines = []
        for key, value in list(parameters.items())[:8]:
            param_lines.append(f"  - {key}: {value}")
        param_str = "\n".join(param_lines) if param_lines else "No parameter data"
        
        # Format performance
        perf_lines = []
        for key, value in list(performance.items())[:5]:
            perf_lines.append(f"  - {key}: {value}")
        perf_str = "\n".join(perf_lines) if perf_lines else "No performance data"
        
        prompt = self.EQUIPMENT_DESCRIPTION_PROMPT.format(
            equipment_name=equipment_name,
            equipment_type=equipment_type,
            industry=industry,
            inlet_streams=inlet_str,
            outlet_streams=outlet_str,
            parameters=param_str,
            performance=perf_str,
        )
        
        return await self._call_llm(prompt, temperature=0.5)
    
    async def generate_all_equipment_descriptions(
        self,
        equipment_table: EquipmentTableData,
        stream_table: Optional[StreamTableData],
        industry: str,
    ) -> Dict[str, Optional[str]]:
        """
        Generate descriptions for all equipment units.
        
        Args:
            equipment_table: Equipment data
            stream_table: Stream data for context
            industry: Industry type
            
        Returns:
            Dict mapping equipment_id to description (or None if failed)
        """
        logger.info(f"Generating descriptions for {len(equipment_table.equipment)} equipment units")
        
        descriptions: Dict[str, Optional[str]] = {}
        
        # Build stream lookup by equipment
        inlet_map: Dict[str, List[Dict]] = {}
        outlet_map: Dict[str, List[Dict]] = {}
        
        if stream_table:
            for stream in stream_table.streams:
                source = stream.get("source", "")
                dest = stream.get("destination", "")
                
                if source:
                    if source not in outlet_map:
                        outlet_map[source] = []
                    outlet_map[source].append(stream)
                
                if dest:
                    if dest not in inlet_map:
                        inlet_map[dest] = []
                    inlet_map[dest].append(stream)
        
        # Generate description for each equipment
        for equipment in equipment_table.equipment:
            eq_id = equipment.get("equipment_id", equipment.get("name", "Unknown"))
            eq_type = equipment.get("type", equipment.get("equipment_type", "Unknown"))
            params = equipment.get("parameters", equipment.get("params", {}))
            perf = equipment.get("performance", equipment.get("results", {}))
            
            inlet_streams = inlet_map.get(eq_id, [])
            outlet_streams = outlet_map.get(eq_id, [])
            
            description = await self.generate_equipment_description(
                equipment_name=eq_id,
                equipment_type=eq_type,
                industry=industry,
                inlet_streams=inlet_streams,
                outlet_streams=outlet_streams,
                parameters=params,
                performance=perf,
            )
            
            descriptions[eq_id] = description
        
        successful = sum(1 for d in descriptions.values() if d is not None)
        logger.info(f"Generated {successful}/{len(descriptions)} equipment descriptions")
        
        return descriptions
    
    async def generate_observations(
        self,
        run_data: SimulationRunData,
        mass_balance: Optional[MassBalanceSummary],
        energy_balance: Optional[EnergyBalanceSummary],
        equipment_table: Optional[EquipmentTableData],
        stream_table: Optional[StreamTableData],
    ) -> Optional[str]:
        """
        Generate observations and recommendations for the simulation.
        
        Args:
            run_data: Simulation run metadata
            mass_balance: Mass balance summary
            energy_balance: Energy balance summary
            equipment_table: Equipment data
            stream_table: Stream data
            
        Returns:
            Observations text or None on failure
        """
        logger.info(f"Generating observations for run {run_data.run_id}")
        
        # Mass balance details
        mass_in = "N/A"
        mass_out = "N/A"
        mass_error = "N/A"
        mass_status = "Not calculated"
        
        if mass_balance:
            mass_in = f"{mass_balance.total_mass_in:.3f}" if mass_balance.total_mass_in else "N/A"
            mass_out = f"{mass_balance.total_mass_out:.3f}" if mass_balance.total_mass_out else "N/A"
            # Calculate closure error from closure_percentage
            closure_error = abs(100.0 - mass_balance.closure_percentage)
            mass_error = f"{closure_error:.4f}"
            mass_status = "Closed" if closure_error < 0.1 else "Open"
        
        # Energy balance details
        heat_in = "N/A"
        heat_out = "N/A"
        net_heat = "N/A"
        energy_status = "Not calculated"
        
        if energy_balance:
            heat_in = f"{energy_balance.total_heat_input:.1f}" if energy_balance.total_heat_input else "N/A"
            heat_out = f"{energy_balance.total_heat_output:.1f}" if energy_balance.total_heat_output else "N/A"
            net_heat_val = energy_balance.total_heat_input - energy_balance.total_heat_output
            net_heat = f"{net_heat_val:.1f}"
            energy_status = "Balanced" if abs(net_heat_val) < 10 else "Unbalanced"
        
        # Equipment performance summary
        equip_perf_lines = []
        if equipment_table:
            for eq in equipment_table.equipment[:5]:
                name = eq.get("equipment_id", eq.get("name", "Unknown"))
                perf = eq.get("performance", eq.get("results", {}))
                if perf:
                    key_metrics = list(perf.items())[:2]
                    metrics_str = ", ".join(f"{k}: {v}" for k, v in key_metrics)
                    equip_perf_lines.append(f"  - {name}: {metrics_str}")
        equip_perf_str = "\n".join(equip_perf_lines) if equip_perf_lines else "No performance data"
        
        # Stream quality summary
        stream_quality_lines = []
        if stream_table:
            for stream in stream_table.streams[:5]:
                name = stream.get("name", "Unknown")
                phase = stream.get("phase", "Unknown")
                temp = stream.get("temperature_K", stream.get("temperature", "N/A"))
                stream_quality_lines.append(f"  - {name}: {phase} phase at {temp} K")
        stream_quality_str = "\n".join(stream_quality_lines) if stream_quality_lines else "No stream data"
        
        prompt = self.OBSERVATIONS_PROMPT.format(
            industry=run_data.industry or "Process",
            process_name=run_data.process_name or "Simulation",
            run_id=run_data.run_id,
            mass_in=mass_in,
            mass_out=mass_out,
            mass_error=mass_error,
            mass_status=mass_status,
            heat_in=heat_in,
            heat_out=heat_out,
            net_heat=net_heat,
            energy_status=energy_status,
            equipment_performance=equip_perf_str,
            stream_quality=stream_quality_str,
        )
        
        return await self._call_llm(prompt, temperature=0.6)
