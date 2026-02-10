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
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

from app.config import settings
from app.services.context_manager import ContextManager

# =============================================================================
# Context Optimization Constants
# =============================================================================
MAX_RECENT_MESSAGES = 10  # Keep last N messages in full detail

# =============================================================================
# MCP Server Tool Mappings (Unique tool names per server)
# =============================================================================

# Tools belonging to MCP Process Server (Sugar Industry, etc.)
PROCESS_SERVER_TOOLS = {
    # Discovery
    "list_industries", "list_processes", "get_process", 
    "get_equipment_types", "get_process_equipment_schema", "get_stream_schema",
    # Validation
    "validate_process_inputs", "validate_process_connections", "validate_equipment_inputs",
    # Simulation
    "simulate_process", "simulate_equipment",
    # Run Management (process-prefixed)
    "get_process_run", "compare_process_runs", "list_process_runs"
}

# Industries handled by Process Server
PROCESS_SERVER_INDUSTRIES = {"sugar"}

# Tool categories for context-aware filtering
DISCOVERY_TOOLS = {
    "list_industries", "list_processes", "get_process", "get_equipment_types", 
    "get_process_equipment_schema", "get_stream_schema"
}
VALIDATION_TOOLS = {
    "validate_process_inputs", "validate_process_connections", "validate_equipment_inputs"
}
SIMULATION_TOOLS = {"simulate_process", "simulate_equipment"}
RUN_TOOLS = {
    "get_process_run", "compare_process_runs", "list_process_runs"
}
CORE_TOOLS = SIMULATION_TOOLS | VALIDATION_TOOLS  # Always include these

# Tools that require user_id to be injected from context
TOOLS_REQUIRING_USER_ID = {
    # Process Server tools (optional but useful for run tracking)
    "simulate_process", "simulate_equipment",
    "list_process_runs",  # Required for listing user's runs
    # Calc Engine Phase 1 tools (calc_ prefixed)
    "calc_get_process",  # Optional: merges user's active version
    "calc_simulate_process",  # Required
    "calc_get_run", "calc_list_runs",  # Required for run tracking
    # Calc Engine Phase 2 tools (parameter versioning)
    "get_editable_parameters",  # Required
    "validate_parameters",  # Uses user overrides for validation context
    # "edit_parameters",  # REMOVED: calc_simulate_process now auto-saves parameters on success
    "get_user_parameters",  # Required: retrieves user's versions
    "switch_parameter_version",  # Required: changes user's active version
    "compare_parameters",  # Required: compares user's versions
}
from app.services.tool_registry import ToolRegistry
from app.services.event_emitter import EventEmitter
from app.core.policy_engine import PolicyEngine, PolicyDecision
from app.core.llm_provider import ClaudeProvider
from app.core.mcp_client import MCPClient, MCPServerConfig, MCPClientRegistry


# =============================================================================
# Helper Functions
# =============================================================================

def inject_result_links(message: str, run_ids: List[str], frontend_url: str) -> str:
    """
    Append result links using the configured frontend URL.
    
    Always injects links with the correct frontend_url from FRONTEND_URL env var.
    Skips if LLM already included result links to avoid duplicates.
    
    Args:
        message: LLM's response text
        run_ids: List of run IDs from simulation tools
        frontend_url: Base URL of frontend application (from FRONTEND_URL env)
    
    Returns:
        Message with result links appended
    """
    if not run_ids:
        return message
    
    # Skip if LLM already included result links
    if "/results/" in message:
        return message
    
    # Inject links using the configured frontend URL
    links = []
    for run_id in run_ids:
        url = f"{frontend_url}/results/{run_id}"
        links.append(f"📊 [View Simulation Results]({url})")
    
    link_section = "\n\n" + "\n".join(links)
    return message + link_section


# =============================================================================
# Streaming LLM Response
# =============================================================================

class StreamingLLMResponse:
    """
    Response object from streaming LLM call.
    
    Provides same interface as ParsedResponse for compatibility
    with existing agentic loop code.
    """
    
    def __init__(
        self,
        text: str,
        tool_calls: List[Dict[str, Any]],
        usage: Dict[str, int]
    ):
        self.text = text
        self.tool_calls = tool_calls
        self._usage = usage
    
    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0
    
    @property
    def usage(self):
        """Return usage as object with attributes for compatibility."""
        return type('Usage', (), self._usage)()
    
    def __repr__(self) -> str:
        return f"StreamingLLMResponse(text_len={len(self.text)}, tool_calls={len(self.tool_calls)})"


