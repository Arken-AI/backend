"""
Orchestration Service - Agentic Loop Implementation

Central coordinator that connects all components with an agentic loop:
- Context Manager: Track conversation state
- Tool Registry: Get available tools
- Policy Engine: Enforce business rules
- LLM Provider: Get tool decisions from Claude/Gemini
- MCP Client: Execute tools

Agentic Loop Flow:
1. User message → Load context
2. Get tools from registry
3. LOOP:
   a. Send message + tools + previous results to LLM
   b. If LLM wants tools → Execute them, send results back
   c. If LLM has no more tool calls → Return text response
4. Return final response to user

This matches Claude Desktop behavior where LLM can:
- See tool errors and recover by calling different tools
- Chain multiple tools together
- Complete complex multi-step tasks
"""

import json
import traceback
from typing import Dict, Any, List, Optional
from datetime import datetime

from app.services.context_manager import ContextManager
from app.services.tool_registry import ToolRegistry
from app.services.event_emitter import EventEmitter
from app.core.policy_engine import PolicyEngine, PolicyDecision
from app.core.llm_provider import ClaudeProvider
from app.core.mcp_client import MCPClient, MCPServerConfig

# Gemini provider - commented out for future use with free tier
# from app.core.llm_gemini_provider import GeminiProvider
# from app.config import settings


