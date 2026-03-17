"""
Orchestration Service - Agentic Loop Implementation

Central coordinator that connects all components with an agentic loop:
- Context Manager: Track conversation state and run history
- Tool Registry: Get available tools from connected MCP servers
- LLM Provider: Get tool decisions from Claude/Gemini
- MCP Client(s): Execute tools on process_server and calc_engine
- Inline policy gates: Enforce sequencing rules (e.g., impact-before-simulate)

Agentic Loop Flow:
1. User message → Load context
2. Get tools from all connected MCP servers
3. LOOP:
   a. Send message + tools + previous results to LLM
   b. If LLM wants tools → Apply inline policy gates → Execute approved tools → Return results
   c. If LLM has no more tool calls → Return text response
4. Return final response to user

The LLM can:
- See tool errors and recover by calling different tools
- Chain multiple tools together across MCP servers
- Complete complex multi-step tasks (discovery → analysis → simulation)
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
    # Calc Engine Phase 5 tools (equipment chaining)
    "calc_chain_equipment",  # Required: user_id needed for run tracking and saving
    # Calc Engine Phase 6 tools (post-simulation intelligence)
    "calc_analyze_parameter_impact",  # Required: user_id needed to load process template overrides
    # Calc Engine Phase 2 tools (parameter versioning)
    "calc_get_editable_parameters",  # Required
    "calc_validate_parameters",  # Uses user overrides for validation context
    # "edit_parameters",  # REMOVED: calc_simulate_process now auto-saves parameters on success
    "calc_get_user_parameters",  # Required: retrieves user's versions
    "calc_switch_parameter_version",  # Required: changes user's active version
    "calc_compare_parameters",  # Required: compares user's versions
}
from app.services.tool_registry import ToolRegistry
from app.services.event_emitter import EventEmitter
from app.core.llm_provider import ClaudeProvider
from app.core.mcp_client import MCPClient, MCPServerConfig, MCPClientRegistry


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
            mcp_client: Legacy single MCP client (deprecated, use mcp_registry)
            mcp_registry: MCP client registry for multiple servers
            event_emitter: Event emitter for real-time updates (optional)
            anthropic_api_key: Anthropic API key for Claude
        """
        self.context_manager = context_manager
        self.tool_registry = tool_registry
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
        user_id: str = "default_user",
        metadata: Optional[Dict[str, Any]] = None
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
            metadata: Optional dict forwarded from frontend; if it contains
                      ``re_simulation=True`` the agentic loop injects a
                      directive block so the LLM simulates immediately.
            
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
            agent_steps_log = []  # Tracks interleaved text + tool steps for persistence
            total_tool_calls = 0

            # Tool result cache — prevents duplicate calls with identical arguments
            # Key: (tool_name, frozenset(sorted(params.items()))) → result
            tool_result_cache: Dict[str, Any] = {}

            # Universal call history — detect ANY duplicate tool call
            all_call_history: Dict[str, Dict] = {}

            # Loop exit tracking
            loop_exit_reason = "max_iterations"  # default if loop completes all iterations
            tool_limit_reached = False
            
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
                # Wind-down: approaching iteration limit, tell LLM to wrap up
                if iteration >= MAX_ITERATIONS - 2 and all_tool_results:
                    conversation_history.append({
                        "role": "user",
                        "content": (
                            "[INTERNAL] You have gathered enough information. On your next response, "
                            "stop calling tools and respond naturally to the user with all results "
                            "you have so far. Do NOT mention any limits or constraints."
                        )
                    })

                # Call LLM with conversation history (non-streaming, response via HTTP)
                # Retry logic for rate limits and transient API errors (429, 529)
                max_retries = 3
                
                thinking_start = datetime.now()
                llm_response = None
                
                for retry in range(max_retries + 1):
                    try:
                        llm_response = await self._call_llm_with_history(
                            conversation_history, 
                            tools, 
                            context,
                            conversation_id,  # Pass conversation_id for streaming events
                            assistant_message_id,  # Pass message_id for error recovery
                            metadata=metadata or {}
                        )
                        break  # Success - exit retry loop
                        
                    except Exception as e:
                        error_message = str(e)
                        is_rate_limit = "rate_limit_error" in error_message or "429" in error_message
                        is_overloaded = "overloaded_error" in error_message or "529" in error_message
                        is_retryable = is_rate_limit or is_overloaded
                        
                        if is_retryable and retry < max_retries:
                            # Exponential backoff: 5s, 10s, 20s for overloaded; 30s, 60s, 120s for rate limit
                            if is_overloaded:
                                retry_delay = 5 * (2 ** retry)  # 5s, 10s, 20s
                            else:
                                retry_delay = 30 * (2 ** retry)  # 30s, 60s, 120s
                            print(f"LLM API {'overloaded' if is_overloaded else 'rate limited'}, retrying in {retry_delay}s (attempt {retry + 1}/{max_retries})")
                            await asyncio.sleep(retry_delay)
                            continue
                        
                        # Not retryable or out of retries - re-raise
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
                    loop_exit_reason = "normal"
                    # Update accumulated text for final response
                    total_accumulated_text = message_text
                    
                    # PHASE 2.3: Update assistant message with final content and status=complete
                    # Build per-message tool_executions list for persistent inline display
                    def _extract_summary_store(tc):
                        """Extract a string summary from a tool result for storage."""
                        r = tc.get("result", {})
                        msg = r.get("message")
                        if isinstance(msg, str):
                            return msg
                        s = r.get("summary")
                        if isinstance(s, str):
                            return s
                        return str(r)[:200]

                    tool_executions_to_store = [
                        {
                            "tool_name": tc.get("name", "unknown"),
                            "status": "error" if tc.get("result", {}).get("status") in ["error", "failed", "simulation_failed"] else "success",
                            "duration_ms": tc.get("duration_ms"),
                            "summary": _extract_summary_store(tc),
                            "error": tc.get("result", {}).get("error") if tc.get("result", {}).get("status") in ["error", "failed", "simulation_failed"] else None,
                            "arguments": tc.get("arguments"),
                            "result": tc.get("result"),
                        }
                        for tc in all_tool_results
                    ]
                    agent_steps_log.append({"type": "text", "content": message_text, "is_final": True})
                    await self.context_manager.update_message(
                        conversation_id,
                        assistant_message_id,
                        content=message_text,
                        status=MessageStatus.COMPLETE,
                        metadata={"iterations": iteration + 1, "tool_calls": len(all_tool_results), "tool_executions": tool_executions_to_store, "agent_steps": agent_steps_log}
                    )
                    
                    # Emit thinking end
                    if self.event_emitter:
                        await self.event_emitter.emit_thinking_end(conversation_id, thinking_duration_ms)
                    # Response returned via HTTP (no emit_message_final needed)
                    print(f"[TIMING] thinking_end emitted for {conversation_id}, HTTP response returning NOW")
                    
                    # Get updated context with run_ids
                    final_context = await self.context_manager.get_context(conversation_id)
                    run_ids = final_context.get("run_ids", [])
                    
                    # Result links are now generated by MCP servers in tool responses
                    # LLM includes the result_link from tool output in its message
                    
                    # Format tool executions for response
                    def _extract_summary(tc):
                        """Extract a string summary from a tool result dict."""
                        r = tc.get("result", {})
                        msg = r.get("message")
                        if isinstance(msg, str):
                            return msg
                        s = r.get("summary")
                        if isinstance(s, str):
                            return s
                        return str(r)[:200]

                    tool_executions = [
                        {
                            "tool_name": tc.get("name", "unknown"),
                            "status": "success" if tc.get("result", {}).get("status") != "error" else "error",
                            "duration_ms": tc.get("duration_ms"),
                            "summary": _extract_summary(tc),
                            "arguments": tc.get("arguments"),
                            "result": tc.get("result"),
                        }
                        for tc in all_tool_results
                    ]

                    # ── Token cost summary ──────────────────────────────
                    # Claude Sonnet 4: $3/M input, $15/M output
                    _in   = total_input_tokens
                    _out  = total_output_tokens
                    _cost = (_in * 3 + _out * 15) / 1_000_000
                    print(
                        f"[TOKENS] conv={conversation_id[:8]}  "
                        f"in={_in:,}  out={_out:,}  total={_in+_out:,}  "
                        f"cost≈${_cost:.4f}"
                    )
                    # ────────────────────────────────────────────────────

                    return {
                        "status": "success",
                        "message": message_text,
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
                # Emit intermediate text to frontend via SSE for step-by-step display
                if message_text and message_text.strip() and self.event_emitter:
                    await self.event_emitter.emit_agent_text(
                        conversation_id, message_text.strip(), iteration
                    )
                    agent_steps_log.append({"type": "text", "content": message_text.strip(), "iteration": iteration})
                
                # Add assistant message with BOTH text and tool calls to history
                assistant_msg = {"role": "assistant", "tool_calls": tool_calls_list}
                if message_text and message_text.strip():
                    assistant_msg["content"] = message_text  # Preserve intermediate text
                conversation_history.append(assistant_msg)
                
                # Execute tools and collect results
                iteration_tool_results = []
                
                # ── POLICY GATE: Impact-before-simulate ──────────────
                # If the LLM tries to call calc_analyze_parameter_impact
                # AND calc_simulate_process in the same turn, block the
                # simulate. The user must see impact results and choose
                # Option A/B before simulation runs.
                tool_names_this_turn = set()
                for tc in tool_calls_list:
                    if isinstance(tc, dict):
                        tool_names_this_turn.add(tc.get("name", ""))
                    else:
                        tool_names_this_turn.add(getattr(tc, "name", ""))
                
                impact_and_simulate_same_turn = (
                    "calc_analyze_parameter_impact" in tool_names_this_turn
                    and "calc_simulate_process" in tool_names_this_turn
                )
                # ─────────────────────────────────────────────────────
                
                for tool_call in tool_calls_list:
                    # Safety check: don't exceed tool call limit
                    total_tool_calls += 1
                    if total_tool_calls > MAX_TOOL_CALLS:
                        total_tool_calls -= 1  # undo increment
                        tool_limit_reached = True
                        break
                    
                    # Extract tool info
                    if isinstance(tool_call, dict):
                        tool_name = tool_call.get("name")
                        tool_params = tool_call.get("input", tool_call.get("arguments", {}))
                    else:
                        tool_name = tool_call.name
                        tool_params = tool_call.input if hasattr(tool_call, 'input') else tool_call.arguments
                    
                    # ── POLICY GATE: Block simulate if impact analysis is in same turn ──
                    # The user must see the downstream impact and choose before simulating.
                    if impact_and_simulate_same_turn and tool_name == "calc_simulate_process":
                        gate_result = {
                            "status": "blocked",
                            "error": (
                                "POLICY: calc_simulate_process was blocked because calc_analyze_parameter_impact "
                                "was called in the same turn. You MUST present the impact analysis results to the "
                                "user first and wait for their choice (Option A or Option B) before simulating. "
                                "Do NOT call calc_simulate_process until the user responds."
                            ),
                        }
                        iteration_tool_results.append({
                            "name": tool_name,
                            "result": gate_result
                        })
                        all_tool_results.append({
                            "name": tool_name,
                            "tool": tool_name,
                            "arguments": tool_params,
                            "result": gate_result,
                            "duration_ms": 0,
                            "status": "blocked"
                        })
                        continue
                    
                    # ── Duplicate call detection ─────────────────────────
                    # Build a stable cache key from tool name + sorted params.
                    # Only cache read-only / idempotent discovery tools;
                    # simulation tools must always execute.
                    _CACHEABLE_TOOLS = {
                        "calc_list_processes", "calc_get_process",
                        "calc_list_runs", "calc_get_run",
                        "calc_get_editable_parameters",
                        "list_industries", "list_processes", "get_process",
                    }
                    # Optional filter keys per tool — a cached result with fewer
                    # filters subsumes a request with more filters.
                    _SUBSUMABLE_FILTER_KEYS = {
                        "calc_list_processes": {"category", "template_type", "equipment_type"},
                        "calc_get_editable_parameters": {"equipment_id"},
                    }
                    try:
                        _cache_key = f"{tool_name}::{json.dumps(tool_params, sort_keys=True, default=str)}"
                    except Exception:
                        _cache_key = None  # unhashable params — skip cache

                    # ── Universal duplicate detection (all tools) ──
                    # If this exact call was already made (any tool), return
                    # previous result immediately — no re-execution.
                    if _cache_key and _cache_key in all_call_history and tool_name not in _CACHEABLE_TOOLS:
                        dup_result = all_call_history[_cache_key]["result"]
                        dup_result_with_notice = {**dup_result, "_duplicate_notice": "You already called this tool with identical arguments. Use the previous result."}
                        total_tool_calls -= 1  # don't count against budget
                        iteration_tool_results.append({
                            "name": tool_name,
                            "result": dup_result_with_notice
                        })
                        all_tool_results.append({
                            "name": tool_name,
                            "tool": tool_name,
                            "arguments": tool_params,
                            "result": dup_result,
                            "duration_ms": 0,
                            "status": "success"
                        })
                        if self.event_emitter:
                            await self.event_emitter.emit_tool_start(conversation_id, tool_name, tool_params)
                            await self.event_emitter.emit_tool_end(
                                conversation_id, tool_name, "success", 0,
                                f"{tool_name} (duplicate)", error_message=None,
                                result=dup_result
                            )
                        agent_steps_log.append({"type": "tool", "tool_name": tool_name, "status": "success", "duration_ms": 0, "arguments": tool_params, "result": dup_result})
                        continue

                    cached_result = tool_result_cache.get(_cache_key) if (_cache_key and tool_name in _CACHEABLE_TOOLS) else None

                    # If no exact cache hit, check for a broader (fewer filters) cached result
                    if cached_result is None and _cache_key and tool_name in _SUBSUMABLE_FILTER_KEYS:
                        filter_keys = _SUBSUMABLE_FILTER_KEYS[tool_name]
                        base_params = {k: v for k, v in tool_params.items() if k not in filter_keys}
                        our_filters = {k: v for k, v in tool_params.items() if k in filter_keys and v is not None}
                        if our_filters:  # only check if we're actually filtering
                            for ck, cv in tool_result_cache.items():
                                if not ck.startswith(f"{tool_name}::"):
                                    continue
                                try:
                                    cached_params = json.loads(ck.split("::", 1)[1])
                                except (json.JSONDecodeError, IndexError):
                                    continue
                                cached_base = {k: v for k, v in cached_params.items() if k not in filter_keys}
                                cached_filters = {k: v for k, v in cached_params.items() if k in filter_keys and v is not None}
                                if cached_base == base_params and len(cached_filters) < len(our_filters):
                                    cached_result = cv
                                    break

                    if cached_result is not None:
                        # Return cached result without re-executing
                        result = cached_result["result"]
                        tool_duration_ms = 0
                        if self.event_emitter:
                            await self.event_emitter.emit_tool_start(conversation_id, tool_name, tool_params)
                            await self.event_emitter.emit_tool_end(
                                conversation_id, tool_name, "success", 0,
                                f"{tool_name} (cached)", error_message=None,
                                result=result
                            )
                        agent_steps_log.append({"type": "tool", "tool_name": tool_name, "status": "success", "duration_ms": 0, "arguments": tool_params, "result": result})
                    else:
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

                        # Store in cache for cacheable tools on success
                        if _cache_key and tool_name in _CACHEABLE_TOOLS and result.get("status") != "error":
                            tool_result_cache[_cache_key] = {"result": result}

                        # Store in universal call history (all tools, all results)
                        if _cache_key:
                            all_call_history[_cache_key] = {"result": result}

                        # Emit tool end (only for non-cached — cached already emitted above)
                        if self.event_emitter:
                            # Treat 'not_converged' as success - simulation ran, just didn't converge
                            # Only 'error', 'failed', 'simulation_failed' are actual tool failures
                            result_status = result.get("status", "")
                            status = "error" if result_status in ["error", "failed", "simulation_failed"] else "success"
                            # Build a meaningful string summary for SSE
                            _msg = result.get("message")
                            if isinstance(_msg, str):
                                summary = _msg
                            else:
                                summary = f"{tool_name} completed"
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
                                error_message=error_msg,
                                result=result
                            )
                        agent_steps_log.append({"type": "tool", "tool_name": tool_name, "status": status, "duration_ms": tool_duration_ms, "arguments": tool_params, "result": result})
                    
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
                        "arguments": tool_params,
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

                # If tool limit was reached, force natural response
                if tool_limit_reached:
                    loop_exit_reason = "tool_limit"
                    break

                # Continue loop - LLM will see results in next iteration

            # =====================================================
            # LOOP EXITED: Handle tool_limit or max_iterations
            # =====================================================
            if loop_exit_reason in ("tool_limit", "max_iterations"):
                print(f"INFO: Loop exited due to {loop_exit_reason}, forcing natural response")
                return await self._force_natural_response(
                    conversation_history=conversation_history,
                    context=context,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message_id,
                    all_tool_results=all_tool_results,
                    metadata=metadata,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    agent_steps_log=agent_steps_log,
                )
            
        except Exception as e:
            raw_error = str(e)
            
            # Map known API errors to user-friendly messages
            error_msg = self._get_user_friendly_error(raw_error)
            
            # PHASE 2.4: Update assistant message with error status and any partial content
            # Use total_accumulated_text if available, otherwise just error message
            try:
                from app.services.context_manager import MessageStatus
                partial_content = total_accumulated_text if total_accumulated_text else ""
                final_content = partial_content + ("\n\n[" + error_msg + "]" if partial_content else error_msg)
                
                await self.context_manager.update_message(
                    conversation_id,
                    assistant_message_id,
                    content=final_content,
                    status=MessageStatus.ERROR,
                    metadata={"error": raw_error[:500]}
                )
            except Exception as update_error:
                print(f"Failed to update message with error: {update_error}")
            
            if self.event_emitter:
                # Determine error type for structured event
                from app.models.events import ErrorType
                if "overloaded_error" in raw_error or "529" in raw_error:
                    event_error_type = ErrorType.LLM_ERROR
                elif "rate_limit_error" in raw_error or "429" in raw_error:
                    event_error_type = ErrorType.RATE_LIMIT_ERROR
                elif "API usage limits" in raw_error:
                    event_error_type = ErrorType.RATE_LIMIT_ERROR
                else:
                    event_error_type = ErrorType.INTERNAL_ERROR
                
                await self.event_emitter.emit_app_error(
                    conversation_id,
                    error_type=event_error_type,
                    error_message=error_msg,
                    details={"exception": raw_error[:300]},
                    recoverable=True
                )
                # Emit thinking_end so SSE stream terminates and frontend
                # doesn't hang waiting for it in waitForCompletion()
                await self.event_emitter.emit_thinking_end(conversation_id, 0)
            
            print(f"ERROR: Error processing message: {e}"); import traceback; traceback.print_exc()
            return {
                "status": "error",
                "message": error_msg,
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
    # Error Message Helpers
    # =========================================================================
    
    @staticmethod
    def _get_user_friendly_error(raw_error: str) -> str:
        """Map raw API/system errors to user-friendly messages."""
        if "overloaded_error" in raw_error or "529" in raw_error:
            return "Our AI service is temporarily busy due to high demand. Please try again in a few seconds."
        elif "rate_limit_error" in raw_error or "429" in raw_error:
            return "Too many requests — please wait a moment and try again."
        elif "API usage limits" in raw_error or "You have reached your specified" in raw_error:
            return "API usage limit reached. Service will resume after the limit resets."
        elif "authentication_error" in raw_error or "401" in raw_error:
            return "AI service authentication error. Please contact support."
        elif "invalid_request_error" in raw_error or "400" in raw_error:
            return "There was an issue processing your request. Please try rephrasing your message."
        elif "connection" in raw_error.lower() or "timeout" in raw_error.lower():
            return "Connection issue with the AI service. Please check your network and try again."
        else:
            return "Something went wrong while processing your request. Please try again."

    # =========================================================================
    # LLM Communication
    # =========================================================================
    
    async def _call_llm_with_history(
        self,
        conversation_history: List[Dict[str, Any]],
        tools: List[Any],
        context: Dict[str, Any],
        conversation_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
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
        metadata = metadata or {}

        # Build system prompt — concise, prioritized, with examples
        system_parts = [
            # ═══════════════════════════════════════════════════════
            # ROLE & SCOPE
            # ═══════════════════════════════════════════════════════
            "You are a senior process engineer assistant for chemical and sugar/ethanol plant simulation.",
            "Scope: distillation, evaporation, heat exchange, pumping, flash separation, adsorption, and sugar/ethanol processing.",
            "Out of scope: questions unrelated to process engineering — politely redirect.",
            "Always use tools to retrieve real values. If unsure about a parameter's physical meaning or valid range, ask the user rather than assuming.",
            "When a tool returns a result_link, include it verbatim in your response.",
            "",
            # ═══════════════════════════════════════════════════════
            # SERVER ROUTING
            # ═══════════════════════════════════════════════════════
            "SERVER ROUTING:",
            "- calc_* tools → Calculation Engine (generic flowsheets, single-equipment, chaining).",
            "- Tools without calc_ prefix → Process Server (sugar industry, fixed templates).",
            "",
            # ═══════════════════════════════════════════════════════
            # TOOL USAGE RULES (ranked by importance)
            # ═══════════════════════════════════════════════════════
            "TOOL RULES:",
            "1. Use exact IDs only — process_id, equipment_id, and parameter names are case-sensitive. Retrieve them from discovery tools first.",
            "2. Discovery calls first, broad: call calc_list_processes / calc_get_editable_parameters with NO optional filters. Add filters only if you already have valid values from a previous result.",
            "3. Mandatory prerequisite: always call calc_get_editable_parameters before calc_simulate_process or calc_analyze_parameter_impact.",
            "4. Copy equipment IDs and parameter paths exactly from calc_get_editable_parameters output into simulation parameters.",
            "5. One call per argument set — duplicate calls with identical arguments are automatically cached and returned.",
            "6. Parallel tool calls: when multiple independent tools are needed in the same turn (e.g., multiple calc_analyze_parameter_impact calls), call them in parallel.",
            "7. Single-equipment templates use generic compound placeholders — ask the user for real compounds and pass compound_mapping.",
            "8. For chaining: use calc_chain_equipment with source_run_id, source_equipment_id, source_port, target_process_id. For multi-outlet equipment, ask which outlet to use.",
            "9. Open-ended first message (e.g., 'what can you do?'): list available templates using calc_list_processes and list_processes.",
            "",
            # ═══════════════════════════════════════════════════════
            # NEW SIMULATION SEQUENCE
            # ═══════════════════════════════════════════════════════
            "NEW SIMULATION SEQUENCE:",
            "Calc Engine: calc_list_processes → calc_get_editable_parameters(process_id) → calc_simulate_process.",
            "Process Server: list_industries → list_processes → get_process → simulate_process.",
            "Each step uses IDs from the previous. Skipping discovery steps leads to errors.",
            "",
            # ═══════════════════════════════════════════════════════
            # PARAMETER CHANGE WORKFLOW (with inline example)
            # ═══════════════════════════════════════════════════════
            "PARAMETER CHANGE WORKFLOW:",
            "When a user changes any parameter, follow these steps across SEPARATE turns:",
            "",
            "Step 0 — DISCOVER (if not done for this process):",
            "  Call calc_get_editable_parameters(process_id, user_id) WITHOUT filters → get ALL equipment IDs, parameter names, valid ranges.",
            "",
            "Step 1 — ANALYZE (turn 1 — tool calls):",
            "  Call calc_analyze_parameter_impact once per changed parameter (equipment_id + bare parameter name).",
            "  Always do this step, even if the user says 'just run it'.",
            "  The backend blocks calc_simulate_process if called in the same turn as impact analysis.",
            "",
            "Step 2 — REASON AND PRESENT (turn 1 — text response, no tool calls):",
            "  After receiving impact results, stop calling tools and present:",
            "  a) Which downstream equipment is affected and how.",
            "  b) Co-dependent parameters that may need adjustment (with suggested values).",
            "  c) Engineering risks: phase boundary crossings (T/P near bubble/dew point), parameter limit violations,",
            "     coupled parameters (e.g., reflux_ratio ↔ num_stages, ΔP ↔ pump_efficiency),",
            "     operability limits (weeping, flooding, cavitation), CAPEX/OPEX trade-offs, recycle loop amplification.",
            "  d) Two options: Option A (user's change only) or Option B (user's change + your suggested co-adjustments).",
            "  If user said 'just run it', present the analysis and default to Option A.",
            "",
            "Step 3 — SIMULATE (turn 2 — after user responds):",
            "  Call calc_simulate_process once with all agreed changes.",
            "",
            "EXAMPLE — user says 'increase reflux ratio to 3.0 in my IPA distillation':",
            "  Turn 1: calc_get_editable_parameters('ipa_recovery_distillation') → discover column_1.reflux_ratio",
            "           calc_analyze_parameter_impact(process_id='ipa_recovery_distillation', equipment_id='column_1', parameter='reflux_ratio')",
            "           → respond with impact analysis, suggest adjusting feed_stage, offer Option A / B.",
            "  Turn 2 (user picks A): calc_simulate_process(process_id='ipa_recovery_distillation', parameters={'equipment.column_1.parameters.reflux_ratio': 3.0})",
            "",
            # ═══════════════════════════════════════════════════════
            # ERROR RECOVERY
            # ═══════════════════════════════════════════════════════
            "ERROR RECOVERY:",
            "Match error text → recovery action:",
            "- 'Process not found' → calc_list_processes for valid IDs.",
            "- 'Invalid parameter path(s)' → calc_get_editable_parameters for correct paths.",
            "- 'Source run not found' → calc_list_runs for valid run IDs.",
            "- 'not_converged' → report which equipment failed, show residuals, suggest smaller parameter steps, offer re-simulation.",
            "- Unknown errors → explain in plain English, suggest an alternative approach.",
            "If a tool fails, adapt — try a different tool or ask the user for clarification.",
            "",
            # ═══════════════════════════════════════════════════════
            # POST-SIMULATION RESPONSE FORMAT
            # ═══════════════════════════════════════════════════════
            "POST-SIMULATION RESPONSE FORMAT:",
            "Structure every post-simulation response as:",
            "1. KEY RESULTS — Primary KPIs, mass/energy balance closure.",
            "2. COMPARISON — If a parent run exists, quantify deltas (e.g., 'purity dropped 99.5% → 94.2%'). Use compare tools if available.",
            "3. VALIDATION FLAGS — Group by severity (CRITICAL → WARNING → INFO):",
            "   - Explain each flag in plain English with real-world consequence.",
            "   - For quick_fix / fix_hint / suggested_equipment fields: present as actionable proposals the user can approve.",
            "   - No flags? Say 'Simulation looks physically reasonable — no issues detected.'",
            "4. SUGGESTIONS — Suboptimal co-dependent parameters, specific improvement values, offer to re-simulate.",
            "If not converged: list which equipment failed vs. converged, show residuals, suggest targeted adjustments.",
            "",
            # ═══════════════════════════════════════════════════════
            # RESPONSE GUIDELINES
            # ═══════════════════════════════════════════════════════
            "RESPONSE GUIDELINES:",
            "- Be concise but complete. Avoid repeating tool output verbatim — summarise and interpret.",
            "- Use tables for comparing KPIs across runs.",
            "- Keep response under 800 words unless the user asks for detail.",
            "- If all servers are unavailable mid-conversation, tell the user the simulation service is temporarily unreachable and suggest retrying shortly.",
        ]

        # ── Re-simulation directive block ──────────────────────────────────────
        # When the frontend sends re_simulation=True in metadata it also sends
        # a fully-classified parameter payload.  Inject an explicit directive so
        # the LLM does NOT ask clarifying questions and fires the right tool
        # immediately.
        if metadata.get("re_simulation"):
            source          = metadata.get("source", "")           # "calc_engine" | "process_server"
            process_id      = metadata.get("process_id", "")
            template_type   = metadata.get("template_type", "")    # "single_equipment" | "process"
            parent_run_id   = metadata.get("parent_run_id", "")
            eq_edits        = metadata.get("equipment_param_edits", {})
            feed_edits      = metadata.get("feed_stream_edits", {})
            compound_map    = metadata.get("compound_mapping", {})

            # Choose the right MCP tool name based on source + template_type
            if source == "calc_engine":
                tool_hint = "calc_simulate_process"
                # Format equipment params as dot-notation inline_overrides
                inline_parts = []
                for equip_id, params in eq_edits.items():
                    for param, val in params.items():
                        inline_parts.append(f'"equipment.{equip_id}.parameters.{param}": {val}')
                for stream_id, props in feed_edits.items():
                    for prop, val in props.items():
                        inline_parts.append(f'"feed_streams.{stream_id}.{prop}": {val}')
                overrides_hint = "{" + ", ".join(inline_parts) + "}" if inline_parts else "{}"
            else:
                # process_server — single_equipment or process
                tool_hint = "simulate_equipment" if template_type == "single_equipment" else "simulate_process"
                overrides_hint = str({"node_params": eq_edits, "feeds": feed_edits})

            # Build the list of equipment IDs and parameters being changed
            # for the dependency analysis directive
            changed_params_info = []
            for equip_id, params in eq_edits.items():
                for param_name, param_val in params.items():
                    changed_params_info.append({
                        "equipment_id": equip_id,
                        "parameter": param_name,
                        "new_value": param_val
                    })

            directive_lines = [
                "",
                "RE-SIMULATION REQUEST (from UI parameter edit):",
                f"- Parent run: {parent_run_id}",
                f"- Source: {source} | Process: {process_id} | Template: {template_type}",
                f"- Parameters changed: {changed_params_info}",
                f"- Simulation tool: {tool_hint}",
                f"- process_id: \"{process_id}\"",
                f"- Parameter overrides: {overrides_hint}",
            ]
            if compound_map:
                directive_lines.append(f"- compound_mapping: {compound_map}")
            elif template_type == "single_equipment" and source == "calc_engine":
                directive_lines.append(
                    f"- Single-equipment template — reuse compound_mapping from parent run ({parent_run_id})."
                )

            if changed_params_info and source == "calc_engine":
                # Tell LLM to follow the PARAMETER CHANGE WORKFLOW with specific tool args
                directive_lines.append("")
                directive_lines.append("Follow the PARAMETER CHANGE WORKFLOW above. Specific tool calls for Step 1:")
                for cp in changed_params_info:
                    directive_lines.append(
                        f"  - calc_analyze_parameter_impact(process_id=\"{process_id}\", "
                        f"equipment_id=\"{cp['equipment_id']}\", parameter=\"{cp['parameter']}\")"
                    )
                directive_lines.append(f"For Step 3, use {tool_hint} with process_id=\"{process_id}\".")
                directive_lines.append(f"After simulation, compare results against parent run {parent_run_id}.")
            else:
                # Process server — no dependency analysis, just simulate
                directive_lines.append("")
                directive_lines.append(f"Call {tool_hint} with the parameter overrides above. Do not ask for confirmation.")
                directive_lines.append("After simulation, use POST-SIMULATION RESPONSE FORMAT above.")

            system_parts.extend(directive_lines)
        # ── End re-simulation directive ────────────────────────────────────────
        
        # Add current context if available (industry, process)
        if context.get("current_industry") or context.get("current_process"):
            context_info = []
            if context.get("current_industry"):
                context_info.append(f"Industry: {context['current_industry']}")
            if context.get("current_process"):
                context_info.append(f"Process: {context['current_process']}")
            system_parts.append(f"\nCurrent context: {', '.join(context_info)}")

        # Inject confirmed process IDs so the LLM never has to guess them again.
        # These are IDs that were returned by calc_list_processes or successfully
        # used in a simulation — they are guaranteed to exist in the database.
        confirmed_pids = context.get("confirmed_process_ids", [])
        if confirmed_pids:
            system_parts.append(
                f"\nConfirmed process_ids available in this conversation (use these EXACTLY — do not guess or modify them): "
                + ", ".join(f'"{p}"' for p in confirmed_pids)
            )

        # Add run history reference for follow-up questions
        run_ids = context.get("run_ids", [])
        if run_ids:
            system_parts.append(f"\n{len(run_ids)} previous simulation(s) available in this conversation.")

        # ── Inject last simulation summary (Step 1 intelligence) ────────
        # Gives the LLM memory of the most recent run's KPIs so it can
        # answer follow-up questions and compare without calling get_run.
        sim_summary = context.get("last_simulation_summary")
        if sim_summary and isinstance(sim_summary, dict):
            summary_lines = ["\nLAST SIMULATION SUMMARY (use for comparison and follow-ups):"]
            summary_lines.append(f"  Run ID: {sim_summary.get('run_id', 'N/A')}")
            summary_lines.append(f"  Process: {sim_summary.get('process_id', 'N/A')}")
            summary_lines.append(f"  Status: {'converged' if sim_summary.get('converged') else sim_summary.get('simulation_status', 'unknown')}")

            kpis = sim_summary.get("kpis")
            if kpis and isinstance(kpis, dict):
                # Show scalar KPIs first
                scalar_kpis = {k: v for k, v in kpis.items() if not isinstance(v, (dict, list))}
                if scalar_kpis:
                    kpi_str = ", ".join(f"{k}={v}" for k, v in scalar_kpis.items())
                    summary_lines.append(f"  KPIs: {kpi_str}")
                # Show per-equipment KPIs if present
                equip_kpis = kpis.get("equipment_kpis")
                if equip_kpis and isinstance(equip_kpis, list):
                    for ek in equip_kpis[:5]:  # limit to top 5
                        parts = [f"{k}={v}" for k, v in ek.items() if k != "equipment_id"]
                        summary_lines.append(f"    {ek.get('equipment_id', '?')}: {', '.join(parts)}")

            mb = sim_summary.get("mass_balance_closure")
            if mb is not None:
                summary_lines.append(f"  Mass balance closure: {mb}")
            eb = sim_summary.get("energy_balance_closure")
            if eb is not None:
                summary_lines.append(f"  Energy balance closure: {eb}")

            flags = sim_summary.get("validation_flag_count", {})
            if any(v > 0 for v in flags.values()):
                flag_str = ", ".join(f"{k}: {v}" for k, v in flags.items() if v > 0)
                summary_lines.append(f"  Validation flags: {flag_str}")

            summary_lines.append(f"  Timestamp: {sim_summary.get('timestamp', 'N/A')}")
            summary_lines.append("Use this summary to compare with new simulation results. Highlight improvements and regressions in KPIs.")
            system_parts.append("\n".join(summary_lines))

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
            is_overloaded = "overloaded_error" in error_message or "529" in error_message
            is_usage_limit = "API usage limits" in error_message or "You have reached your specified" in error_message
            
            if is_overloaded:
                # API server overloaded (529) - transient, will be retried by caller
                if assistant_message_id:
                    try:
                        from app.services.context_manager import MessageStatus
                        await self.context_manager.update_message(
                            conversation_id,
                            assistant_message_id,
                            content="[AI service temporarily busy - retrying automatically]",
                            status=MessageStatus.ERROR,
                            metadata={"error": "overloaded"}
                        )
                    except Exception:
                        pass
                
                if self.event_emitter and conversation_id:
                    from app.models.events import ErrorType
                    await self.event_emitter.emit_app_error(
                        conversation_id,
                        error_type=ErrorType.LLM_ERROR,
                        error_message="AI service is temporarily busy. Retrying automatically...",
                        details={
                            "error": "Anthropic API overloaded (529)",
                            "suggestion": "This is a temporary issue and will be retried automatically",
                        }
                    )
            
            elif is_usage_limit:
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
        if tool_name in ["simulate_process", "simulate_equipment", "calc_simulate_process", "calc_chain_equipment"]:
            # Extract run_id - could be 'calc_run_id' or 'run_id' depending on the tool
            run_id = result.get("calc_run_id") or result.get("run_id")
            
            # Get current context to retrieve existing run_ids and params
            context = await self.context_manager.get_context(conversation_id)
            current_run_ids = context.get("run_ids", []) if context else []
            
            # Only add run_id if it's not None and not already present
            if run_id:
                if run_id not in current_run_ids:
                    updated_run_ids = [run_id] + current_run_ids
                    # Keep only last 10 run IDs
                    updated_run_ids = updated_run_ids[:10]
                else:
                    updated_run_ids = current_run_ids
            else:
                updated_run_ids = current_run_ids

            # Store the confirmed process_id used in this simulation so the
            # system prompt can surface it to the LLM on subsequent turns,
            # preventing it from guessing/hallucinating the ID again.
            confirmed_pid = params.get("process_id")
            if confirmed_pid:
                existing_pids = set(context.get("confirmed_process_ids", []) if context else [])
                existing_pids.add(confirmed_pid.lower())
                confirmed_process_ids = list(existing_pids)
            else:
                confirmed_process_ids = context.get("confirmed_process_ids", []) if context else []
            
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
                    "run_ids": updated_run_ids,
                    "confirmed_process_ids": confirmed_process_ids,
                }
            )

            # ── Populate last_simulation_summary (Step 1 intelligence) ──
            # Gives the LLM memory of the most recent simulation's KPIs,
            # convergence status, and validation flags so it can compare
            # across turns without a separate get_run call.
            await self.context_manager.update_simulation_summary(
                conversation_id, tool_name, result, params
            )
        
        # Capture confirmed process_ids from discovery tools so the LLM can
        # reuse them on the next turn without re-listing or guessing.
        if tool_name in ["calc_list_processes"] and result.get("status") == "success":
            context = await self.context_manager.get_context(conversation_id)
            existing_pids = set(context.get("confirmed_process_ids", []) if context else [])
            for item in result.get("processes", []):
                pid = item.get("process_id")
                if pid:
                    existing_pids.add(pid.lower())
            if existing_pids:
                await self.context_manager.update_context(
                    conversation_id,
                    {"confirmed_process_ids": list(existing_pids)}
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
    # Force Natural Response (wind-down / limit recovery)
    # =========================================================================

    async def _force_natural_response(
        self,
        conversation_history: List[Dict[str, Any]],
        context: Dict[str, Any],
        conversation_id: str,
        assistant_message_id: str,
        all_tool_results: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        agent_steps_log: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Force the LLM to produce a natural text response with no tool calls."""
        from app.services.context_manager import MessageStatus

        conversation_history.append({
            "role": "user",
            "content": (
                "[INTERNAL] Respond to the user now using all the tool results you have. "
                "Give a complete, natural response as if everything went normally. "
                "Do NOT mention any limits, iterations, or constraints. "
                "Do NOT apologize or say anything was incomplete."
            )
        })

        final_response = await self._call_llm_with_history(
            conversation_history, [],  # empty tools = text-only
            context, conversation_id, assistant_message_id,
            metadata=metadata or {}
        )

        message_text = (final_response.text if hasattr(final_response, 'text')
                        else final_response.get("message", ""))

        # Track tokens from this final call
        if hasattr(final_response, 'usage'):
            usage = final_response.usage
            if isinstance(usage, dict):
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)
            else:
                total_input_tokens += getattr(usage, "input_tokens", 0)
                total_output_tokens += getattr(usage, "output_tokens", 0)

        # Build tool_executions for storage and response
        def _extract_summary(tc):
            r = tc.get("result", {})
            msg = r.get("message")
            if isinstance(msg, str):
                return msg
            s = r.get("summary")
            if isinstance(s, str):
                return s
            return str(r)[:200]

        tool_executions = [
            {
                "tool_name": tc.get("name", "unknown"),
                "status": "error" if tc.get("result", {}).get("status") in ["error", "failed", "simulation_failed"] else "success",
                "duration_ms": tc.get("duration_ms"),
                "summary": _extract_summary(tc),
                "arguments": tc.get("arguments"),
                "result": tc.get("result"),
            }
            for tc in all_tool_results
        ]

        if agent_steps_log is None:
            agent_steps_log = []
        # Emit the forced response text via SSE so the UI shows it in real-time
        if message_text and message_text.strip() and self.event_emitter:
            await self.event_emitter.emit_agent_text(conversation_id, message_text.strip(), -1)
        agent_steps_log.append({"type": "text", "content": message_text, "is_final": True})

        # Update message as COMPLETE (not ERROR)
        await self.context_manager.update_message(
            conversation_id, assistant_message_id,
            content=message_text, status=MessageStatus.COMPLETE,
            metadata={"iterations": len(all_tool_results), "tool_calls": len(all_tool_results), "tool_executions": tool_executions, "agent_steps": agent_steps_log}
        )

        if self.event_emitter:
            await self.event_emitter.emit_thinking_end(conversation_id, 0)

        final_context = await self.context_manager.get_context(conversation_id)

        # Token cost summary
        _in = total_input_tokens
        _out = total_output_tokens
        _cost = (_in * 3 + _out * 15) / 1_000_000
        print(
            f"[TOKENS] conv={conversation_id[:8]}  "
            f"in={_in:,}  out={_out:,}  total={_in+_out:,}  "
            f"cost≈${_cost:.4f}"
        )

        return {
            "status": "success",
            "message": message_text,
            "tool_calls": all_tool_results,
            "tool_executions": tool_executions,
            "run_ids": final_context.get("run_ids", []),
            "iterations": len(all_tool_results),
            "conversation_id": conversation_id,
            "context": final_context,
            "token_usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens
            }
        }

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
    
    # =========================================================================
    # Public API
    # =========================================================================
    
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
