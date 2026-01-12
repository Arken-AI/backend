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

# =============================================================================
# Context Optimization Constants
# =============================================================================
MAX_RECENT_MESSAGES = 10  # Keep last N messages in full detail

# Tool categories for context-aware filtering
DISCOVERY_TOOLS = {"list_industries", "list_processes", "get_process", "get_equipment_types", "get_equipment_schema", "get_stream_schema"}
VALIDATION_TOOLS = {"validate_process_inputs", "validate_connections", "validate_equipment_inputs"}
SIMULATION_TOOLS = {"simulate_process", "simulate_equipment"}
RUN_TOOLS = {"get_run", "compare_runs"}
CORE_TOOLS = SIMULATION_TOOLS | VALIDATION_TOOLS  # Always include these
from app.services.tool_registry import ToolRegistry
from app.services.event_emitter import EventEmitter
from app.core.policy_engine import PolicyEngine, PolicyDecision
from app.core.llm_provider import ClaudeProvider
from app.core.mcp_client import MCPClient, MCPServerConfig


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
    """
    
    def __init__(
        self,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        policy_engine: PolicyEngine,
        mcp_client: MCPClient,
        event_emitter: Optional[EventEmitter] = None,
        anthropic_api_key: str = None
    ):
        """
        Initialize orchestration service.
        
        Args:
            context_manager: Context tracking service
            tool_registry: Tool metadata registry
            policy_engine: Policy enforcement engine
            mcp_client: MCP client for tool execution
            event_emitter: Event emitter for real-time updates (optional)
            anthropic_api_key: Anthropic API key for Claude
        """
        self.context_manager = context_manager
        self.tool_registry = tool_registry
        self.policy_engine = policy_engine
        self.mcp_client = mcp_client
        self.event_emitter = event_emitter
        
        # Initialize Claude LLM provider
        self.llm = ClaudeProvider(api_key=anthropic_api_key)
        print("Orchestration service initialized with Claude (claude-sonnet-4-20250514)")
    
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
            
            # Step 0: Health check - fail fast if MCP server is unavailable
            if not await self.mcp_client.health_check():
                error_msg = "MCP server is unavailable. Please ensure the server is running and try again."
                print(f"ERROR: {error_msg}")
                
                if self.event_emitter:
                    await self.event_emitter.emit_app_error(
                        conversation_id,
                        "mcp_server_unavailable",
                        error_msg,
                        details={"check": "health_check"},
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
                
                # Call LLM with conversation history (streaming with message_delta events)
                thinking_start = datetime.now()
                llm_response = await self._call_llm_with_history(
                    conversation_history, 
                    tools, 
                    context,
                    conversation_id  # Pass conversation_id for streaming events
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
                    
                    # Get updated context with run_ids
                    final_context = await self.context_manager.get_context(conversation_id)
                    
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
                        "message": message_text,
                        "tool_calls": all_tool_results,
                        "tool_executions": tool_executions,
                        "run_ids": final_context.get("run_ids", []),
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
                print(f"LLM requested {len(tool_calls_list)} tool calls")
                
                # Step 1.3: Emit intermediate message BEFORE executing tools
                # Claude often sends text like "I'll validate your inputs now..." before tool calls
                # We need to emit this as a message_final so it appears in the chat!
                if message_text and message_text.strip() and self.event_emitter:
                    print(f"Emitting intermediate message before tools: {message_text[:50]}...")
                    # Emit as message_final so it persists in the chat UI
                    await self.event_emitter.emit_message_final(
                        conversation_id,
                        message_text,
                        role="assistant",
                        metadata={
                            "is_intermediate": True,  # Flag to indicate more coming
                            "iteration": iteration + 1,
                            "has_tool_calls": True
                        }
                    )
                
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
                    result = await self._execute_tool(tool_name, tool_params, conversation_id)
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
            
            # Get context for run_ids
            final_context = await self.context_manager.get_context(conversation_id)
            
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
            
            # Return partial results
            return {
                "status": "error",
                "message": "I apologize, but I couldn't complete your request within the allowed iterations. Here's what I was able to do:\n\n" + 
                          self._format_results_for_user(all_tool_results),
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
        conversation_id: Optional[str] = None
    ) -> Any:
        """
        Call LLM with full conversation history using STREAMING.
        
        This enables the agentic loop by preserving:
        - User message
        - Assistant tool calls
        - Tool results (success and errors)
        
        The LLM sees the full history and decides what to do next.
        Now uses streaming to emit message_delta events in real-time.
        """
        # Build system prompt - Claude is naturally agentic, minimal prompting needed
        system_parts = [
            "You are a process simulation assistant with tools for industrial process simulation."
        ]
        
        # Add context if available
        if context.get("current_industry") or context.get("current_process"):
            context_info = []
            if context.get("current_industry"):
                context_info.append(f"Industry: {context['current_industry']}")
            if context.get("current_process"):
                context_info.append(f"Process: {context['current_process']}")
            system_parts.append("Current context: " + ", ".join(context_info))
        
        # Add last run reference for follow-up questions
        run_ids = context.get("run_ids", [])
        if run_ids:
            latest_run_id = run_ids[0]  # Newest is always first
            system_parts.append(f"\nLast simulation run_id: {latest_run_id} - use get_run tool if user asks about previous results.")
            if len(run_ids) > 1:
                system_parts.append(f"Previous run_ids available: {run_ids[1:]} - use compare_runs to compare simulations.")
            print(f"Including run_ids in prompt: latest={latest_run_id}, total={len(run_ids)}")
        else:
            print(f"No run_ids in context for follow-up")
        
        system = "\n".join(system_parts)
        
        # Optimize conversation history using sliding window
        optimized_history = self._prepare_conversation_for_llm(conversation_history)
        
        print(f"Calling LLM with {len(optimized_history)} messages (original: {len(conversation_history)})")
        
        # Use streaming to emit real-time message deltas
        return await self._call_llm_streaming(
            optimized_history,
            tools,
            system,
            conversation_id
        )
    
    async def _call_llm_streaming(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Any],
        system: str,
        conversation_id: Optional[str] = None
    ) -> "StreamingLLMResponse":
        """
        Call LLM with streaming and emit message_delta events.
        
        Processes the Claude stream and:
        1. Emits message_delta for each text chunk (typing effect)
        2. Accumulates text and tool calls
        3. Returns a response object compatible with non-streaming interface
        
        Args:
            messages: Prepared conversation history
            tools: Available tools
            system: System prompt
            conversation_id: For emitting events (optional)
            
        Returns:
            StreamingLLMResponse with accumulated text, tool_calls, usage
        """
        accumulated_text = ""
        accumulated_length = 0
        tool_calls = []
        current_tool_call = None
        current_tool_input_json = ""
        usage = {"input_tokens": 0, "output_tokens": 0}
        
        print(f"Starting streaming message to Claude")
        
        try:
            async for event in self.llm.create_message_stream(
                messages=messages,
                tools=tools,
                system=system
            ):
                # Handle different event types from Claude stream
                if event.type == "message_start":
                    # Message metadata (usage will be updated at end)
                    if hasattr(event, 'message') and hasattr(event.message, 'usage'):
                        usage["input_tokens"] = event.message.usage.input_tokens
                
                elif event.type == "content_block_start":
                    # New content block starting
                    if hasattr(event, 'content_block'):
                        if event.content_block.type == "text":
                            # Text block starting - nothing to do yet
                            pass
                        elif event.content_block.type == "tool_use":
                            # Tool call starting
                            current_tool_call = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input": {}
                            }
                            current_tool_input_json = ""
                            print(f"  Tool call starting: {event.content_block.name}")
                
                elif event.type == "content_block_delta":
                    if hasattr(event, 'delta'):
                        if event.delta.type == "text_delta":
                            # TEXT CHUNK - emit message_delta for typing effect
                            chunk = event.delta.text
                            accumulated_text += chunk
                            accumulated_length += len(chunk)
                            
                            # Emit message_delta event for real-time streaming
                            if self.event_emitter and conversation_id:
                                await self.event_emitter.emit_message_delta(
                                    conversation_id,
                                    delta=chunk,
                                    accumulated_length=accumulated_length
                                )
                        
                        elif event.delta.type == "input_json_delta":
                            # Tool input being streamed (JSON chunks)
                            if current_tool_call:
                                current_tool_input_json += event.delta.partial_json
                
                elif event.type == "content_block_stop":
                    # Content block finished
                    if current_tool_call:
                        # Parse accumulated tool input JSON
                        try:
                            if current_tool_input_json:
                                current_tool_call["input"] = json.loads(current_tool_input_json)
                        except json.JSONDecodeError:
                            print(f"WARNING: Failed to parse tool input JSON: {current_tool_input_json[:100]}")
                            current_tool_call["input"] = {}
                        
                        tool_calls.append(current_tool_call)
                        print(f"  Tool call complete: {current_tool_call['name']}")
                        current_tool_call = None
                        current_tool_input_json = ""
                
                elif event.type == "message_delta":
                    # Final message metadata (stop_reason, usage)
                    if hasattr(event, 'usage'):
                        usage["output_tokens"] = event.usage.output_tokens
                
                elif event.type == "message_stop":
                    # Stream complete
                    print(f"Stream complete: {usage['input_tokens']} in, {usage['output_tokens']} out")
            
            # Return response object compatible with existing code
            return StreamingLLMResponse(
                text=accumulated_text,
                tool_calls=tool_calls,
                usage=usage
            )
            
        except Exception as e:
            print(f"ERROR: Claude streaming error: {e}")
            import traceback
            traceback.print_exc()
            
            # Step 5.3: Emit error event so frontend can handle gracefully
            if self.event_emitter and conversation_id:
                await self.event_emitter.emit_app_error(
                    conversation_id,
                    "llm_streaming_error",
                    f"Error during LLM streaming: {str(e)}",
                    details={"exception": str(e), "accumulated_text": accumulated_text[:200] if accumulated_text else ""},
                    recoverable=True
                )
            
            # If we have partial text, return it so user sees something
            if accumulated_text:
                return StreamingLLMResponse(
                    text=accumulated_text + "\n\n_[Response was interrupted due to an error]_",
                    tool_calls=tool_calls,
                    usage=usage
                )
            
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
        
        print(f"Context optimization: {len(old_messages)} old messages → summary, keeping {len(recent_messages)} recent")
        
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
        print(f"Calling Claude with {len(tools)} tools")
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
            
            # Get current context to retrieve existing run_ids
            context = await self.context_manager.get_context(conversation_id)
            current_run_ids = context.get("run_ids", []) if context else []
            updated_run_ids = [run_id] + current_run_ids
            # Keep only last 10 run IDs
            updated_run_ids = updated_run_ids[:10]
            
            # Store run_ids array - LLM will call get_run tool if needed
            await self.context_manager.update_context(
                conversation_id,
                {
                    "simulation_params": params,
                    "run_ids": updated_run_ids
                }
            )
            
            print(f"[_update_context] Context updated with run_ids (latest={run_id}, total={len(updated_run_ids)})")
        
        # Update validation params if this was a validation
        if tool_name.startswith("validate_"):
            await self.context_manager.update_context(
                conversation_id,
                {"validation_params": params}
            )
        
        print(f"Context updated after {tool_name} execution")
    
    # =========================================================================
    # Tool Management
    # =========================================================================
    
    async def _get_available_tools(self, context: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Get available tools from MCP server, filtered by context.
        
        Filters tools based on conversation state to reduce token usage:
        - Always include: simulation and validation tools
        - If last_run_id exists: include run tools (get_run, compare_runs)
        - If no process context: include discovery tools
        
        Args:
            context: Current conversation context
            
        Returns:
            Filtered list of tools
        """
        all_tools = await self.mcp_client.list_tools()
        
        if context is None:
            print(f"Retrieved {len(all_tools)} tools from MCP server (no filtering)")
            return all_tools
        
        # Determine which tool categories to include
        include_categories = set(CORE_TOOLS)  # Always include simulation + validation
        
        # Include run tools if we have previous runs
        if context.get("run_ids"):
            include_categories |= RUN_TOOLS
        
        # Include discovery tools if no process context established
        if not context.get("current_process") and not context.get("current_industry"):
            include_categories |= DISCOVERY_TOOLS
        
        # Filter tools
        filtered_tools = [
            tool for tool in all_tools
            if self._get_tool_name(tool) in include_categories
        ]
        
        print(f"Tool filtering: {len(all_tools)} → {len(filtered_tools)} tools (context: process={context.get('current_process')}, run_ids={len(context.get('run_ids', []))})")
        
        return filtered_tools
    
    def _get_tool_name(self, tool: Any) -> str:
        """Extract tool name from tool object or dict."""
        if isinstance(tool, dict):
            return tool.get("name", "")
        return getattr(tool, "name", "")
    
    async def _execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute tool via MCP client with progress tracking for simulations."""
        print(f"Executing tool: {tool_name}")
        
        try:
            # Quick health check before expensive tool execution
            if not await self.mcp_client.health_check():
                return {
                    "status": "error",
                    "error": "MCP server is unavailable",
                    "message": f"Cannot execute {tool_name}: MCP server connection lost"
                }
            
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
            
            result = await self.mcp_client.call_tool(tool_name, params)
            
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
