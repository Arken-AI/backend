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
    
    # Generate observations
    observations = await service.generate_observations(simulation_data, ...)
"""

import logging
from typing import Optional
from anthropic import AsyncAnthropic, APIError, APIConnectionError, RateLimitError

from app.config import settings
from app.services.report_data_collector import format_display_name
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
    - Observations and recommendations

    All methods return Optional[str] - None indicates failure and the section
    should be skipped in the final PDF (not replaced with placeholder text).
    """

    # =========================================================================
    # System Prompt
    # =========================================================================

    SYSTEM_PROMPT = """You are a senior process engineer and technical writer at ARKEN AI, a physics-based process simulation platform for the chemical, sugar, and general process industries.

You write narrative sections for detailed simulation reports (executive summaries, observations, recommendations). Your audience is process engineers, plant managers, and technical decision-makers who rely on these reports for design validation, optimization, and operational decisions.

Domain expertise:
- Thermodynamics (VLE, enthalpy, phase equilibria), heat and mass transfer, unit operations
- Equipment: distillation columns, heat exchangers, reactors, flash drums, pumps, compressors, evaporators, mixers, splitters
- Key metrics: mass/energy balance closure, separation efficiency, heat duties, product purity, recovery rates

Writing guidelines:
- Write in third person, past tense, professional engineering tone
- Be specific: always include numbers with units (e.g., "351.2 K (78.1°C)", "1,000 kg/hr", "99.5% purity")
- State facts from the data — do not speculate or invent values not present in the provided data
- Keep paragraphs concise (3-5 sentences each)
- Do NOT use markdown formatting (no **, ##, bullet points, or lists). Write in plain prose paragraphs only
- When closure is near 100%, state it positively. When it deviates, quantify the gap and note possible causes
- Round appropriately: temperatures to 1 decimal, pressures to 2 decimals, compositions to 1-2 decimals, closure to 4 decimals"""

    # =========================================================================
    # Prompt Templates
    # =========================================================================

    EXECUTIVE_SUMMARY_PROMPT = """Generate a comprehensive executive summary (3-4 paragraphs) for this simulation report.

=== SIMULATION METADATA ===
- Run ID: {run_id}
- Industry: {industry}
- Process: {process_name}
- Timestamp: {timestamp}
- Status: {status}

=== STREAM DATA ===
{stream_details}

=== EQUIPMENT DATA ===
{equipment_details}

=== MASS BALANCE ===
- Total Mass In: {mass_in:.2f} kg/hr
- Total Mass Out: {mass_out:.2f} kg/hr
- Closure: {mass_closure:.4f}%
- Status: {mass_balance_status}

=== ENERGY BALANCE ===
- Total Heat Input: {heat_in:.2f} kW
- Total Heat Output: {heat_out:.2f} kW
- Net Heat Duty: {net_heat:.2f} kW
- Status: {energy_balance_status}

Write a professional executive summary that:
1. States the purpose and scope of this {process_name} simulation
2. Describes the process configuration (number of equipment units, key streams)
3. Highlights key operating conditions (temperatures, pressures, compositions)
4. Summarizes mass and energy balance results with specific numbers
5. Notes the overall process efficiency and any significant findings"""

    OBSERVATIONS_PROMPT = """Generate detailed observations and recommendations based on the complete simulation data.

=== SIMULATION OVERVIEW ===
- Industry: {industry}
- Process: {process_name}
- Run ID: {run_id}

=== COMPLETE STREAM DATA ===
{stream_details}

=== COMPLETE EQUIPMENT DATA ===
{equipment_details}

=== MASS BALANCE ===
- Total Mass In: {mass_in:.2f} kg/hr
- Total Mass Out: {mass_out:.2f} kg/hr
- Closure: {mass_closure:.4f}%
- Status: {mass_status}

=== ENERGY BALANCE ===
- Heat Input: {heat_in:.2f} kW
- Heat Output: {heat_out:.2f} kW  
- Net Heat: {net_heat:.2f} kW
- Status: {energy_status}

Based on this data, generate 4-6 detailed observations that:
1. Analyze the mass and energy balance closure quality with specific numbers
2. Evaluate equipment performance (efficiencies, duties, operating conditions)
3. Assess stream compositions and product purity achieved
4. Identify any concerning conditions or limitations
5. Suggest specific optimization opportunities with expected benefits
6. Recommend next steps or areas for further investigation

Write each observation as a separate paragraph. Be specific and actionable."""

    def __init__(self):
        """Initialize the narrative generator service."""
        self._client: Optional[AsyncAnthropic] = None
        self._model = settings.report_llm_model
        self._max_tokens = settings.report_llm_max_tokens
        
        # Check if API key is available
        if not settings.anthropic_api_key:
            logger.warning(
                "Anthropic API key not configured. "
                "AI narrative generation will be disabled."
            )
    
    @property
    def client(self) -> Optional[AsyncAnthropic]:
        """Lazy-load the Anthropic client."""
        if self._client is None and settings.anthropic_api_key:
            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
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
            
            # Call the API asynchronously
            response = await self.client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=temperature,
                system=system_message or self.SYSTEM_PROMPT,
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
    # Helper Methods for Formatting Data
    # =========================================================================
    
    def _format_name(self, raw_id: str, raw_name: str = None) -> str:
        """
        Format a raw ID or name into a human-readable display name.
        
        Examples:
            dilute_ethanol_feed -> Dilute Ethanol Feed
            e01_column_to_cooler -> Column to Cooler
            concentration_column -> Concentration Column
        """
        # Use provided name if it's different from ID
        if raw_name and raw_name != raw_id:
            return raw_name
        
        name = raw_id
        
        # Remove common prefixes like e01_, s01_, etc.
        import re
        name = re.sub(r'^[es]\d+_', '', name)
        
        # Replace underscores with spaces
        name = name.replace('_', ' ')
        
        # Title case each word
        name = ' '.join(word.capitalize() for word in name.split())
        
        return name
    
    def _classify_stream_type(self, stream_id: str) -> str:
        """Classify stream as feed, product, intermediate, or waste."""
        sid_lower = stream_id.lower()
        
        if any(kw in sid_lower for kw in ['feed', 'inlet', 'input', 'raw']):
            return "Feed"
        elif any(kw in sid_lower for kw in ['product', 'distillate', 'output']):
            return "Product"
        elif any(kw in sid_lower for kw in ['waste', 'bottoms', 'reject', 'purge']):
            return "Waste"
        elif '_to_' in sid_lower:
            return "Intermediate"
        else:
            return "Product"
    
    def _format_stream_details(self, run_data: SimulationRunData) -> str:
        """Format all stream data for LLM context with nice names."""
        if not run_data.streams:
            return "No stream data available"
        
        lines = []
        for i, stream in enumerate(run_data.streams, 1):
            stream_id = stream.get("id", "Unknown")
            data = stream.get("data", {})
            
            # Get display name - prefer name from data, else format ID
            raw_name = data.get("name", stream_id)
            display_name = self._format_name(stream_id, raw_name)
            stream_type = self._classify_stream_type(stream_id)
            
            # Extract key properties
            flow_rate = data.get("flow_rate", 0)
            temp_K = data.get("temperature_K", 0)
            pressure_Pa = data.get("pressure_Pa", 0)
            composition = data.get("composition", {})
            phase = data.get("phase", "liquid")
            
            # Convert temperature to Celsius for readability
            temp_C = temp_K - 273.15 if temp_K else 0
            
            # Format composition with nice component names
            comp_parts = []
            for comp, frac in composition.items():
                comp_name = comp.replace('_', ' ').title()
                comp_parts.append(f"{comp_name}: {frac*100:.1f}%")
            comp_str = ", ".join(comp_parts) if comp_parts else "N/A"
            
            lines.append(f"""
Stream {i}: {display_name} ({stream_type})
  - Flow Rate: {flow_rate:.2f} kg/hr
  - Temperature: {temp_C:.1f}°C ({temp_K:.1f} K)
  - Pressure: {pressure_Pa/1000:.1f} kPa
  - Phase: {phase.capitalize()}
  - Composition: {comp_str}""")
        
        return "\n".join(lines)
    
    def _format_equipment_details(self, run_data: SimulationRunData) -> str:
        """Format all equipment data for LLM context with nice names."""
        if not run_data.equipment_list:
            return "No equipment data available"
        
        lines = []
        for i, eq in enumerate(run_data.equipment_list, 1):
            eq_id = eq.get("id", "Unknown")
            eq_name = eq.get("name", eq_id)
            eq_type = eq.get("type", "unknown")
            eq_data = eq.get("data", {})
            
            # Format display name
            display_name = self._format_name(eq_id, eq_name)
            display_type = self._format_name(eq_type)
            
            # Get metadata for detailed info
            metadata = eq_data.get("metadata", {})
            energy_streams = eq_data.get("energy_streams", {})
            
            lines.append(f"\nEquipment {i}: {display_name}")
            lines.append(f"  - Type: {display_type}")
            
            # Add energy data if available
            if energy_streams:
                for stream_name, stream_data in energy_streams.items():
                    duty_kW = stream_data.get("duty_kW", 0)
                    energy_type = stream_data.get("energy_type", "")
                    nice_name = self._format_name(stream_name)
                    lines.append(f"  - {nice_name}: {abs(duty_kW):.1f} kW ({energy_type})")
            
            # Add key metadata
            if metadata:
                # For distillation columns
                if "num_stages" in metadata:
                    lines.append(f"  - Number of Stages: {metadata.get('num_stages')}")
                    lines.append(f"  - Feed Stage: {metadata.get('feed_stage')}")
                    lines.append(f"  - Reflux Ratio: {metadata.get('reflux_ratio')}")
                    if metadata.get("condenser_duty_kW"):
                        lines.append(f"  - Condenser Duty: {metadata.get('condenser_duty_kW'):.2f} kW")
                    if metadata.get("reboiler_duty_kW"):
                        lines.append(f"  - Reboiler Duty: {metadata.get('reboiler_duty_kW'):.2f} kW")
                    sep = metadata.get("separation_achieved", {})
                    if sep:
                        lines.append(f"  - Light Key in Distillate: {sep.get('light_key_in_distillate', 0)*100:.2f}%")
                        lines.append(f"  - Heavy Key in Bottoms: {sep.get('heavy_key_in_bottoms', 0)*100:.2f}%")
                
                # For heat exchangers/coolers
                if "duty" in metadata and isinstance(metadata["duty"], dict):
                    duty_info = metadata["duty"]
                    lines.append(f"  - Total Duty: {duty_info.get('total_duty_kW', 0):.2f} kW")
                    lines.append(f"  - Inlet Temp: {metadata.get('inlet_temperature_K', 0) - 273.15:.1f}°C")
                    lines.append(f"  - Outlet Temp: {metadata.get('outlet_temperature_K', 0) - 273.15:.1f}°C")
                
                # Efficiency
                if "efficiency" in metadata:
                    lines.append(f"  - Efficiency: {metadata.get('efficiency')*100:.1f}%")
        
        return "\n".join(lines)
    
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
        
        # Format full stream and equipment details from raw data
        stream_details = self._format_stream_details(run_data)
        equipment_details = self._format_equipment_details(run_data)
        
        # Get mass balance values
        mass_in = mass_balance.total_mass_in if mass_balance else 0
        mass_out = mass_balance.total_mass_out if mass_balance else 0
        mass_closure = (mass_balance.closure_percentage if mass_balance and mass_balance.closure_percentage is not None else 100.0)

        # Determine mass balance status
        if mass_balance and mass_balance.closure_percentage is None:
            mass_status = "Unable to calculate - streams could not be categorized as inlet/outlet"
        else:
            closure_error = abs(100.0 - mass_closure)
            if closure_error < 0.01:
                mass_status = "Excellent - Perfect closure"
            elif closure_error < 0.1:
                mass_status = "Good - Minor imbalance"
            elif closure_error < 1.0:
                mass_status = "Acceptable - Small imbalance"
            else:
                mass_status = f"Warning - {closure_error:.2f}% imbalance"

        # Get energy balance values
        heat_in = energy_balance.total_heat_input if energy_balance else 0
        heat_out = energy_balance.total_heat_output if energy_balance else 0
        net_heat = heat_in - heat_out

        # Determine energy balance status
        if heat_in > 0 or heat_out > 0:
            energy_status = f"Heat duty: {abs(net_heat):.1f} kW net {'input' if net_heat > 0 else 'removal'}"
        else:
            energy_status = "No significant heat duties"

        # Format the prompt with full data
        formatted_name = format_display_name(run_data.process_name) if run_data.process_name else "Simulation"

        prompt = self.EXECUTIVE_SUMMARY_PROMPT.format(
            run_id=run_data.run_id,
            industry=run_data.industry or "Chemical Process",
            process_name=formatted_name,
            timestamp=run_data.timestamp.isoformat() if run_data.timestamp else "Unknown",
            status=run_data.status or "completed",
            stream_details=stream_details,
            equipment_details=equipment_details,
            mass_in=mass_in,
            mass_out=mass_out,
            mass_closure=mass_closure,
            mass_balance_status=mass_status,
            heat_in=heat_in,
            heat_out=heat_out,
            net_heat=net_heat,
            energy_balance_status=energy_status,
        )
        
        return await self._call_llm(prompt, temperature=0.5)

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
        
        # Format full stream and equipment details from raw data
        stream_details = self._format_stream_details(run_data)
        equipment_details = self._format_equipment_details(run_data)
        
        # Get mass balance values
        mass_in = mass_balance.total_mass_in if mass_balance else 0
        mass_out = mass_balance.total_mass_out if mass_balance else 0
        mass_closure = (mass_balance.closure_percentage if mass_balance and mass_balance.closure_percentage is not None else 100.0)

        # Determine mass balance status
        if mass_balance and mass_balance.closure_percentage is None:
            mass_status = "Unable to calculate - streams could not be categorized as inlet/outlet"
        else:
            closure_error = abs(100.0 - mass_closure)
            if closure_error < 0.01:
                mass_status = "Excellent - Perfect closure"
            elif closure_error < 0.1:
                mass_status = "Good - Minor imbalance"
            elif closure_error < 1.0:
                mass_status = "Acceptable - Small imbalance"
            else:
                mass_status = f"Warning - {closure_error:.2f}% imbalance"

        # Get energy balance values
        heat_in = energy_balance.total_heat_input if energy_balance else 0
        heat_out = energy_balance.total_heat_output if energy_balance else 0
        net_heat = heat_in - heat_out

        # Determine energy balance status
        if heat_in > 0 or heat_out > 0:
            energy_status = f"Heat duty: {abs(net_heat):.1f} kW net {'input' if net_heat > 0 else 'removal'}"
        else:
            energy_status = "No significant heat duties"

        formatted_name = format_display_name(run_data.process_name) if run_data.process_name else "Simulation"

        prompt = self.OBSERVATIONS_PROMPT.format(
            industry=run_data.industry or "Chemical Process",
            process_name=formatted_name,
            run_id=run_data.run_id,
            stream_details=stream_details,
            equipment_details=equipment_details,
            mass_in=mass_in,
            mass_out=mass_out,
            mass_closure=mass_closure,
            mass_status=mass_status,
            heat_in=heat_in,
            heat_out=heat_out,
            net_heat=net_heat,
            energy_status=energy_status,
        )
        
        return await self._call_llm(prompt, temperature=0.6)
