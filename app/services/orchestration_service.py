"""
Orchestration Service

Central coordinator that connects all Phase 3 components:
- Context Manager: Track conversation state
- Tool Registry: Get available tools
- Policy Engine: Enforce business rules
- LLM Provider: Get tool decisions from Claude/Gemini
- MCP Client: Execute tools

Flow:
1. User message → Load context
2. Get tools from registry
3. Send to LLM with tools
4. LLM decides which tool to call
5. Check policy rules
6. Execute tool if allowed
7. Update context
8. Return response
"""

import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from app.services.context_manager import ContextManager
from app.services.tool_registry import ToolRegistry
from app.core.policy_engine import PolicyEngine, PolicyDecision
from app.core.llm_provider import ClaudeProvider
from app.core.llm_gemini_provider import GeminiProvider
from app.core.mcp_client import MCPClient, MCPServerConfig
from app.config import settings

logger = logging.getLogger(__name__)


class OrchestrationService:
    """
    Central orchestrator for chat-based process simulation.
    
    Coordinates between:
    - Context Manager (conversation state)
    - Tool Registry (available tools)
    - Policy Engine (business rules)
    - LLM Provider (Claude/Gemini)
    - MCP Client (tool execution)
    """
    
    def __init__(
        self,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        policy_engine: PolicyEngine,
        mcp_client: MCPClient,
        llm_provider: str = None
    ):
        """
        Initialize orchestration service.
        
        Args:
            context_manager: Context tracking service
            tool_registry: Tool metadata registry
            policy_engine: Policy enforcement engine
            mcp_client: MCP client for tool execution
            llm_provider: "claude" or "gemini" (defaults to settings.default_llm_provider)
        """
        self.context_manager = context_manager
        self.tool_registry = tool_registry
        self.policy_engine = policy_engine
        self.mcp_client = mcp_client
        
        # Initialize LLM provider
        self.llm_provider_name = llm_provider or settings.default_llm_provider
        if self.llm_provider_name == "claude":
            self.llm = ClaudeProvider()
        elif self.llm_provider_name == "gemini":
            self.llm = GeminiProvider()
        else:
            raise ValueError(f"Unknown LLM provider: {self.llm_provider_name}")
        
        logger.info(f"Orchestration service initialized with {self.llm_provider_name}")
    
    async def process_message(
        self,
        conversation_id: str,
        user_message: str,
        user_id: str = "default_user"
    ) -> Dict[str, Any]:
        """
        Main entry point: Process user message and return response.
        
        Flow:
        1. Load/create conversation context
        2. Get available tools
        3. Send message + tools to LLM
        4. If LLM wants to call a tool:
           a. Check policy rules
           b. If ALLOWED: Execute tool, return result to user
           c. If DENIED and tool is simulation: Give LLM ONE chance to fix (e.g., validate first)
           d. If still fails or non-simulation: Return error to user
        5. Return response to user
        
        Args:
            conversation_id: Unique conversation identifier
            user_message: User's message
            user_id: User identifier
            
        Returns:
            {
                "status": "success" | "error",
                "message": "LLM response text",
                "tool_calls": [...],  # Tools that were called
                "policy_violations": [...],  # If any tools were denied
                "conversation_id": "...",
                "context": {...}  # Current conversation context
            }
        """
        try:
            logger.info(f"Processing message for conversation {conversation_id}")
            
            # Step 1: Load or create context
            context = await self._get_or_create_context(conversation_id, user_id)
            
            # Step 2: Get available tools
            tools = await self._get_available_tools()
            
            # Track all tool calls and policy violations
            all_tool_results = []
            all_policy_violations = []
            
            # Step 3: First LLM call - handle user's request
            logger.info("LLM call 1: Processing user request")
            llm_response = await self._call_llm(user_message, tools, context)
            
            # Check if LLM wants to call tools
            # Handle both object responses (ParsedResponse/GeminiParsedResponse) and dict responses (mocks)
            if hasattr(llm_response, 'has_tool_calls'):
                has_tools = llm_response.has_tool_calls
                tool_calls_list = llm_response.tool_calls if has_tools else []
                message_text = llm_response.text if hasattr(llm_response, 'text') else ""
            else:
                # Dict response (mock or legacy)
                has_tools = bool(llm_response.get("tool_calls"))
                tool_calls_list = llm_response.get("tool_calls", [])
                message_text = llm_response.get("message", "")
            
            if not has_tools:
                # LLM returned text response, no tools needed
                return {
                    "status": "success",
                    "message": message_text,
                    "tool_calls": [],
                    "policy_violations": [],
                    "conversation_id": conversation_id,
                    "context": await self.context_manager.get_context(conversation_id)
                }
            
            # Process tool calls from first LLM call
            simulation_denied = False
            denied_simulation_tool = None
            denied_simulation_params = None
            
            for tool_call in tool_calls_list:
                # Handle both object format (from real providers) and dict format (from mocks)
                if isinstance(tool_call, dict):
                    tool_name = tool_call.get("name")
                    tool_params = tool_call.get("arguments", tool_call.get("input", {}))
                else:
                    # Assuming it's an object with attributes
                    tool_name = tool_call.name
                    tool_params = tool_call.input if hasattr(tool_call, 'input') else tool_call.arguments
                
                logger.info(f"LLM wants to call: {tool_name}")
                
                # Check policy
                policy_result = await self._enforce_policy(tool_name, context, tool_params)
                
                if policy_result["decision"] == "allow":
                    # Execute tool
                    logger.info(f"Policy ALLOWED: {tool_name}")
                    result = await self._execute_tool(tool_name, tool_params)
                    
                    # Update context
                    await self._update_context(
                        conversation_id,
                        tool_name,
                        result,
                        tool_params
                    )
                    
                    all_tool_results.append({
                        "tool": tool_name,
                        "status": "executed",
                        "result": result
                    })
                    
                else:
                    # Policy denied
                    logger.warning(f"Policy DENIED: {tool_name} - {policy_result['reason']}")
                    
                    all_policy_violations.append({
                        "tool": tool_name,
                        "reason": policy_result["reason"],
                        "decision": policy_result["decision"]
                    })
                    
                    # Check if this is a simulation tool that was denied
                    if tool_name in ["simulate_process", "simulate_equipment"]:
                        simulation_denied = True
                        denied_simulation_tool = tool_name
                        denied_simulation_params = tool_params
            
            # Step 4: Handle results from first call
            
            # Case 1: All tools executed successfully - return results to user
            if all_tool_results and not all_policy_violations:
                logger.info("All tools executed successfully, returning to user")
                
                # Format results for user
                results_summary = self._format_results_for_user(all_tool_results)
                
                return {
                    "status": "success",
                    "message": results_summary,
                    "tool_calls": all_tool_results,
                    "policy_violations": [],
                    "conversation_id": conversation_id,
                    "context": await self.context_manager.get_context(conversation_id)
                }
            
            # Case 2: Simulation was denied - give LLM ONE chance to fix it
            if simulation_denied:
                logger.info("Simulation denied, giving LLM one chance to fix (e.g., validate first)")
                
                # Build feedback for LLM
                feedback = self._format_policy_violations_for_llm(all_policy_violations)
                recovery_message = (
                    f"Policy denied:\n{feedback}\n\n"
                    f"Original user request: {user_message}"
                )
                
                # Step 5: Second LLM call - let it try to fix the issue
                logger.info("LLM call 2: Attempting recovery")
                context = await self.context_manager.get_context(conversation_id)  # Reload context
                recovery_response = await self._call_llm(recovery_message, tools, context)
                
                # Handle response format (object or dict)
                if hasattr(recovery_response, 'has_tool_calls'):
                    recovery_has_tools = recovery_response.has_tool_calls
                    recovery_tool_calls = recovery_response.tool_calls if recovery_has_tools else []
                else:
                    recovery_has_tools = bool(recovery_response.get("tool_calls"))
                    recovery_tool_calls = recovery_response.get("tool_calls", [])
                
                # Check if LLM wants to call tools for recovery
                if recovery_has_tools:
                    for tool_call in recovery_tool_calls:
                        # Handle both formats
                        if isinstance(tool_call, dict):
                            tool_name = tool_call.get("name")
                            tool_params = tool_call.get("arguments", tool_call.get("input", {}))
                        else:
                            tool_name = tool_call.name
                            tool_params = tool_call.input if hasattr(tool_call, 'input') else tool_call.arguments
                        
                        logger.info(f"LLM recovery attempt: {tool_name}")
                        
                        # Reload context (may have been updated)
                        context = await self.context_manager.get_context(conversation_id)
                        
                        # Check policy for recovery tool
                        policy_result = await self._enforce_policy(tool_name, context, tool_params)
                        
                        if policy_result["decision"] == "allow":
                            # Execute recovery tool (e.g., validation)
                            logger.info(f"Recovery tool ALLOWED: {tool_name}")
                            result = await self._execute_tool(tool_name, tool_params)
                            
                            # Update context
                            await self._update_context(
                                conversation_id,
                                tool_name,
                                result,
                                tool_params
                            )
                            
                            all_tool_results.append({
                                "tool": tool_name,
                                "status": "executed",
                                "result": result
                            })
                            
                            # Check if recovery tool (validation) succeeded
                            recovery_status = result.get("status", "unknown")
                            
                            if recovery_status == "success":
                                # Validation succeeded! Now try the original simulation
                                logger.info(f"Recovery successful, now attempting original simulation: {denied_simulation_tool}")
                                
                                # Reload context with fresh validation
                                context = await self.context_manager.get_context(conversation_id)
                                
                                # Re-check policy for simulation (should ALLOW now)
                                sim_policy_result = await self._enforce_policy(
                                    denied_simulation_tool, 
                                    context, 
                                    denied_simulation_params
                                )
                                
                                if sim_policy_result["decision"] == "allow":
                                    # Execute the original simulation
                                    logger.info(f"Simulation now ALLOWED: {denied_simulation_tool}")
                                    sim_result = await self._execute_tool(
                                        denied_simulation_tool, 
                                        denied_simulation_params
                                    )
                                    
                                    # Update context
                                    await self._update_context(
                                        conversation_id,
                                        denied_simulation_tool,
                                        sim_result,
                                        denied_simulation_params
                                    )
                                    
                                    all_tool_results.append({
                                        "tool": denied_simulation_tool,
                                        "status": "executed",
                                        "result": sim_result
                                    })
                                    
                                    logger.info("Validation + Simulation both successful")
                                else:
                                    # Simulation still denied (shouldn't happen, but handle it)
                                    logger.warning(f"Simulation still denied after validation: {sim_policy_result['reason']}")
                                    all_policy_violations.append({
                                        "tool": denied_simulation_tool,
                                        "reason": sim_policy_result["reason"],
                                        "decision": sim_policy_result["decision"]
                                    })
                            else:
                                # Validation failed - don't try to simulate
                                logger.warning(f"Recovery tool failed: {result}")
                            
                        else:
                            # Recovery tool also denied
                            logger.warning(f"Recovery tool DENIED: {tool_name}")
                            all_policy_violations.append({
                                "tool": tool_name,
                                "reason": policy_result["reason"],
                                "decision": policy_result["decision"]
                            })
                
                # Return results to user
                if all_tool_results:
                    # Tools executed (validation and/or simulation)
                    results_summary = self._format_results_for_user(all_tool_results)
                    
                    return {
                        "status": "success",
                        "message": results_summary,
                        "tool_calls": all_tool_results,
                        "policy_violations": all_policy_violations,
                        "conversation_id": conversation_id,
                        "context": await self.context_manager.get_context(conversation_id)
                    }
                else:
                    # Recovery failed, return error to user
                    error_message = self._format_policy_violation_message(all_policy_violations)
                    
                    return {
                        "status": "error",
                        "message": error_message,
                        "tool_calls": [],
                        "policy_violations": all_policy_violations,
                        "conversation_id": conversation_id,
                        "context": await self.context_manager.get_context(conversation_id)
                    }
            
            # Case 3: Non-simulation tools denied - return error to user directly
            logger.info("Non-simulation tools denied, returning error to user")
            error_message = self._format_policy_violation_message(all_policy_violations)
            
            return {
                "status": "error",
                "message": error_message,
                "tool_calls": all_tool_results,
                "policy_violations": all_policy_violations,
                "conversation_id": conversation_id,
                "context": await self.context_manager.get_context(conversation_id)
            }
            
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Error processing your request: {str(e)}",
                "conversation_id": conversation_id
            }
    
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
    
    async def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get all available tools from MCP server."""
        tools = await self.mcp_client.list_tools()
        logger.info(f"Retrieved {len(tools)} tools from MCP server")
        return tools
    
    async def _call_llm(
        self,
        user_message: str,
        tools: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send message to LLM with available tools.
        
        Returns:
            {
                "message": "LLM response",
                "tool_calls": [{"name": "...", "arguments": {...}}]  # Optional
            }
        """
        # Build conversation history
        messages = self._build_message_history(user_message, context)
        
        # Call LLM
        logger.info(f"Calling {self.llm_provider_name} with {len(tools)} tools")
        response = await self.llm.create_message(messages, tools)
        
        return response
    
    def _build_message_history(
        self,
        user_message: str,
        context: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """
        Build message history for LLM.
        
        For now, just send current message.
        Future: Include conversation history from context.
        """
        messages = [{"role": "user", "content": user_message}]
        
        # Add context information to system message if available
        if context.get("current_industry") or context.get("current_process"):
            context_info = []
            if context.get("current_industry"):
                context_info.append(f"Industry: {context['current_industry']}")
            if context.get("current_process"):
                context_info.append(f"Process: {context['current_process']}")
            
            # Prepend system message with context
            system_msg = "Context: " + ", ".join(context_info)
            messages.insert(0, {"role": "system", "content": system_msg})
        
        return messages
    
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
                "decision": "ALLOW" | "DENY",
                "reason": "...",
                "metadata": {...}
            }
        """
        result = self.policy_engine.enforce_policy(tool_name, context, params)
        
        return {
            "decision": result.decision.value,
            "reason": result.reason,
            "metadata": result.metadata
        }
    
    async def _execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute tool via MCP client."""
        logger.info(f"Executing tool: {tool_name}")
        result = await self.mcp_client.call_tool(tool_name, params)
        return result
    
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
    
    async def _get_final_response(
        self,
        user_message: str,
        tool_results: List[Dict[str, Any]],
        policy_violations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        DEPRECATED: This method is no longer used.
        The new process_message loop sends feedback directly back to LLM.
        Kept for backward compatibility.
        """
        result_summary = self._format_tool_results(tool_results)
        
        follow_up_message = (
            f"Tool execution results:\n{result_summary}\n\n"
            f"Original question: {user_message}\n\n"
            f"Please provide a final response to the user based on these results."
        )
        
        messages = [{"role": "user", "content": follow_up_message}]
        response = await self.llm.send_message(messages, tools=[])
        
        return response
    
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
        
        # Simple summary of what was executed
        tool_names = [r["tool"] for r in tool_results]
        
        if len(tool_names) == 1:
            tool_name = tool_names[0]
            result = tool_results[0]["result"]
            
            # Check if tool execution was successful
            status = result.get("status", "unknown")
            
            if status == "success":
                return f"Successfully executed {tool_name}. Results available in context."
            else:
                error_msg = result.get("error") or result.get("message", "Unknown error")
                return f"Tool {tool_name} failed: {error_msg}"
        else:
            # Multiple tools executed
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