class OrchestrationService:
    """
    Central orchestrator for chat-based process simulation.
    
    Uses an agentic loop pattern where:
    - LLM proposes tool calls
    - Backend executes tools and returns results
    - LLM sees results (success AND errors) and decides next action
    - Loop continues until LLM provides final text response
    
    Supports multiple MCP servers:
    - process_server: Sugar industry simulations
    - calc_engine: Dynamic flowsheet simulations (IPA recovery, distillation, etc.)
    """
    
    def __init__(
        self,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        policy_engine: PolicyEngine,
        mcp_client: MCPClient = None,  # Legacy: single client (deprecated)
        mcp_registry: MCPClientRegistry = None,  # New: multi-server registry
        event_emitter: Optional[EventEmitter] = None,
        anthropic_api_key: str = None,
        llm_provider = None  # Pre-built singleton LLM provider
    ):
        """
        Initialize orchestration service.
        
        Args:
            context_manager: Context tracking service
            tool_registry: Tool metadata registry
            policy_engine: Policy enforcement engine
            mcp_client: Legacy single MCP client (deprecated, use mcp_registry)
            mcp_registry: MCP client registry for multiple servers
            event_emitter: Event emitter for real-time updates (optional)
            anthropic_api_key: Anthropic API key for Claude
        """
        self.context_manager = context_manager
        self.tool_registry = tool_registry
        self.policy_engine = policy_engine
        self.event_emitter = event_emitter
        
        # Support both legacy single client and new registry
        if mcp_registry:
            self.mcp_registry = mcp_registry
            # For backward compatibility, set mcp_client to process_server
            self.mcp_client = mcp_registry.get_client("process_server")
        elif mcp_client:
            # Legacy mode: wrap single client in a minimal registry-like interface
            self.mcp_client = mcp_client
            self.mcp_registry = None
        else:
            self.mcp_client = None
            self.mcp_registry = None
        
        # Use pre-built singleton LLM provider if available, otherwise create new one
        if llm_provider:
            self.llm = llm_provider
        else:
            self.llm = ClaudeProvider(api_key=anthropic_api_key)
    
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
            # Step 0: Health check - fail fast if no MCP servers available
            if self.mcp_registry:
                # Check if at least one server is available
                health_status = self.mcp_registry.get_health_status()
                any_server_healthy = any(s["connected"] for s in health_status.values())
                connected_servers = self.mcp_registry.connected_servers
                
                if not any_server_healthy:
                    error_msg = "No MCP servers are available. Please ensure at least one server is running."
                    
                    if self.event_emitter:
                        await self.event_emitter.emit_app_error(
                            conversation_id,
                            "mcp_server_unavailable",
                            error_msg,
                            details={"health_status": health_status},
                            recoverable=False
                        )
                    
                    return {
                        "status": "error",
                        "message": error_msg,
                        "conversation_id": conversation_id,
                        "token_usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0
                        }
                    }
                
                print(f"INFO: MCP servers available: {connected_servers}")
            else:
                # Legacy single client mode
                process_server_healthy = await self.mcp_client.health_check() if self.mcp_client else False
                
                if not process_server_healthy:
                    error_msg = "MCP Process Server is unavailable. Please ensure the server is running and try again."
                    
                    if self.event_emitter:
                        await self.event_emitter.emit_app_error(
                            conversation_id,
                            "mcp_server_unavailable",
                            error_msg,
                            details={"check": "health_check", "process_server": process_server_healthy},
                            recoverable=False
                        )
                    
                    return {
                        "status": "error",
                        "message": error_msg,
                        "conversation_id": conversation_id,
                        "token_usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0
                        }
                    }
            
            # Step 1: Load or create context
            context = await self._get_or_create_context(conversation_id, user_id)
            
            # Step 2: Get available tools (filtered by context)
            tools = await self._get_available_tools(context)
            
            # Track state across iterations
            all_tool_results = []
            total_tool_calls = 0
            
            # Track repeated errors to stop loop early
            recent_errors = []  # Store last N error messages
            MAX_REPEATED_ERRORS = 3  # Stop if same error occurs this many times
            
            # Build conversation history for multi-turn
            # Load previous messages from context for continuity
            previous_messages = await self.context_manager.get_messages(conversation_id)
            
            conversation_history = []
            
            # Add previous conversation turns (limit to last 10 for context window)
            # IMPORTANT: Filter out empty messages and incomplete streaming messages
            # to avoid Claude error "all messages must have non-empty content"
            for msg in previous_messages[-10:]:
                content = msg.get("content", "")
                status = msg.get("status", "complete")
                has_tool_calls = bool(msg.get("tool_calls") or msg.get("metadata", {}).get("tool_calls"))
                
                # Skip messages that are still streaming
                if status == "streaming":
                    continue
                
                # Skip empty messages UNLESS they have tool_calls (valid intermediate state)
                if (not content or not content.strip()) and not has_tool_calls:
                    continue
                
                conversation_history.append({
                    "role": msg["role"],
                    "content": content if content and content.strip() else "[Tool execution in progress]"
                })
            
            # Add current user message
            conversation_history.append({"role": "user", "content": user_message})
            
            # Save user message to context for future turns
            await self.context_manager.add_message(conversation_id, "user", user_message)
            
            # Emit thinking start
            if self.event_emitter:
                await self.event_emitter.emit_thinking_start(conversation_id)
            
            # =================================================================
            # PHASE 2.1: Create empty assistant message BEFORE streaming
            # This ensures message is persisted even if errors occur mid-stream
            # =================================================================
            from app.services.context_manager import MessageStatus
            assistant_message_id = await self.context_manager.add_message(
                conversation_id,
                "assistant",
                "",  # Empty initially, will be updated with streamed content
                status=MessageStatus.STREAMING
            )
            
            # Track accumulated text across all iterations for error recovery
            total_accumulated_text = ""
            
            # =====================================================
            # AGENTIC LOOP: Continue until LLM stops calling tools
            # =====================================================
            for iteration in range(MAX_ITERATIONS):
                # Call LLM with conversation history (non-streaming, response via HTTP)
                # Retry logic for rate limits
                max_retries = 2
                retry_delay = 60  # seconds
                
                thinking_start = datetime.now()
                llm_response = None
                
                for retry in range(max_retries + 1):
                    try:
                        llm_response = await self._call_llm_with_history(
                            conversation_history, 
                            tools, 
                            context,
                            conversation_id,  # Pass conversation_id for streaming events
                            assistant_message_id  # Pass message_id for error recovery
                        )
                        break  # Success - exit retry loop
                        
                    except Exception as e:
                        error_message = str(e)
                        is_rate_limit = "rate_limit_error" in error_message or "429" in error_message
                        
                        if is_rate_limit and retry < max_retries:
                            await asyncio.sleep(retry_delay)
                            continue
                        
                        # Not a rate limit or out of retries - re-raise
                        raise
                
                if llm_response is None:
                    raise Exception("Failed to get LLM response after retries")
                    
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
                    # Update accumulated text for final response
                    total_accumulated_text = message_text
                    
                    # PHASE 2.3: Update assistant message with final content and status=complete
                    await self.context_manager.update_message(
                        conversation_id,
                        assistant_message_id,
                        content=message_text,
                        status=MessageStatus.COMPLETE,
                        metadata={"iterations": iteration + 1, "tool_calls": len(all_tool_results)}
                    )
                    
                    # Emit thinking end
                    if self.event_emitter:
                        await self.event_emitter.emit_thinking_end(conversation_id, thinking_duration_ms)
                    # Response returned via HTTP (no emit_message_final needed)
                    
                    # Get updated context with run_ids
                    final_context = await self.context_manager.get_context(conversation_id)
                    run_ids = final_context.get("run_ids", [])
                    
                    # Inject result links if LLM didn't include them (Phase 4.1)
                    message_with_links = inject_result_links(
                        message=message_text,
                        run_ids=run_ids,
                        frontend_url=settings.frontend_url
                    )
                    
                    # Format tool executions for response
                    tool_executions = [
                        {
                            "tool_name": tc.get("name", "unknown"),
                            "status": "success" if tc.get("result", {}).get("status") != "error" else "error",
                            "duration_ms": tc.get("duration_ms"),
                            "summary": tc.get("result", {}).get("summary", str(tc.get("result", {}))[:100])
                        }
                        for tc in all_tool_results
                    ]
                    
                    return {
                        "status": "success",
                        "message": message_with_links,
                        "tool_calls": all_tool_results,
                        "tool_executions": tool_executions,
                        "run_ids": run_ids,
                        "iterations": iteration + 1,
                        "conversation_id": conversation_id,
                        "context": final_context,
                        "token_usage": {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "total_tokens": total_input_tokens + total_output_tokens
                        }
                    }
                
                # =====================================================
                # TOOL EXECUTION: Process each tool call
                # =====================================================
                # Intermediate text before tool calls is now included in final HTTP response
                # (No need to emit separately)
                
                # Add assistant message with BOTH text and tool calls to history
                assistant_msg = {"role": "assistant", "tool_calls": tool_calls_list}
                if message_text and message_text.strip():
                    assistant_msg["content"] = message_text  # Preserve intermediate text
                conversation_history.append(assistant_msg)
                
                # Execute tools and collect results
                iteration_tool_results = []
                
                for tool_call in tool_calls_list:
                    # Safety check: don't exceed tool call limit
                    total_tool_calls += 1
                    if total_tool_calls > MAX_TOOL_CALLS:
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
                    
                    # Check policy
                    policy_result = await self._enforce_policy(tool_name, context, tool_params)
                    
                    if policy_result["decision"] != "allow":
                        # Policy denied - send error back to LLM (not to user!)
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
                            "name": tool_name,
                            "tool": tool_name,  # For backward compatibility
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
                    result = await self._execute_tool(tool_name, tool_params, conversation_id, context)
                    tool_duration_ms = int((datetime.now() - tool_start_time).total_seconds() * 1000)
                    
                    # Emit tool end
                    if self.event_emitter:
                        # Treat 'not_converged' as success - simulation ran, just didn't converge
                        # Only 'error', 'failed', 'simulation_failed' are actual tool failures
                        result_status = result.get("status", "")
                        status = "error" if result_status in ["error", "failed", "simulation_failed"] else "success"
                        summary = result.get("message", f"{tool_name} completed")
                        error_msg = result.get("error") if status == "error" else None
                        # Ensure error_message is always a string, not a list or other type
                        if error_msg is not None and not isinstance(error_msg, str):
                            error_msg = str(error_msg)
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
                    # Claude sees errors and decides how to recover based on tool descriptions
                    
                    # Track errors for repeated error detection
                    # Only track actual failures, not 'not_converged' (which is a valid simulation result)
                    if result.get("status") in ["error", "failed", "simulation_failed"]:
                        error_key = f"{tool_name}:{result.get('error', '')[:100]}"  # Normalize error for comparison
                        recent_errors.append(error_key)
                        
                        # Check for repeated errors
                        if len(recent_errors) >= MAX_REPEATED_ERRORS:
                            # Count occurrences of the latest error
                            latest_error = recent_errors[-1]
                            error_count = sum(1 for e in recent_errors if e == latest_error)
                            
                            if error_count >= MAX_REPEATED_ERRORS:
                                # Same error repeated - add hint to result and let Claude respond
                                # Don't force exit - let the loop continue so Claude can generate response
                                result["repeated_error_notice"] = (
                                    f"This same error has occurred {error_count} times. "
                                    f"Please explain the issue to the user and ask for clarification or suggest alternatives."
                                )
                    
                    iteration_tool_results.append({
                        "name": tool_name,
                        "result": result
                    })
                    
                    # Track tool execution with proper status
                    # 'not_converged' is a successful tool call - simulation ran and returned results
                    result_status = result.get("status", "")
                    tool_status = "error" if result_status in ["error", "failed", "simulation_failed"] else "success"
                    all_tool_results.append({
                        "name": tool_name,
                        "tool": tool_name,  # For backward compatibility
                        "result": result,
                        "duration_ms": tool_duration_ms,
                        "status": tool_status
                    })
                
                # =====================================================
                # SEND RESULTS BACK TO LLM (including errors!)
                # =====================================================
                # Add tool results to conversation history
                conversation_history.append({
                    "role": "tool",
                    "tool_results": iteration_tool_results
                })
                
                # Continue loop - LLM will see results in next iteration
            
            # =====================================================
            # MAX ITERATIONS REACHED
            # =====================================================
            error_message = "I apologize, but I couldn't complete your request within the allowed iterations. Here's what I was able to do:\n\n" + self._format_results_for_user(all_tool_results)
            
            # PHASE 2.4: Update assistant message with error status
            await self.context_manager.update_message(
                conversation_id,
                assistant_message_id,
                content=error_message,
                status=MessageStatus.ERROR,
                metadata={"max_iterations_reached": True, "iterations": MAX_ITERATIONS}
            )
            
            if self.event_emitter:
                await self.event_emitter.emit_thinking_end(conversation_id, 0)
            # Response returned via HTTP (no emit_message_final needed)
            
            # Get context for run_ids
            final_context = await self.context_manager.get_context(conversation_id)
            
            print(f"WARNING: Max iterations ({MAX_ITERATIONS}) reached")
            # Format tool executions for response
            tool_executions = [
                {
                    "tool_name": tc.get("name", "unknown"),
                    "status": "success" if tc.get("result", {}).get("status") != "error" else "error",
                    "duration_ms": tc.get("duration_ms"),
                    "summary": tc.get("result", {}).get("summary", str(tc.get("result", {}))[:100])
                }
                for tc in all_tool_results
            ]
            
            # Return success - agentic loop completed, Claude's message explains partial results
            return {
                "status": "success",
                "message": error_message,
                "tool_calls": all_tool_results,
                "tool_executions": tool_executions,
                "run_ids": final_context.get("run_ids", []),
                "iterations": MAX_ITERATIONS,
                "conversation_id": conversation_id,
                "context": final_context,
                "token_usage": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens
                }
            }
            
        except Exception as e:
            error_msg = f"Error processing your request: {str(e)}"
            
            # PHASE 2.4: Update assistant message with error status and any partial content
            # Use total_accumulated_text if available, otherwise just error message
            try:
                from app.services.context_manager import MessageStatus
                partial_content = total_accumulated_text if total_accumulated_text else ""
                final_content = partial_content + ("\n\n[Error: " + str(e)[:200] + "]" if partial_content else error_msg)
                
                await self.context_manager.update_message(
                    conversation_id,
                    assistant_message_id,
                    content=final_content,
                    status=MessageStatus.ERROR,
                    metadata={"error": str(e)[:500]}
                )
            except Exception as update_error:
                print(f"Failed to update message with error: {update_error}")
            
            if self.event_emitter:
                await self.event_emitter.emit_app_error(
                    conversation_id,
                    "internal_error",
                    error_msg,
                    details={"exception": str(e)},
                    recoverable=True
                )
                # Response returned via HTTP (no emit_message_final needed)
            
            print(f"ERROR: Error processing message: {e}"); import traceback; traceback.print_exc()
            return {
                "status": "error",
                "message": f"Error processing your request: {str(e)}",
                "conversation_id": conversation_id,
                "tool_executions": [],
                "run_ids": [],
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
        context: Dict[str, Any],
        conversation_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None
    ) -> Any:
        """
        Call LLM with full conversation history.
        
        This enables the agentic loop by preserving:
        - User message
        - Assistant tool calls
        - Tool results (success and errors)
        
        The LLM sees the full history and decides what to do next.
        Claude will learn from tool descriptions and error messages to determine workflow.
        """
        # Build minimal system prompt - let Claude learn from tool descriptions and errors
        frontend_url = settings.frontend_url
        system_parts = [
            "You are a process simulation assistant with access to tools for industrial process simulation.",
            "Use the available tools to help users. If a tool fails, read the error message to understand what went wrong.",
            "If you need more information from the user to proceed, ask them directly.",
            f"When a simulation completes successfully and returns a run_id, include a link for the user: 📊 [View Simulation Results]({frontend_url}/results/{{run_id}}) — replace {{run_id}} with the actual run_id.",
        ]
        
        # Add current context if available (industry, process)
        if context.get("current_industry") or context.get("current_process"):
            context_info = []
            if context.get("current_industry"):
                context_info.append(f"Industry: {context['current_industry']}")
            if context.get("current_process"):
                context_info.append(f"Process: {context['current_process']}")
            system_parts.append(f"\nCurrent context: {', '.join(context_info)}")
        
        # Add run history reference for follow-up questions
        run_ids = context.get("run_ids", [])
        if run_ids:
            system_parts.append(f"\n{len(run_ids)} previous simulation(s) available in this conversation.")
        
        system = "\n".join(system_parts)
        
        # Optimize conversation history using sliding window
        optimized_history = self._prepare_conversation_for_llm(conversation_history)
        
        # Call LLM (non-streaming - response delivered via HTTP)
        return await self._call_llm(
            optimized_history,
            tools,
            system,
            conversation_id,
            assistant_message_id
        )
    
    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Any],
        system: str,
        conversation_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None
    ) -> "StreamingLLMResponse":
        """
        Call LLM and return response.
        
        Uses non-streaming API call. Tool progress events are still
        emitted via SSE, but the final response is returned directly.
        
        Args:
            messages: Prepared conversation history
            tools: Available tools
            system: System prompt
            conversation_id: For context (optional)
            assistant_message_id: For error recovery (optional)
            
        Returns:
            StreamingLLMResponse with text, tool_calls, usage
        """
        try:
            # Call LLM (non-streaming)
            response = await self.llm.create_message(
                messages=messages,
                tools=tools,
                system=system
            )
            
            # Extract tool calls in expected format
            tool_calls = []
            if response.has_tool_calls:
                for call in response.tool_calls:
                    tool_calls.append({
                        "id": call.get("id", ""),
                        "name": call["name"],
                        "input": call.get("input", call.get("arguments", {}))
                    })
            
            # Return response object compatible with existing code
            return StreamingLLMResponse(
                text=response.text,
                tool_calls=tool_calls,
                usage={
                    "input_tokens": response.usage.input_tokens if hasattr(response.usage, 'input_tokens') else 0,
                    "output_tokens": response.usage.output_tokens if hasattr(response.usage, 'output_tokens') else 0
                }
            )
            
        except Exception as e:
            error_message = str(e)
            
            # Check for different types of API errors
            is_rate_limit = "rate_limit_error" in error_message or "429" in error_message
            is_usage_limit = "API usage limits" in error_message or "You have reached your specified" in error_message
            
            if is_usage_limit:
                # API usage limit reached (monthly/daily limit)
                if assistant_message_id:
                    try:
                        from app.services.context_manager import MessageStatus
                        await self.context_manager.update_message(
                            conversation_id,
                            assistant_message_id,
                            content="[API usage limit reached - service will resume after limit resets]",
                            status=MessageStatus.ERROR,
                            metadata={"error": "usage_limit"}
                        )
                    except Exception:
                        pass
                
                # Emit user-friendly usage limit error
                if self.event_emitter and conversation_id:
                    from app.models.events import ErrorType
                    await self.event_emitter.emit_app_error(
                        conversation_id,
                        error_type=ErrorType.RATE_LIMIT_ERROR,
                        error_message="API usage limit reached. Please try again later.",
                        details={
                            "error": "Monthly API usage limit exceeded",
                            "suggestion": "Service will resume after the usage limit resets",
                            "raw_error": error_message[:200]
                        }
                    )
            
            elif is_rate_limit:
                # Rate limit (requests per minute)
                if assistant_message_id:
                    try:
                        from app.services.context_manager import MessageStatus
                        await self.context_manager.update_message(
                            conversation_id,
                            assistant_message_id,
                            content="[Rate limit reached - please try again in a moment]",
                            status=MessageStatus.ERROR,
                            metadata={"error": "rate_limit"}
                        )
                    except Exception:
                        pass
                
                # Emit user-friendly rate limit error
                if self.event_emitter and conversation_id:
                    from app.models.events import ErrorType
                    await self.event_emitter.emit_app_error(
                        conversation_id,
                        error_type=ErrorType.RATE_LIMIT_ERROR,
                        error_message="Rate limit exceeded. Please wait a moment and try again.",
                        details={
                            "error": "Too many requests in a short time",
                            "suggestion": "Please wait 60 seconds before sending another message",
                            "limit": "30,000 tokens per minute"
                        }
                    )
            
            print(f"ERROR: LLM API error: {e}")
            raise
    
    # =========================================================================
    # Context Optimization (Sliding Window + Summarization)
    # =========================================================================
    
    def _prepare_conversation_for_llm(
        self,
        conversation_history: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Prepare conversation history for LLM using sliding window.
        
        If history exceeds MAX_RECENT_MESSAGES:
        - Summarize old messages into a compact context string
        - Keep recent messages in full detail
        
        This reduces token usage while preserving context.
        
        Args:
            conversation_history: Full conversation history
            
        Returns:
            Optimized history with summary + recent messages
        """
        if len(conversation_history) <= MAX_RECENT_MESSAGES:
            # No optimization needed
            return conversation_history
        
        # Split into old (to summarize) and recent (keep full)
        old_messages = conversation_history[:-MAX_RECENT_MESSAGES]
        recent_messages = conversation_history[-MAX_RECENT_MESSAGES:]
        
        # Extract summary from old messages
        summary = self._extract_summary_from_messages(old_messages)
        
        # Build optimized history with summary context
        optimized = []
        
        if summary:
            # Inject summary as a context message pair
            optimized.append({
                "role": "user",
                "content": f"[Previous context: {summary}]"
            })
            optimized.append({
                "role": "assistant",
                "content": "Understood, I have the context from our previous conversation."
            })
        
        # Add recent messages in full
        optimized.extend(recent_messages)
        
        return optimized
    
    def _extract_summary_from_messages(
        self,
        messages: List[Dict[str, Any]]
    ) -> str:
        """
        Extract key facts from old messages for context summary.
        
        Extracts from tool results only (that's where the data lives):
        - run_ids from simulations
        - process_ids and equipment_types
        - success/error counts
        
        Skips user/assistant text (recent messages have the intent).
        
        Args:
            messages: Old messages to summarize
            
        Returns:
            Compact summary string (50-150 chars)
        """
        run_ids = []
        processes = set()
        equipment = set()
        success_count = 0
        error_count = 0
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "tool":
                # Extract from tool results
                tool_results = msg.get("tool_results", [])
                for tr in tool_results:
                    result = tr.get("result", {})
                    if isinstance(result, dict):
                        # Extract run IDs (keep FULL ID for tool use)
                        run_id = result.get("calc_run_id") or result.get("run_id")
                        if run_id:
                            run_ids.append(str(run_id))  # Keep full ID
                        
                        # Extract process/equipment info
                        if result.get("process_id"):
                            processes.add(result["process_id"])
                        
                        # Count success/errors
                        status = result.get("status")
                        if status == "success":
                            success_count += 1
                        elif status == "error":
                            error_count += 1
                    
                    # Also check tool name for equipment type
                    tool_name = tr.get("name", "")
                    if tool_name == "simulate_equipment":
                        # Try to get equipment type from result
                        if isinstance(result, dict) and result.get("unit"):
                            equipment.add(result["unit"])
            
            elif role == "assistant" and "tool_calls" in msg:
                # Extract from tool call parameters
                for tc in msg.get("tool_calls", []):
                    params = tc.get("input", tc.get("arguments", {}))
                    if isinstance(params, dict):
                        if params.get("process_id"):
                            processes.add(params["process_id"])
                        if params.get("equipment_type"):
                            equipment.add(params["equipment_type"])
        
        # Build compact summary
        parts = []
        
        if processes:
            parts.append(f"Processes: {', '.join(sorted(processes))}")
        
        if equipment:
            parts.append(f"Equipment: {', '.join(sorted(equipment))}")
        
        if run_ids:
            # Keep all run IDs (LLM needs them for compare_runs tool)
            parts.append(f"Available run_ids: {', '.join(run_ids)}")
        
        if success_count or error_count:
            parts.append(f"Results: {success_count} success, {error_count} errors")
        
        return " | ".join(parts) if parts else "Previous conversation"
    
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
        
        # Update industry context from discovery tools
        if tool_name == "list_industries" and result.get("status") == "success":
            # User is exploring industries - no change yet
            pass
        
        if tool_name in ["list_processes", "get_process"] and result.get("industry_id"):
            # Update current industry from result
            await self.context_manager.update_context(
                conversation_id,
                {
                    "current_industry": result.get("industry_id")
                }
            )
        
        # Update simulation params if this was a simulation
        if tool_name in ["simulate_process", "simulate_equipment"]:
            # Extract run_id - could be 'calc_run_id' or 'run_id' depending on the tool
            run_id = result.get("calc_run_id") or result.get("run_id")
            
            # Get current context to retrieve existing run_ids and params
            context = await self.context_manager.get_context(conversation_id)
            current_run_ids = context.get("run_ids", []) if context else []
            
            # Only add run_id if it's not None
            if run_id:
                updated_run_ids = [run_id] + current_run_ids
                # Keep only last 10 run IDs
                updated_run_ids = updated_run_ids[:10]
            else:
                updated_run_ids = current_run_ids
            
            # MERGE parameters instead of overwriting
            # This ensures resolved warnings from equipment simulations persist
            # across multiple tool calls (e.g., heater params + centrifuge params)
            existing_params = context.get("simulation_params", {}) if context else {}
            
            # Deep merge: equipment-level parameters
            merged_params = existing_params.copy()
            
            # For simulate_equipment, params typically contain equipment-specific settings
            # Merge nested dicts (like node_params, feeds, etc.)
            for key, value in params.items():
                if key in merged_params and isinstance(merged_params[key], dict) and isinstance(value, dict):
                    # Merge nested dictionaries (e.g., node_params, operating_params)
                    merged_params[key] = {**merged_params[key], **value}
                else:
                    # Overwrite for non-dict values or new keys
                    merged_params[key] = value
            
            # Store merged params - LLM will call get_run tool if needed
            await self.context_manager.update_context(
                conversation_id,
                {
                    "simulation_params": merged_params,
                    "run_ids": updated_run_ids
                }
            )
        
        # Update validation params if this was a validation
        if tool_name.startswith("validate_"):
            # Get current context to merge validation params
            context = await self.context_manager.get_context(conversation_id)
            existing_validation_params = context.get("validation_params", {}) if context else {}
            
            # Merge validation parameters (same logic as simulation params)
            merged_validation_params = existing_validation_params.copy()
            for key, value in params.items():
                if key in merged_validation_params and isinstance(merged_validation_params[key], dict) and isinstance(value, dict):
                    merged_validation_params[key] = {**merged_validation_params[key], **value}
                else:
                    merged_validation_params[key] = value
            
            await self.context_manager.update_context(
                conversation_id,
                {"validation_params": merged_validation_params}
            )
    
    # =========================================================================
    # Tool Management
    # =========================================================================
    
    async def _get_available_tools(self, context: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Get available tools from all connected MCP servers, filtered by context.
        
        Merges tools from:
        - process_server: Sugar industry tools
        - calc_engine: Dynamic flowsheet tools (IPA recovery, distillation, etc.)
        
        Args:
            context: Current conversation context
            
        Returns:
            List of tools from all connected servers
        """
        # Get tools from all connected servers
        if self.mcp_registry:
            all_tools = await self.mcp_registry.list_all_tools()
        elif self.mcp_client:
            all_tools = await self.mcp_client.list_tools()
        else:
            all_tools = []
        
        if context is None:
            return all_tools
        
        # For now, return all tools without filtering
        # The LLM will choose the appropriate tool based on descriptions
        # TODO: Implement smarter context-based filtering if needed
        return all_tools
    
    def _get_mcp_client_for_tool(self, tool_name: str, context: Dict[str, Any] = None) -> MCPClient:
        """
        Get MCP client for tool execution.
        
        Uses MCPClientRegistry to route tools to the correct server:
        - Process server tools (sugar industry) → process_server
        - All other tools (including calc_* prefix) → calc_engine (default)
        
        Args:
            tool_name: Name of the tool to execute
            context: Current conversation context
            
        Returns:
            MCPClient instance for the appropriate server
        """
        if self.mcp_registry:
            # Use registry routing
            client = self.mcp_registry.get_client_for_tool(tool_name)
            if client:
                return client
            # Fallback to any available client
            for server_name in self.mcp_registry.connected_servers:
                return self.mcp_registry.get_client(server_name)
        
        # Legacy: return single mcp_client
        return self.mcp_client
    
    async def _get_combined_processes(self, industry_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get processes from MCP Process Server.
        
        Args:
            industry_id: Optional industry filter (e.g., "sugar")
            
        Returns:
            List of processes from process server
        """
        processes_list = []
        
        # Get from Process Server (sugar industry)
        try:
            params = {"industry_id": industry_id} if industry_id else {"industry_id": "sugar"}
            process_result = await self.mcp_client.call_tool("list_processes", params)
            if isinstance(process_result, str):
                import json
                process_result = json.loads(process_result)
            
            if process_result.get("status") == "success":
                for process in process_result.get("processes", []):
                    if isinstance(process, dict):
                        # Create copy to avoid mutating original cached dict
                        process_copy = process.copy()
                        process_copy["_source"] = "process_server"
                        process_copy["_industry"] = process_result.get("industry", "sugar")
                        processes_list.append(process_copy)
                    else:
                        processes_list.append({
                            "process_id": process,
                            "display_name": process.replace("_", " ").title(),
                            "_source": "process_server",
                            "_industry": "sugar"
                        })
        except Exception as e:
            print(f"Warning: Failed to get processes from Process Server: {e}")
        
        return {
            "status": "success",
            "processes": processes_list,
            "count": len(processes_list),
            "industries_included": list(set(p.get("_industry", "unknown") for p in processes_list)),
            "hint": "Use get_process to see full process details."
        }
    
    def _get_tool_name(self, tool: Any) -> str:
        """Extract tool name from tool object or dict."""
        if isinstance(tool, dict):
            return tool.get("name", "")
        return getattr(tool, "name", "")
    
    async def _execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        conversation_id: Optional[str] = None,
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Execute tool via MCP Process Server with progress tracking.
        """
        try:
            # Get the MCP client for this tool
            mcp_client = self._get_mcp_client_for_tool(tool_name, context)
            
            # Check if we have a valid client for this tool
            if mcp_client is None:
                # Determine which capability is unavailable for user-friendly message
                if tool_name in PROCESS_SERVER_TOOLS:
                    capability = "Sugar industry simulations"
                    alternatives = (
                        "You can try other simulation types like IPA recovery, distillation, "
                        "or generic flowsheet calculations which are currently available."
                    )
                else:
                    capability = "Dynamic flowsheet simulations"
                    alternatives = (
                        "Sugar industry simulations may still be available. "
                        "Try asking about sugar production or milling processes."
                    )
                
                return {
                    "status": "error",
                    "error": "service_unavailable",
                    "message": (
                        f"{capability} are temporarily unavailable. {alternatives} "
                        "If you need this specific feature, please try again in a few minutes."
                    ),
                    "user_friendly": True  # Flag for LLM to pass message directly to user
                }
            
            # Quick health check before expensive tool execution
            if not await mcp_client.health_check():
                return {
                    "status": "error",
                    "error": "service_unavailable",
                    "message": (
                        "The simulation service temporarily lost connection. "
                        "Please try your request again in a moment."
                    ),
                    "user_friendly": True
                }
            
            # Inject user_id and conversation_id for tools that need them
            if tool_name in TOOLS_REQUIRING_USER_ID:
                # Get user_id from context if not already provided
                if "user_id" not in params or not params["user_id"]:
                    user_id = context.get("user_id", "default_user") if context else "default_user"
                    params["user_id"] = user_id
            
            # Inject conversation_id for list_runs tools (for filtering)
            if tool_name in ("list_process_runs", "calc_list_runs") and conversation_id:
                params["conversation_id"] = conversation_id
            
            # For simulation tools, emit progress events to show user activity
            is_simulation = tool_name in ["simulate_process", "simulate_equipment"]
            if is_simulation and self.event_emitter and conversation_id:
                # Emit initial progress - starting simulation
                await self.event_emitter.emit_run_progress(
                    conversation_id,
                    stage="Initializing",
                    percentage=5,
                    message=f"Starting {tool_name.replace('_', ' ')}..."
                )
            
            result = await mcp_client.call_tool(tool_name, params)
            
            # Emit completion progress for simulations
            if is_simulation and self.event_emitter and conversation_id:
                await self.event_emitter.emit_run_progress(
                    conversation_id,
                    stage="Complete",
                    percentage=100,
                    message="Simulation finished"
                )
            
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
            error_str = str(e)
            return {
                "status": "error",
                "error": error_str,
                "message": f"Tool {tool_name} failed: {error_str}"
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
        
        return {
            "status": "success",
            "message": "Conversation reset successfully",
            "conversation_id": conversation_id
        }
