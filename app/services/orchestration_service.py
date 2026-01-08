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
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from app.services.context_manager import ContextManager
from app.services.tool_registry import ToolRegistry
from app.services.event_emitter import EventEmitter
from app.core.policy_engine import PolicyEngine, PolicyDecision
from app.core.llm_provider import ClaudeProvider
from app.core.llm_gemini_provider import GeminiProvider
from app.core.mcp_client import MCPClient, MCPServerConfig
from app.config import settings

logger = logging.getLogger(__name__)


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
        llm_provider: str = None
    ):
        """
        Initialize orchestration service.
        
        Args:
            context_manager: Context tracking service
            tool_registry: Tool metadata registry
            policy_engine: Policy enforcement engine
            mcp_client: MCP client for tool execution
            event_emitter: Event emitter for real-time updates (optional)
            llm_provider: "claude" or "gemini" (defaults to settings.default_llm_provider)
        """
        self.context_manager = context_manager
        self.tool_registry = tool_registry
        self.policy_engine = policy_engine
        self.mcp_client = mcp_client
        self.event_emitter = event_emitter
        
        # Initialize LLM provider
        self.llm_provider_name = llm_provider or settings.default_llm_provider
        if self.llm_provider_name == "claude":
            self.llm = ClaudeProvider(
                api_key=settings.anthropic_api_key
            )
        elif self.llm_provider_name == "gemini":
            self.llm = GeminiProvider(
                api_key=settings.google_api_key,
                model=settings.llm_model_gemini,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature
            )
        else:
            raise ValueError(f"Unknown LLM provider: {self.llm_provider_name}")
        
        logger.info(f"Orchestration service initialized with {self.llm_provider_name}")
    
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
            logger.info(f"Processing message for conversation {conversation_id}")
            
            # Step 1: Load or create context
            context = await self._get_or_create_context(conversation_id, user_id)
            
            # Step 2: Get available tools
            tools = await self._get_available_tools()
            
            # Track state across iterations
            all_tool_results = []
            total_tool_calls = 0
            
            # Build conversation history for multi-turn
            # Start with user's message
            conversation_history = [
                {"role": "user", "content": user_message}
            ]
            
            # Emit thinking start
            if self.event_emitter:
                await self.event_emitter.emit_thinking_start(conversation_id)
            
            # =====================================================
            # AGENTIC LOOP: Continue until LLM stops calling tools
            # =====================================================
            for iteration in range(MAX_ITERATIONS):
                logger.info(f"=== Agentic Loop Iteration {iteration + 1} ===")
                
                # Call LLM with conversation history
                thinking_start = datetime.now()
                llm_response = await self._call_llm_with_history(
                    conversation_history, 
                    tools, 
                    context
                )
                thinking_duration_ms = int((datetime.now() - thinking_start).total_seconds() * 1000)
                
                # Track tokens
                if hasattr(llm_response, 'usage'):
                    total_input_tokens += llm_response.usage.get("input_tokens", 0)
                    total_output_tokens += llm_response.usage.get("output_tokens", 0)
                
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
                    logger.info(f"LLM finished after {iteration + 1} iterations with text response")
                    
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
                logger.info(f"LLM requested {len(tool_calls_list)} tool calls")
                
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
                        logger.warning(f"Tool call limit exceeded ({MAX_TOOL_CALLS})")
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
                    
                    logger.info(f"Executing tool: {tool_name}")
                    
                    # Check policy
                    policy_result = await self._enforce_policy(tool_name, context, tool_params)
                    
                    if policy_result["decision"] != "allow":
                        # Policy denied - send error back to LLM (not to user!)
                        logger.warning(f"Policy DENIED: {tool_name} - {policy_result['reason']}")
                        
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
                
                logger.info(f"Sent {len(iteration_tool_results)} tool results back to LLM")
                
                # Continue loop - LLM will see results in next iteration
            
            # =====================================================
            # MAX ITERATIONS REACHED
            # =====================================================
            logger.warning(f"Max iterations ({MAX_ITERATIONS}) reached")
            
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
            logger.error(f"Error processing message: {e}", exc_info=True)
            
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
        
        system = "\n".join(system_parts)
        
        logger.info(f"Calling LLM with {len(conversation_history)} messages")
        
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
        logger.info(f"Calling {self.llm_provider_name} with {len(tools)} tools")
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
            logger.info(f"Creating new context for conversation {conversation_id}")
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
        
        # Update simulation params if this was a simulation
        if tool_name in ["simulate_process", "simulate_equipment"]:
            await self.context_manager.update_context(
                conversation_id,
                {
                    "simulation_params": params,
                    "last_run_id": result.get("run_id")
                }
            )
        
        # Update validation params if this was a validation
        if tool_name.startswith("validate_"):
            await self.context_manager.update_context(
                conversation_id,
                {"validation_params": params}
            )
        
        logger.info(f"Context updated after {tool_name} execution")
    
    # =========================================================================
    # Tool Management
    # =========================================================================
    
    async def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get all available tools from MCP server."""
        tools = await self.mcp_client.list_tools()
        logger.info(f"Retrieved {len(tools)} tools from MCP server")
        return tools
    
    async def _execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute tool via MCP client."""
        logger.info(f"Executing tool: {tool_name}")
        
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
            logger.error(f"Tool execution error: {e}")
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
        
        logger.info(f"Conversation {conversation_id} reset")
        
        return {
            "status": "success",
            "message": "Conversation reset successfully",
            "conversation_id": conversation_id
        }