class OrchestrationService:
    """
    Central orchestrator for chat-based process simulation.
    
    Uses an agentic loop pattern where:
    - LLM proposes tool calls
    - Backend executes tools and returns results
    - LLM sees results (success AND errors) and decides next action
    - Loop continues until LLM provides final text response
    """
    
    def __init__(
        self,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        policy_engine: PolicyEngine,
        mcp_client: MCPClient,
        event_emitter: Optional[EventEmitter] = None,
        anthropic_api_key: str = None,
        llm_provider: str = "claude"  # Can be "claude" or "gemini" in future
    ):
        """
        Initialize orchestration service.
        
        Args:
            context_manager: Context tracking service
            tool_registry: Tool metadata registry
            policy_engine: Policy enforcement engine
            mcp_client: MCP client for tool execution
            event_emitter: Event emitter for real-time updates (optional)
            anthropic_api_key: Anthropic API key for Claude (required for claude provider)
            llm_provider: "claude" (default) or "gemini" for future use
        """
        self.context_manager = context_manager
        self.tool_registry = tool_registry
        self.policy_engine = policy_engine
        self.mcp_client = mcp_client
        self.event_emitter = event_emitter
        
        # Initialize LLM provider
        self.llm_provider_name = llm_provider
        
        if self.llm_provider_name == "claude":
            # Claude provider (claude-sonnet-4-20250514)
            self.llm = ClaudeProvider(
                api_key=anthropic_api_key
            )
            print(f"Orchestration service initialized with Claude (claude-sonnet-4-20250514)")
        
        # Gemini provider - commented out for future use with free tier
        # elif self.llm_provider_name == "gemini":
        #     self.llm = GeminiProvider(
        #         api_key=settings.google_api_key,
        #         model=settings.llm_model_gemini,
        #         max_tokens=settings.llm_max_tokens,
        #         temperature=settings.llm_temperature
        #     )
        #     print(f"Orchestration service initialized with Gemini")
        
        else:
            raise ValueError(f"Unknown LLM provider: {self.llm_provider_name}. Currently only 'claude' is supported.")
    
    # =========================================================================
    # AGENTIC LOOP - Main Entry Point
    # =========================================================================
    
    async def process_message(
        self,
        conversation_id: str,
        user_message: str,
        user_id: str = "default_user"
    ) -> Dict[str, Any]:
        """
        Main entry point: Process user message using an agentic loop.
        
        The agentic loop allows the LLM to:
        1. Call tools and see results (including errors)
        2. Decide what to do next (call more tools or respond)
        3. Recover from errors by calling different tools
        4. Continue until it has a final answer
        
        This matches Claude Desktop's behavior where the LLM can:
        - Try a tool, see it fail, then try a different approach
        - Chain multiple tools together
        - Handle errors gracefully
        
        Args:
            conversation_id: Unique conversation identifier
            user_message: User's message
            user_id: User identifier
            
        Returns:
            {
                "status": "success" | "error",
                "message": "LLM response text",
                "tool_calls": [...],
                "iterations": N,
                "conversation_id": "...",
                "token_usage": {...}
            }
        """
        MAX_ITERATIONS = 10
        MAX_TOOL_CALLS = 15  # Safety limit on total tool calls
        
        # Initialize token tracking
        total_input_tokens = 0
        total_output_tokens = 0
        
        try:
            print(f"Processing message for conversation {conversation_id}")
            
            # Step 1: Load or create context
            context = await self._get_or_create_context(conversation_id, user_id)
            
            # Step 2: Get available tools
            tools = await self._get_available_tools()
            
            # Track state across iterations
            all_tool_results = []
            total_tool_calls = 0
            
            # Build conversation history for multi-turn
            # Load previous messages from context for continuity
            previous_messages = await self.context_manager.get_messages(conversation_id)
            
            conversation_history = []
            
            # Add previous conversation turns (limit to last 10 for context window)
            for msg in previous_messages[-10:]:
                conversation_history.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            
            # Add current user message
            conversation_history.append({"role": "user", "content": user_message})
            
            # Save user message to context for future turns
            await self.context_manager.add_message(conversation_id, "user", user_message)
            
            print(f"Conversation history has {len(conversation_history)} messages")
            
            # Emit thinking start
            if self.event_emitter:
                await self.event_emitter.emit_thinking_start(conversation_id)
            
            # =====================================================
            # AGENTIC LOOP: Continue until LLM stops calling tools
            # =====================================================
            for iteration in range(MAX_ITERATIONS):
                print(f"=== Agentic Loop Iteration {iteration + 1} ===")
                
                # Call LLM with conversation history
                thinking_start = datetime.now()
                llm_response = await self._call_llm_with_history(
                    conversation_history, 
                    tools, 
                    context
                )
                thinking_duration_ms = int((datetime.now() - thinking_start).total_seconds() * 1000)
                
                # Track tokens - handle both dict and object usage attributes
                if hasattr(llm_response, 'usage'):
                    usage = llm_response.usage
                    if isinstance(usage, dict):
                        # Gemini returns dict
                        total_input_tokens += usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("output_tokens", 0)
                    else:
                        # Claude returns Anthropic Usage object
                        total_input_tokens += getattr(usage, "input_tokens", 0)
                        total_output_tokens += getattr(usage, "output_tokens", 0)
                
                # Extract response info
                if hasattr(llm_response, 'has_tool_calls'):
                    has_tools = llm_response.has_tool_calls
                    tool_calls_list = llm_response.tool_calls if has_tools else []
                    message_text = llm_response.text if hasattr(llm_response, 'text') else ""
                else:
                    has_tools = bool(llm_response.get("tool_calls"))
                    tool_calls_list = llm_response.get("tool_calls", [])
                    message_text = llm_response.get("message", "")
                
                # =====================================================
                # EXIT CONDITION: LLM has no more tool calls
                # =====================================================
                if not has_tools:
                    print(f"LLM finished after {iteration + 1} iterations with text response")
                    
                    # Save assistant response to context for future turns
                    await self.context_manager.add_message(
                        conversation_id, 
                        "assistant", 
                        message_text,
                        metadata={"iterations": iteration + 1, "tool_calls": len(all_tool_results)}
                    )
                    
                    # Emit thinking end
                    if self.event_emitter:
                        await self.event_emitter.emit_thinking_end(conversation_id, thinking_duration_ms)
                        await self.event_emitter.emit_message_final(
                            conversation_id,
                            message_text,
                            role="assistant",
                            metadata={"iterations": iteration + 1, "tool_calls": len(all_tool_results)}
                        )
                    
                    return {
                        "status": "success",
                        "message": message_text,
                        "tool_calls": all_tool_results,
                        "iterations": iteration + 1,
                        "conversation_id": conversation_id,
                        "context": await self.context_manager.get_context(conversation_id),
                        "token_usage": {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "total_tokens": total_input_tokens + total_output_tokens
                        }
                    }
                
                # =====================================================
                # TOOL EXECUTION: Process each tool call
                # =====================================================
                print(f"LLM requested {len(tool_calls_list)} tool calls")
                
                # Add assistant message with tool calls to history
                conversation_history.append({
                    "role": "assistant",
                    "tool_calls": tool_calls_list
                })
                
                # Execute tools and collect results
                iteration_tool_results = []
                
                for tool_call in tool_calls_list:
                    # Safety check: don't exceed tool call limit
                    total_tool_calls += 1
                    if total_tool_calls > MAX_TOOL_CALLS:
                        print(f"WARNING: " + f"Tool call limit exceeded ({MAX_TOOL_CALLS})")
                        iteration_tool_results.append({
                            "name": "system",
                            "result": {
                                "status": "error",
                                "error": f"Tool call limit exceeded. Maximum {MAX_TOOL_CALLS} calls allowed."
                            }
                        })
                        break
                    
                    # Extract tool info
                    if isinstance(tool_call, dict):
                        tool_name = tool_call.get("name")
                        tool_params = tool_call.get("input", tool_call.get("arguments", {}))
                    else:
                        tool_name = tool_call.name
                        tool_params = tool_call.input if hasattr(tool_call, 'input') else tool_call.arguments
                    
                    print(f"Executing tool: {tool_name}")
                    
                    # Check policy
                    policy_result = await self._enforce_policy(tool_name, context, tool_params)
                    
                    if policy_result["decision"] != "allow":
                        # Policy denied - send error back to LLM (not to user!)
                        print(f"WARNING: " + f"Policy DENIED: {tool_name} - {policy_result['reason']}")
                        
                        if self.event_emitter:
                            await self.event_emitter.emit_app_error(
                                conversation_id,
                                "policy_violation",
                                f"Tool {tool_name} denied: {policy_result['reason']}",
                                details={"tool": tool_name, "reason": policy_result['reason']},
                                recoverable=True
                            )
                        
                        # Send error back to LLM so it can recover
                        iteration_tool_results.append({
                            "name": tool_name,
                            "result": {
                                "status": "error",
                                "error": f"Policy denied: {policy_result['reason']}"
                            }
                        })
                        
                        all_tool_results.append({
                            "tool": tool_name,
                            "status": "denied",
                            "reason": policy_result["reason"]
                        })
                        continue
                    
                    # Execute tool
                    if self.event_emitter:
                        await self.event_emitter.emit_tool_start(
                            conversation_id,
                            tool_name,
                            tool_params
                        )
                    
                    tool_start_time = datetime.now()
                    result = await self._execute_tool(tool_name, tool_params)
                    tool_duration_ms = int((datetime.now() - tool_start_time).total_seconds() * 1000)
                    
                    # DEBUG: Log the result keys and calc_run_id for simulation tools
                    if tool_name in ["simulate_process", "simulate_equipment"]:
                        print(f"[DEBUG] Simulation result keys: {list(result.keys())}")
                        print(f"[DEBUG] calc_run_id: {result.get('calc_run_id')}")
                        print(f"[DEBUG] run_id: {result.get('run_id')}")
                        print(f"[DEBUG] status: {result.get('status')}")
                    
                    # Emit tool end
                    if self.event_emitter:
                        status = "success" if result.get("status") == "success" else "error"
                        summary = result.get("message", f"{tool_name} completed")
                        error_msg = result.get("error") if status == "error" else None
                        await self.event_emitter.emit_tool_end(
                            conversation_id,
                            tool_name,
                            status,
                            tool_duration_ms,
                            summary,
                            error_message=error_msg
                        )
                    
                    # Update context
                    await self._update_context(
                        conversation_id,
                        tool_name,
                        result,
                        tool_params
                    )
                    
                    # Reload context for next tool call
                    context = await self.context_manager.get_context(conversation_id)
                    
                    # Add to results - include BOTH success AND error results
                    # This is the key: LLM needs to see errors to recover!
                    iteration_tool_results.append({
                        "name": tool_name,
                        "result": result
                    })
                    
                    all_tool_results.append({
                        "tool": tool_name,
                        "status": "executed",
                        "result": result
                    })
                
                # =====================================================
                # SEND RESULTS BACK TO LLM (including errors!)
                # =====================================================
                # Add tool results to conversation history
                conversation_history.append({
                    "role": "tool",
                    "tool_results": iteration_tool_results
                })
                
                print(f"Sent {len(iteration_tool_results)} tool results back to LLM")
                
                # Continue loop - LLM will see results in next iteration
            
            # =====================================================
            # MAX ITERATIONS REACHED
            # =====================================================
            print(f"WARNING: " + f"Max iterations ({MAX_ITERATIONS}) reached")
            
            if self.event_emitter:
                await self.event_emitter.emit_thinking_end(conversation_id, 0)
            
            # Return partial results
            return {
                "status": "error",
                "message": "I apologize, but I couldn't complete your request within the allowed iterations. Here's what I was able to do:\n\n" + 
                          self._format_results_for_user(all_tool_results),
                "tool_calls": all_tool_results,
                "iterations": MAX_ITERATIONS,
                "conversation_id": conversation_id,
                "context": await self.context_manager.get_context(conversation_id),
                "token_usage": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens
                }
            }
            
        except Exception as e:
            print(f"ERROR: " + f"Error processing message: {e}"); import traceback; traceback.print_exc()
            
            if self.event_emitter:
                await self.event_emitter.emit_app_error(
                    conversation_id,
                    "internal_error",
                    f"Error processing your request: {str(e)}",
                    details={"exception": str(e)},
                    recoverable=True
                )
            
            return {
                "status": "error",
                "message": f"Error processing your request: {str(e)}",
                "conversation_id": conversation_id,
                "token_usage": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens
                }
            }
    
    # =========================================================================
    # LLM Communication
    # =========================================================================
    
    async def _call_llm_with_history(
        self,
        conversation_history: List[Dict[str, Any]],
        tools: List[Any],
        context: Dict[str, Any]
    ) -> Any:
        """
        Call LLM with full conversation history including tool results.
        
        This enables the agentic loop by preserving:
        - User message
        - Assistant tool calls
        - Tool results (success and errors)
        
        The LLM sees the full history and decides what to do next.
        """
        # Build system prompt based on LLM provider
        # Claude is naturally agentic - minimal prompting needed
        # Gemini needs explicit instructions to be agentic
        
        if self.llm_provider_name == "claude":
            # Claude is naturally agentic - just provide context
            system_parts = [
                "You are a process simulation assistant with tools for industrial process simulation."
            ]
        else:
            # Gemini needs explicit instructions to behave agentically
            system_parts = [
                "You are a process simulation assistant. You MUST use tools to complete user requests.",
                "",
                "CRITICAL INSTRUCTIONS:",
                "1. ALWAYS call tools first - never respond with just text when tools can help",
                "2. When a tool returns an error, DO NOT apologize or ask the user - instead:",
                "   - If process_id not found: Call list_processes to find correct process names",
                "   - If validation fails: Fix the parameters and try again",
                "   - If any error occurs: Try alternative approaches using other tools",
                "3. Keep calling tools until you successfully complete the user's request",
                "4. Only respond with text AFTER you have successfully used tools to get results",
                "",
                "WORKFLOW for simulation requests:",
                "1. Call validate_process_inputs first",
                "2. If process not found, call list_processes to discover correct name",
                "3. Retry validate_process_inputs with correct process name",
                "4. Call simulate_process after successful validation",
                "5. Summarize results for the user",
                "",
                "NEVER say 'I cannot' or 'Would you like me to' - just DO IT with tools."
            ]
        
        # Add context if available
        if context.get("current_industry") or context.get("current_process"):
            context_info = []
            if context.get("current_industry"):
                context_info.append(f"Industry: {context['current_industry']}")
            if context.get("current_process"):
                context_info.append(f"Process: {context['current_process']}")
            system_parts.append("Current context: " + ", ".join(context_info))
        
        # Add simulation summary for follow-up questions
        if context.get("last_simulation_summary"):
            summary = context["last_simulation_summary"]
            print(f"Including simulation summary in prompt: run_id={summary.get('run_id')}, type={summary.get('simulation_type')}, equipment_count={len(summary.get('equipment_summary', {}))}")
            summary_parts = [
                "",
                "=== PREVIOUS SIMULATION RESULTS ===",
                "(Use this data to answer follow-up questions without calling tools)",
                "",
                f"Run ID: {summary.get('run_id')}",
                f"Type: {summary.get('simulation_type')}",
                f"Process/Equipment: {summary.get('process_id') or summary.get('equipment_type')}",
                f"Status: {summary.get('status')}",
            ]
            
            # Include inputs used
            if summary.get('inputs_used'):
                summary_parts.append("")
                summary_parts.append("Inputs Used:")
                summary_parts.append(json.dumps(summary.get('inputs_used', {}), indent=2))
            
            # Include KPIs/Summary
            if summary.get('kpis'):
                summary_parts.append("")
                summary_parts.append("KPIs/Summary:")
                summary_parts.append(json.dumps(summary.get('kpis', {}), indent=2))
            
            # Include equipment details (heater, evaporator, etc.)
            if summary.get('equipment_summary'):
                summary_parts.append("")
                summary_parts.append("Equipment Results (heater, evaporator, clarifier, etc.):")
                summary_parts.append(json.dumps(summary.get('equipment_summary', {}), indent=2))
            
            # Include boiling house if available
            if summary.get('boiling_house'):
                summary_parts.append("")
                summary_parts.append("Boiling House Summary:")
                summary_parts.append(json.dumps(summary.get('boiling_house', {}), indent=2))
            
            # Include balances if available
            if summary.get('balances'):
                summary_parts.append("")
                summary_parts.append("Balance Errors:")
                summary_parts.append(json.dumps(summary.get('balances', {}), indent=2))
            
            # Include warnings
            if summary.get('warnings'):
                summary_parts.append("")
                summary_parts.append(f"Warnings: {summary.get('warnings')}")
            
            summary_parts.append("")
            summary_parts.append("=== END SIMULATION RESULTS ===")
            summary_parts.append("Use this data for follow-up questions. Only call get_run if user needs MORE detailed data.")
            system_parts.extend(summary_parts)
        else:
            print(f"No simulation summary in context for follow-up")
        
        system = "\n".join(system_parts)
        
        print(f"Calling LLM with {len(conversation_history)} messages")
        
        response = await self.llm.create_message(
            messages=conversation_history,
            tools=tools,
            system=system
        )
        
        return response
    
    async def _call_llm(
        self,
        user_message: str,
        tools: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send a single message to LLM with available tools.
        
        DEPRECATED: Use _call_llm_with_history for agentic loop.
        Kept for backward compatibility.
        """
        messages = self._build_message_history(user_message, context)
        print(f"Calling {self.llm_provider_name} with {len(tools)} tools")
        response = await self.llm.create_message(messages, tools)
        return response
    
    def _build_message_history(
        self,
        user_message: str,
        context: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """
        Build message history for LLM (simple version).
        
        DEPRECATED: The agentic loop builds history dynamically.
        Kept for backward compatibility.
        """
        messages = [{"role": "user", "content": user_message}]
        
        if context.get("current_industry") or context.get("current_process"):
            context_info = []
            if context.get("current_industry"):
                context_info.append(f"Industry: {context['current_industry']}")
            if context.get("current_process"):
                context_info.append(f"Process: {context['current_process']}")
            
            system_msg = "Context: " + ", ".join(context_info)
            messages.insert(0, {"role": "system", "content": system_msg})
        
        return messages
    
    # =========================================================================
    # Context Management
    # =========================================================================
    
    async def _get_or_create_context(
        self,
        conversation_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Load existing context or create new one."""
        context = await self.context_manager.get_context(conversation_id)
        
        if not context:
            print(f"Creating new context for conversation {conversation_id}")
            await self.context_manager.create_context(conversation_id, user_id)
            context = await self.context_manager.get_context(conversation_id)
        
        return context
    
    async def _update_context(
        self,
        conversation_id: str,
        tool_name: str,
        result: Dict[str, Any],
        params: Dict[str, Any]
    ) -> None:
        """Update context after tool execution."""
        # Add tool execution to history
        await self.context_manager.add_tool_execution(
            conversation_id,
            tool_name,
            result
        )
        
        print(f"[_update_context] tool_name={tool_name}, result_keys={list(result.keys())}")
        
        # Update simulation params if this was a simulation
        if tool_name in ["simulate_process", "simulate_equipment"]:
            # Extract run_id - could be 'calc_run_id' or 'run_id' depending on the tool
            run_id = result.get("calc_run_id") or result.get("run_id")
            
            print(f"[_update_context] Simulation detected! run_id={run_id}, status={result.get('status')}")
            
            # Extract condensed summary for quick follow-up answers
            simulation_summary = self._extract_simulation_summary(tool_name, result, params)
            
            print(f"[_update_context] Extracted summary: run_id={simulation_summary.get('run_id') if simulation_summary else None}")
            
            await self.context_manager.update_context(
                conversation_id,
                {
                    "simulation_params": params,
                    "last_run_id": run_id,
                    "last_simulation_summary": simulation_summary
                }
            )
            
            print(f"[_update_context] Context update called for conversation {conversation_id}")
        
        # Update validation params if this was a validation
        if tool_name.startswith("validate_"):
            await self.context_manager.update_context(
                conversation_id,
                {"validation_params": params}
            )
        
        print(f"Context updated after {tool_name} execution")
    
    # =========================================================================
    # Simulation Summary Extraction
    # =========================================================================
    
    def _extract_simulation_summary(
        self,
        tool_name: str,
        result: Dict[str, Any],
        params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Extract condensed summary from simulation results.
        
        Works for both process simulations and standalone equipment.
        The summary is stored in context for quick follow-up answers.
        
        Handles two different result structures:
        - Process simulation: {summary, units, boiling_house, balances, ...}
        - Equipment simulation: {kpis, computed, outputs, ...}
        
        Args:
            tool_name: 'simulate_process' or 'simulate_equipment'
            result: Full simulation result from MCP server
            params: Parameters used for the simulation
            
        Returns:
            Condensed summary dict or None if extraction fails
        """
        if result.get("status") != "success":
            return None
        
        try:
            is_equipment = tool_name == "simulate_equipment"
            
            summary = {
                "run_id": result.get("calc_run_id") or result.get("run_id"),
                "simulation_type": "equipment" if is_equipment else "process",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
            
            if is_equipment:
                # Equipment-specific fields
                summary["equipment_type"] = params.get("equipment_type") or result.get("unit")
                summary["industry"] = result.get("industry", "sugar")
                
                # Extract KPIs from equipment result
                if "kpis" in result:
                    summary["kpis"] = self._round_numeric_values(result["kpis"])
                
                # Extract computed values
                if "computed" in result:
                    summary["computed"] = self._round_numeric_values(result["computed"])
                
                # Extract output streams from "outputs" key
                summary["output_streams"] = self._extract_output_streams(result)
                
                # Single equipment summary
                equipment_type = summary.get("equipment_type", "unknown")
                summary["equipment_summary"] = {
                    equipment_type: self._condense_single_equipment(result)
                }
            else:
                # Process-specific fields
                summary["process_id"] = params.get("process_id")
                summary["industry"] = params.get("industry", "sugar")
                
                # Extract KPIs from "summary" key (process simulations use this)
                if "summary" in result:
                    summary["kpis"] = self._round_numeric_values(result["summary"])
                elif "kpis" in result:
                    summary["kpis"] = self._round_numeric_values(result["kpis"])
                
                # Extract balances for context
                if "balances" in result:
                    summary["balances"] = self._extract_key_balances(result["balances"])
                
                # Extract equipment data from "units" key (NOT "outputs")
                if "units" in result:
                    summary["equipment_summary"] = self._condense_units_data(result["units"])
                elif "outputs" in result:
                    summary["equipment_summary"] = self._condense_equipment_outputs(result["outputs"])
                
                # Extract boiling house summary
                if "boiling_house" in result:
                    bh = result["boiling_house"]
                    if "summary" in bh:
                        summary["boiling_house"] = self._round_numeric_values(bh["summary"])
            
            # Keep warnings for context (limit to 5)
            if result.get("warnings"):
                summary["warnings"] = result["warnings"][:5]
            
            # Store the input parameters used for reference
            summary["inputs_used"] = self._extract_key_inputs(params)
            
            print(f"Extracted simulation summary for {tool_name}: kpis={len(summary.get('kpis', {}))}, equipment={len(summary.get('equipment_summary', {}))}")
            return summary
            
        except Exception as e:
            print(f"WARNING: " + f"Failed to extract simulation summary: {e}", exc_info=True)
            return None
    
    def _extract_key_balances(self, balances: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key balance metrics."""
        key_metrics = {}
        
        if "mass_balance" in balances:
            mb = balances["mass_balance"]
            if isinstance(mb, dict):
                key_metrics["mass_balance_error_pct"] = mb.get("error_pct")
        
        if "sucrose_balance" in balances:
            sb = balances["sucrose_balance"]
            if isinstance(sb, dict):
                key_metrics["sucrose_balance_error_pct"] = sb.get("error_pct")
        
        return key_metrics
    
    def _condense_units_data(self, units: Dict[str, Any]) -> Dict[str, Any]:
        """
        Condense per-unit data from process simulation.
        
        The 'units' dict has structure like:
        {
            "heater": {"juice_in_kg_hr": ..., "outlet_temp_C": ..., ...},
            "evaporator": {"steam_consumed_kg_hr": ..., ...},
            ...
        }
        """
        condensed = {}
        
        for unit_name, unit_data in units.items():
            if isinstance(unit_data, dict):
                # Keep all numeric values from each unit
                unit_condensed = {}
                for key, value in unit_data.items():
                    if isinstance(value, (int, float)):
                        if isinstance(value, float):
                            unit_condensed[key] = round(value, 2)
                        else:
                            unit_condensed[key] = value
                    elif isinstance(value, str):
                        unit_condensed[key] = value
                
                if unit_condensed:
                    condensed[unit_name] = unit_condensed
        
        return condensed
    
    def _extract_key_inputs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key input parameters for reference."""
        key_inputs = {}
        
        # Common input parameters to capture
        input_keys = [
            "process_id", "equipment_type", "cane_flow_kg_hr", "cane_flow_tcd",
            "cane_brix", "cane_fiber_pct", "cane_purity", "operating_mode",
            "inlet_stream", "operating_params"
        ]
        
        for key in input_keys:
            if key in params:
                value = params[key]
                if isinstance(value, dict):
                    # Flatten nested dicts for inlet_stream, operating_params
                    key_inputs[key] = self._round_numeric_values(value)
                else:
                    key_inputs[key] = value
        
        return key_inputs
    
    def _extract_output_streams(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract key output stream data from simulation result.
        
        Looks for common stream names and extracts key properties.
        """
        streams = {}
        outputs = result.get("outputs", {})
        
        # Common output stream names across different simulations
        stream_names = [
            "syrup", "condensate", "sugar", "molasses", "bagasse",
            "clarified_juice", "mixed_juice", "massecuite", "filter_cake",
            "raw_juice", "clear_juice", "mother_liquor", "crystal"
        ]
        
        # Key properties to extract from streams
        key_properties = [
            "flow_kg_hr", "flow", "temperature", "brix", "purity",
            "pol", "composition", "pressure", "phase", "moisture"
        ]
        
        for stream_name in stream_names:
            if stream_name in outputs:
                stream_data = outputs[stream_name]
                if isinstance(stream_data, dict):
                    # Extract only key properties
                    condensed = {}
                    for prop in key_properties:
                        if prop in stream_data:
                            value = stream_data[prop]
                            if isinstance(value, float):
                                condensed[prop] = round(value, 2)
                            else:
                                condensed[prop] = value
                    if condensed:
                        streams[stream_name] = condensed
        
        return streams
    
    def _condense_equipment_outputs(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Condense equipment outputs from a full process simulation.
        
        Extracts key metrics for each equipment unit.
        """
        condensed = {}
        
        # Key metrics to extract from equipment outputs
        key_metrics = [
            "efficiency", "extraction", "recovery", "yield",
            "outlet_brix", "outlet_temp", "outlet_flow",
            "steam_economy", "heat_duty", "power",
            "crystal_yield", "purity", "pressure_drop",
            "inlet_brix", "inlet_temp", "concentration_ratio"
        ]
        
        for equip_name, equip_data in outputs.items():
            if isinstance(equip_data, dict):
                equip_condensed = {}
                for metric in key_metrics:
                    if metric in equip_data:
                        value = equip_data[metric]
                        if isinstance(value, float):
                            equip_condensed[metric] = round(value, 3)
                        else:
                            equip_condensed[metric] = value
                if equip_condensed:
                    condensed[equip_name] = equip_condensed
        
        return condensed
    
    def _condense_single_equipment(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Condense single equipment simulation result to key metrics.
        """
        condensed = {}
        
        # Add KPIs
        if "kpis" in result:
            condensed.update(self._round_numeric_values(result["kpis"]))
        
        # Add computed values
        if "computed" in result:
            condensed.update(self._round_numeric_values(result["computed"]))
        
        return condensed
    
    def _round_numeric_values(self, data: Dict[str, Any], decimals: int = 3) -> Dict[str, Any]:
        """
        Round all numeric values in a dictionary.
        """
        rounded = {}
        for key, value in data.items():
            if isinstance(value, float):
                rounded[key] = round(value, decimals)
            elif isinstance(value, dict):
                rounded[key] = self._round_numeric_values(value, decimals)
            else:
                rounded[key] = value
        return rounded
    
    # =========================================================================
    # Tool Management
    # =========================================================================
    
    async def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get all available tools from MCP server."""
        tools = await self.mcp_client.list_tools()
        print(f"Retrieved {len(tools)} tools from MCP server")
        return tools
    
    async def _execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute tool via MCP client."""
        print(f"Executing tool: {tool_name}")
        
        try:
            result = await self.mcp_client.call_tool(tool_name, params)
            
            # MCP client returns text content directly, wrap it in dict format
            if isinstance(result, str):
                # Try to parse as JSON
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        return parsed
                    return {
                        "status": "success",
                        "message": result,
                        "data": parsed
                    }
                except json.JSONDecodeError:
                    return {
                        "status": "success",
                        "message": result,
                        "data": result
                    }
            elif isinstance(result, dict):
                return result
            else:
                return {
                    "status": "success",
                    "message": str(result),
                    "data": result
                }
        except Exception as e:
            print(f"ERROR: " + f"Tool execution error: {e}")
            return {
                "status": "error",
                "error": str(e),
                "message": f"Tool {tool_name} failed: {str(e)}"
            }
    
    # =========================================================================
    # Policy Enforcement
    # =========================================================================
    
    async def _enforce_policy(
        self,
        tool_name: str,
        context: Dict[str, Any],
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Check if tool execution is allowed by policy.
        
        Returns:
            {
                "decision": "allow" | "deny",
                "reason": "...",
                "metadata": {...}
            }
        """
        # TEMPORARY: Skip policy enforcement for testing
        # TODO: Re-enable after agentic loop is verified working
        return {
            "decision": "allow",
            "reason": "Policy enforcement temporarily disabled",
            "metadata": {}
        }
        
        # Original policy enforcement (commented out)
        # result = self.policy_engine.enforce_policy(tool_name, context, params)
        # return {
        #     "decision": result.decision.value,
        #     "reason": result.reason,
        #     "metadata": result.metadata
        # }
    
    # =========================================================================
    # Formatting Helpers
    # =========================================================================
    
    def _format_tool_results_for_llm(self, tool_results: List[Dict[str, Any]]) -> str:
        """Format successful tool results for LLM feedback."""
        formatted = []
        for result in tool_results:
            tool_name = result["tool"]
            tool_result = result["result"]
            formatted.append(f"✓ {tool_name}: {json.dumps(tool_result, indent=2)}")
        
        return "\n".join(formatted)
    
    def _format_policy_violations_for_llm(self, violations: List[Dict[str, Any]]) -> str:
        """Format policy violations for LLM feedback."""
        formatted = []
        for violation in violations:
            tool_name = violation["tool"]
            reason = violation["reason"]
            formatted.append(f"✗ {tool_name}: {reason}")
        
        return "\n".join(formatted)
    
    def _format_results_for_user(self, tool_results: List[Dict[str, Any]]) -> str:
        """Format tool execution results for end user."""
        if not tool_results:
            return "No tools were executed."
        
        tool_names = [r["tool"] for r in tool_results]
        
        if len(tool_names) == 1:
            tool_name = tool_names[0]
            result = tool_results[0].get("result", {})
            status = result.get("status", "unknown")
            
            if status == "success":
                return f"Successfully executed {tool_name}. Results available in context."
            else:
                error_msg = result.get("error") or result.get("message", "Unknown error")
                return f"Tool {tool_name} failed: {error_msg}"
        else:
            summary = f"Executed {len(tool_names)} tools: {', '.join(tool_names)}"
            return summary
    
    def _format_tool_results(self, tool_results: List[Dict[str, Any]]) -> str:
        """Format tool results for LLM."""
        formatted = []
        for result in tool_results:
            tool_name = result["tool"]
            tool_result = result["result"]
            formatted.append(f"- {tool_name}: {json.dumps(tool_result, indent=2)}")
        
        return "\n".join(formatted)
    
    def _format_policy_violation_message(
        self,
        violations: List[Dict[str, Any]]
    ) -> str:
        """Format policy violation message for user."""
        if not violations:
            return "Unable to process request."
        
        messages = ["I cannot execute the requested tools due to policy restrictions:"]
        for violation in violations:
            messages.append(f"- {violation['tool']}: {violation['reason']}")
        
        return "\n".join(messages)
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def get_policy_summary(self, conversation_id: str) -> Dict[str, Any]:
        """
        Get policy summary for current conversation.
        
        Useful for debugging and showing user their current state.
        """
        context = await self.context_manager.get_context(conversation_id)
        
        if not context:
            return {
                "status": "error",
                "message": "Conversation not found"
            }
        
        summary = self.policy_engine.get_policy_summary(context)
        
        return {
            "status": "success",
            "conversation_id": conversation_id,
            "summary": summary
        }
    
    async def reset_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """
        Reset conversation context.
        
        Useful when user wants to start fresh or if tool budget is exceeded.
        """
        await self.context_manager.clear_context(conversation_id)
        
        print(f"Conversation {conversation_id} reset")
        
        return {
            "status": "success",
            "message": "Conversation reset successfully",
            "conversation_id": conversation_id
        }
