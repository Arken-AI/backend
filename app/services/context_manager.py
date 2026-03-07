"""
Context Manager Service

Manages conversation state across multiple messages, storing context in both
Redis (fast, temporary) and MongoDB (persistent, long-term).

Key responsibilities:
- Track conversation state (industry, process, parameters)
- Remember tool executions (for policy enforcement)
- Manage validation status and age
- Store user preferences and session data
"""

import json
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import redis
from motor.motor_asyncio import AsyncIOMotorClient


# Message status constants
class MessageStatus:
    """Status values for conversation messages."""
    STREAMING = "streaming"  # Message is being generated
    COMPLETE = "complete"    # Message finished successfully
    ERROR = "error"          # Message generation failed
    CANCELLED = "cancelled"  # Message was cancelled by user



class ContextManager:
    """
    Manages conversation context with dual storage strategy:
    - Redis: Fast access, 1-hour TTL, primary storage
    - MongoDB: Permanent storage, backup, analytics
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        mongo_client: AsyncIOMotorClient,
        mongo_db_name: str = "arken_process_db",
        redis_ttl: int = 3600  # 1 hour
    ):
        """
        Initialize Context Manager.
        
        Args:
            redis_client: Redis client for fast temporary storage
            mongo_client: MongoDB client for permanent storage
            mongo_db_name: MongoDB database name
            redis_ttl: Time-to-live for Redis keys in seconds (default: 1 hour)
        """
        self.redis = redis_client
        self.mongo = mongo_client
        self.db = self.mongo[mongo_db_name]
        self.contexts_collection = self.db["conversations"]
        self.redis_ttl = redis_ttl
    
    def _redis_key(self, conversation_id: str) -> str:
        """Generate Redis key for conversation context."""
        return f"context:{conversation_id}"
    
    async def create_context(self, conversation_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a new conversation context.
        
        Args:
            conversation_id: Unique identifier for the conversation
            user_id: Optional user identifier
            
        Returns:
            Newly created context dictionary
            
        Example:
            >>> context = create_context("conv_123", "user_456")
            >>> print(context["conversation_id"])
            'conv_123'
        """
        now = datetime.utcnow().isoformat()
        
        context = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "current_industry": None,
            "current_process": None,
            "active_server": None,  # "process_server" or "calc_engine" - tracks which MCP server is active
            "current_mcp_server": None,  # Deprecated: use active_server instead
            "simulation_params": {},
            "executed_tools": [],
            "messages": [],  # Conversation history for multi-turn
            "run_ids": [],  # Legacy: flat array of run IDs (deprecated)
            "runs_by_server": {  # New: run IDs grouped by server
                "process_server": [],  # Sugar industry runs
                "calc_engine": []      # Dynamic flowsheet runs
            },
            "last_simulation_summary": None,  # Condensed simulation results for follow-up questions
            "created_at": now,
            "updated_at": now
        }
        
        # Store in Redis (fast access)
        await self._save_to_redis(conversation_id, context)
        
        return context
    
    async def get_context(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve conversation context.
        
        Tries Redis first (fast), falls back to MongoDB if not found.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Returns:
            Context dictionary or None if not found
            
        Example:
            >>> context = get_context("conv_123")
            >>> if context:
            ...     print(context.get("current_industry"))
            'sugar'
        """
        # Try Redis first (fast)
        context = await self._load_from_redis(conversation_id)
        
        if context:
            return context
        
        # Fallback to MongoDB (slower but permanent)
        context = await self._load_from_mongo_async(conversation_id)
        
        if context:
            # Re-cache in Redis for faster subsequent access
            await self._save_to_redis(conversation_id, context)
            return context
        
        return None
    
    async def update_context(
        self,
        conversation_id: str,
        updates: Dict[str, Any],
        create_if_missing: bool = True
    ) -> None:
        """
        Update specific fields in conversation context.
        
        Args:
            conversation_id: Unique identifier for the conversation
            updates: Dictionary of fields to update
            create_if_missing: Create new context if not found (default: True)
            
        Example:
            >>> await update_context("conv_123", {
            ...     "current_industry": "sugar",
            ...     "current_process": "sugar_production"
            ... })
        """
        # Get existing context
        context = await self.get_context(conversation_id)
        
        if not context and create_if_missing:
            context = await self.create_context(conversation_id)
        elif not context:
            raise ValueError(f"Context not found: {conversation_id}")
        
        # Update fields
        context.update(updates)
        context["updated_at"] = datetime.utcnow().isoformat()
        
        # Save to both storages
        await self._save_to_redis(conversation_id, context)
        await self._save_to_mongo_async(conversation_id, context)
    
    async def update_simulation_summary(
        self,
        conversation_id: str,
        tool_name: str,
        result: Dict[str, Any],
        params: Dict[str, Any]
    ) -> None:
        """
        Extract and store a condensed simulation summary from a tool result.

        This populates ``last_simulation_summary`` in the context so that the
        LLM can compare successive runs without an extra ``get_run`` call.

        Handles both process-server results (top-level dict) and calc-engine
        results (nested under ``results``).
        """
        try:
            status = result.get("status", "unknown")
            sim_status = result.get("simulation_status", status)
            run_id = result.get("calc_run_id") or result.get("run_id")
            process_id = result.get("process_id") or params.get("process_id")

            # ── Determine the payload that contains actual outputs ───────
            # Calc-engine wraps outputs under result["results"];
            # process-server returns them at the top level.
            outputs = result.get("results") or result

            # ── Extract KPIs ─────────────────────────────────────────────
            # Try common locations; each server stores them differently.
            kpis = {}

            # 1. Explicit "kpis" / "summary_kpis" block
            if isinstance(outputs.get("kpis"), dict):
                kpis = outputs["kpis"]
            elif isinstance(outputs.get("summary_kpis"), dict):
                kpis = outputs["summary_kpis"]

            # 2. Walk node_results to collect per-equipment KPIs
            node_results = outputs.get("node_results")
            if isinstance(node_results, dict):
                equip_kpis = []
                for eq_id, eq_data in node_results.items():
                    if not isinstance(eq_data, dict):
                        continue
                    entry = {"equipment_id": eq_id}
                    for field in ("duty", "duty_kW", "efficiency", "separation_quality",
                                  "equipment_type", "type"):
                        if field in eq_data:
                            entry[field] = eq_data[field]
                    if len(entry) > 1:          # has at least one KPI
                        equip_kpis.append(entry)
                if equip_kpis:
                    kpis["equipment_kpis"] = equip_kpis[:8]  # limit for token efficiency

            # 3. Top-level sugar-industry metrics
            for metric in ("overall_recovery", "extraction_efficiency",
                           "juice_extraction_efficiency", "throughput_tcd",
                           "energy_consumption_gj_per_ton", "pol_in_bagasse",
                           "fibre_percent", "mixed_juice_brix", "mixed_juice_purity"):
                if metric in outputs and metric not in kpis:
                    kpis[metric] = outputs[metric]

            # ── Mass / energy balance closures ───────────────────────────
            mass_balance = (
                outputs.get("mass_balance_closure")
                or outputs.get("mass_balance", {}).get("closure")
            )
            energy_balance = (
                outputs.get("energy_balance_closure")
                or outputs.get("energy_balance", {}).get("closure")
            )

            # ── Convergence info ─────────────────────────────────────────
            converged = sim_status not in ("not_converged", "error", "failed",
                                           "simulation_failed")

            # ── Validation flag counts ───────────────────────────────────
            flag_counts = {"critical": 0, "warning": 0, "info": 0}
            validation = result.get("validation")
            if isinstance(validation, dict):
                for flags in validation.values():
                    if isinstance(flags, list):
                        for f in flags:
                            sev = (f.get("severity") or "").lower()
                            if sev in flag_counts:
                                flag_counts[sev] += 1
            elif isinstance(validation, list):
                for f in validation:
                    sev = (f.get("severity") or "").lower()
                    if sev in flag_counts:
                        flag_counts[sev] += 1

            summary = {
                "run_id": run_id,
                "process_id": process_id,
                "tool": tool_name,
                "converged": converged,
                "simulation_status": sim_status,
                "kpis": kpis if kpis else None,
                "mass_balance_closure": mass_balance,
                "energy_balance_closure": energy_balance,
                "validation_flag_count": flag_counts,
                "execution_time_ms": result.get("execution_time_ms"),
                "timestamp": datetime.utcnow().isoformat(),
            }

            await self.update_context(
                conversation_id,
                {"last_simulation_summary": summary}
            )
        except Exception:
            # Never let summary extraction break the main flow
            import traceback as _tb
            _tb.print_exc()

    async def update_mcp_server(
        self,
        conversation_id: str,
        server: str
    ) -> None:
        """
        Update the active MCP server for a conversation.
        
        Supports multiple servers:
        - 'process_server': MCP Process Server (sugar industry)
        - 'calc_engine': MCP Calculation Engine Server (dynamic flowsheets)
        
        Args:
            conversation_id: Unique identifier for the conversation
            server: Server identifier ("process_server" or "calc_engine")
            
        Raises:
            ValueError: If server is not recognized
            
        Example:
            >>> await update_mcp_server("conv_123", "calc_engine")
        """
        valid_servers = {"process_server", "calc_engine", "process"}  # "process" for legacy
        
        if server not in valid_servers:
            raise ValueError(f"Invalid MCP server: {server}. Must be one of: {valid_servers}")
        
        # Normalize legacy "process" to "process_server"
        normalized_server = "process_server" if server == "process" else server
        
        await self.update_context(
            conversation_id,
            {
                "active_server": normalized_server,
                "current_mcp_server": server  # Keep for backward compatibility
            }
        )
    
    async def get_mcp_server(self, conversation_id: str) -> Optional[str]:
        """
        Get the current MCP server for a conversation.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Returns:
            Current MCP server ("process") or None if not set
        """
        context = await self.get_context(conversation_id)
        return context.get("current_mcp_server") if context else None
    
    async def add_tool_execution(
        self,
        conversation_id: str,
        tool_name: str,
        result: Dict[str, Any]
    ) -> None:
        """
        Add tool execution to conversation history.
        
        This is used by Policy Engine to check prerequisites and validation status.
        
        Args:
            conversation_id: Unique identifier for the conversation
            tool_name: Name of the executed tool
            result: Tool execution result (status, data, etc.)
            
        Example:
            >>> await add_tool_execution("conv_123", "validate_process_inputs", {
            ...     "status": "success",
            ...     "process_id": "sugar_production",
            ...     "valid": True
            ... })
        """
        context = await self.get_context(conversation_id)
        
        if not context:
            context = await self.create_context(conversation_id, user_id="system")
        
        # Create execution record
        execution = {
            "tool_name": tool_name,
            "timestamp": datetime.utcnow().isoformat(),
            "status": result.get("status", "unknown"),
            "process_id": result.get("process_id"),
            "valid": result.get("valid"),
            "run_id": result.get("run_id"),
            "server": result.get("server")  # Track which server executed this tool
        }
        
        # Append to executed_tools list
        if "executed_tools" not in context:
            context["executed_tools"] = []
        
        context["executed_tools"].append(execution)
        context["updated_at"] = datetime.utcnow().isoformat()
        
        # Update last_run_id if present (check both calc_run_id and run_id)
        run_id = result.get("run_id")
        if run_id:
            context["last_run_id"] = run_id
            
            # Also store in runs_by_server for organized tracking
            server_name = self._get_server_from_result(result, tool_name)
            if "runs_by_server" not in context:
                context["runs_by_server"] = {"process_server": [], "calc_engine": []}
            
            if server_name in context["runs_by_server"]:
                # Add to front (newest first), limit to 10 per server
                runs = context["runs_by_server"][server_name]
                if run_id not in runs:  # Avoid duplicates
                    runs.insert(0, run_id)
                    context["runs_by_server"][server_name] = runs[:10]
            
            # Also update legacy run_ids for backward compatibility
            if "run_ids" not in context:
                context["run_ids"] = []
            if run_id not in context["run_ids"]:
                context["run_ids"].insert(0, run_id)
                context["run_ids"] = context["run_ids"][:10]
        
        # Save to both storages
        await self._save_to_redis(conversation_id, context)
        await self._save_to_mongo_async(conversation_id, context)
    
    def _get_server_from_result(self, result: Dict[str, Any], tool_name: str) -> str:
        """
        Determine which server a result came from.
        
        Args:
            result: Tool execution result
            tool_name: Name of the tool that was executed
            
        Returns:
            Server name ("process_server" or "calc_engine")
        """
        # Check if server field is in result
        server = result.get("server", "")
        if "calculation-engine" in server or "calc" in server.lower():
            return "calc_engine"
        if "process" in server.lower():
            return "process_server"
        
        # Fallback: determine from tool name
        calc_engine_tools = {
            "calc_simulate_process", "calc_list_processes", "calc_get_process",
            "calc_get_run", "calc_list_runs", "get_editable_parameters",
            "validate_parameters", "get_user_parameters",
            "switch_parameter_version", "compare_parameters"
        }
        
        if tool_name in calc_engine_tools or tool_name.startswith("calc_"):
            return "calc_engine"
        
        return "process_server"
    
    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "complete"
    ) -> str:
        """
        Add a message to conversation history for multi-turn support.
        
        This enables the LLM to remember previous user questions and its own responses.
        
        Args:
            conversation_id: Unique identifier for the conversation
            role: Message role ("user" or "assistant")
            content: Message content
            metadata: Optional metadata (tool_calls, token_usage, etc.)
            status: Message status (streaming, complete, error, cancelled)
            
        Returns:
            message_id: Unique identifier for the created message
            
        Example:
            >>> msg_id = await add_message("conv_123", "user", "Simulate sugar factory")
            >>> msg_id = await add_message("conv_123", "assistant", "", status="streaming")
        """
        context = await self.get_context(conversation_id)
        
        if not context:
            context = await self.create_context(conversation_id, user_id="system")
        
        # Ensure messages list exists
        if "messages" not in context:
            context["messages"] = []
        
        # Generate unique message ID
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        
        # Create message record with message_id and status
        message = {
            "message_id": message_id,
            "role": role,
            "content": content,
            "status": status,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if metadata:
            message["metadata"] = metadata
        
        context["messages"].append(message)
        context["updated_at"] = datetime.utcnow().isoformat()
        
        # Save to both storages
        await self._save_to_redis(conversation_id, context)
        await self._save_to_mongo_async(conversation_id, context)
        
        return message_id
    
    async def update_message(
        self,
        conversation_id: str,
        message_id: str,
        content: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update an existing message by message_id.
        
        Used for:
        - Updating streaming message with final content
        - Marking message as complete/error/cancelled
        - Adding metadata after message creation
        
        Args:
            conversation_id: Unique identifier for the conversation
            message_id: Unique identifier for the message to update
            content: New content (optional, keeps existing if not provided)
            status: New status (optional, keeps existing if not provided)
            metadata: Metadata to merge (optional)
            
        Returns:
            True if message was found and updated, False otherwise
            
        Example:
            >>> await update_message("conv_123", "msg_abc123", 
            ...     content="Full response text", 
            ...     status="complete")
        """
        context = await self.get_context(conversation_id)
        
        if not context or "messages" not in context:
            return False
        
        # Find the message by message_id
        message_found = False
        for message in context["messages"]:
            if message.get("message_id") == message_id:
                # Update fields if provided
                if content is not None:
                    message["content"] = content
                if status is not None:
                    message["status"] = status
                if metadata is not None:
                    # Merge metadata
                    existing_metadata = message.get("metadata", {})
                    existing_metadata.update(metadata)
                    message["metadata"] = existing_metadata
                
                message["updated_at"] = datetime.utcnow().isoformat()
                message_found = True
                break
        
        if not message_found:
            return False
        
        context["updated_at"] = datetime.utcnow().isoformat()
        
        # Save to both storages
        await self._save_to_redis(conversation_id, context)
        await self._save_to_mongo_async(conversation_id, context)
        
        return True

    async def get_messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        """
        Get conversation message history.
        
        Returns messages in chronological order for building LLM context.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Returns:
            List of messages with role, content, and timestamp
            
        Example:
            >>> messages = await get_messages("conv_123")
            >>> for msg in messages:
            ...     print(f"{msg['role']}: {msg['content'][:50]}...")
        """
        context = await self.get_context(conversation_id)
        
        if not context or "messages" not in context:
            return []
        
        return context["messages"]
    
    async def get_executed_tools(self, conversation_id: str) -> List[str]:
        """
        Get list of tool names executed in this conversation.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Returns:
            List of tool names (e.g., ['validate_process_inputs', 'simulate_process'])
            
        Example:
            >>> tools = await get_executed_tools("conv_123")
            >>> print("validate_process_inputs" in tools)
            True
        """
        context = await self.get_context(conversation_id)
        
        if not context or "executed_tools" not in context:
            return []
        
        return [exec_record["tool_name"] for exec_record in context["executed_tools"]]
    
    async def get_last_validation(
        self,
        conversation_id: str,
        process_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent validation for a process.
        
        Used by Policy Engine to check if validation is still valid (< 10 min old).
        
        Args:
            conversation_id: Unique identifier for the conversation
            process_id: Optional process ID to filter by
            
        Returns:
            Validation record with timestamp, status, and result
            None if no validation found
            
        Example:
            >>> validation = await get_last_validation("conv_123", "sugar_production")
            >>> if validation:
            ...     print(f"Valid: {validation['valid']}")
            ...     print(f"Age: {datetime.now() - validation['timestamp']}")
        """
        context = await self.get_context(conversation_id)
        
        if not context or "executed_tools" not in context:
            return None
        
        # Find validation tools (validate_process_inputs, validate_equipment_inputs)
        validation_tools = [
            exec_record for exec_record in context["executed_tools"]
            if "validate" in exec_record["tool_name"]
        ]
        
        if not validation_tools:
            return None
        
        # Filter by process_id if provided
        if process_id:
            validation_tools = [
                v for v in validation_tools
                if v.get("process_id") == process_id
            ]
        
        if not validation_tools:
            return None
        
        # Return the most recent validation
        latest = validation_tools[-1]
        
        return {
            "tool_name": latest["tool_name"],
            "timestamp": datetime.fromisoformat(latest["timestamp"]),
            "status": latest["status"],
            "valid": latest.get("valid", False),
            "process_id": latest.get("process_id")
        }
    
    async def get_run_ids_by_server(
        self, 
        conversation_id: str, 
        server: Optional[str] = None
    ) -> Dict[str, List[str]]:
        """
        Get run IDs from the conversation, optionally filtered by server.
        
        Args:
            conversation_id: Unique identifier for the conversation
            server: Optional server filter ("process" or "calc_engine")
                   If None, returns all runs grouped by server
        
        Returns:
            Dictionary with server keys and lists of run_ids:
            - If server is None: {"process": [...], "calc_engine": [...]}
            - If server specified: {server: [...]}
            
        Example:
            >>> runs = await get_run_ids_by_server("conv_123")
            {"process": ["run_001"], "calc_engine": ["run_002", "run_003"]}
            
            >>> runs = await get_run_ids_by_server("conv_123", server="calc_engine")
            {"calc_engine": ["run_002", "run_003"]}
        """
        context = await self.get_context(conversation_id)
        if not context:
            return {"process": [], "calc_engine": []}
        
        run_ids_by_server = context.get("run_ids_by_server", {
            "process": [],
            "calc_engine": []
        })
        
        if server:
            if server not in ["process", "calc_engine"]:
                return {"process": [], "calc_engine": []}
            return {server: run_ids_by_server.get(server, [])}
        
        return run_ids_by_server
    
    async def get_active_server(self, conversation_id: str) -> Optional[str]:
        """
        Get the currently active MCP server for the conversation.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Returns:
            Server name ("process" or "calc_engine") or None if not set
        """
        context = await self.get_context(conversation_id)
        if not context:
            return None
        
        return context.get("active_server")
    
    async def clear_context(self, conversation_id: str) -> None:
        """
        Clear conversation context from both Redis and MongoDB.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Example:
            >>> await clear_context("conv_123")
        """
        # Remove from Redis
        redis_key = self._redis_key(conversation_id)
        try:
            await self.redis.delete(redis_key)
        except Exception as e:
            print(f"Warning: Failed to delete context from Redis: {e}")
        
        # Remove from MongoDB
        try:
            await self.contexts_collection.delete_one(
                {"conversation_id": conversation_id}
            )
        except Exception as e:
            print(f"Warning: Failed to delete context from MongoDB: {e}")
    
    # Private helper methods
    
    async def _save_to_redis(self, conversation_id: str, context: Dict[str, Any]) -> None:
        """Save context to Redis with TTL."""
        redis_key = self._redis_key(conversation_id)
        context_json = json.dumps(context)
        await self.redis.setex(redis_key, self.redis_ttl, context_json)
    
    async def _load_from_redis(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Load context from Redis."""
        redis_key = self._redis_key(conversation_id)
        context_json = await self.redis.get(redis_key)
        
        if context_json:
            return json.loads(context_json)
        
        return None
    
    async def _save_to_mongo_async(self, conversation_id: str, context: Dict[str, Any]) -> None:
        """
        Save context to MongoDB (async operation).
        
        Updates existing conversation or creates new one (upsert).
        """
        try:
            # Create a copy and remove session_id if it's None to avoid duplicate key error
            mongo_context = {k: v for k, v in context.items() if not (k == "session_id" and v is None)}
            
            await self.contexts_collection.update_one(
                {"conversation_id": conversation_id},
                {"$set": mongo_context},
                upsert=True
            )
        except Exception as e:
            # Log error but don't fail - Redis is primary storage
            print(f"Warning: Failed to save context to MongoDB: {e}")
    
    async def _load_from_mongo_async(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        Load context from MongoDB (async operation).
        
        Returns:
            Context dictionary or None if not found
        """
        try:
            context = await self.contexts_collection.find_one(
                {"conversation_id": conversation_id}
            )
            
            if context:
                # Remove MongoDB's _id field before returning
                context.pop("_id", None)
                return context
            
            return None
        except Exception as e:
            # Log error and return None - Redis will be tried first anyway
            print(f"Warning: Failed to load context from MongoDB: {e}")
            return None


# Example usage and testing
if __name__ == "__main__":
    # This section demonstrates how to use Context Manager
    
    # Setup (normally done in FastAPI dependency injection)
    import redis
    from motor.motor_asyncio import AsyncIOMotorClient
    
    redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    mongo_client = AsyncIOMotorClient("mongodb://localhost:27017")
    
    # Create Context Manager
    cm = ContextManager(redis_client, mongo_client)
    
    # Example 1: Create new conversation
    print("=== Example 1: Create Context ===")
    context = cm.create_context("conv_demo_123", user_id="user_456")
    print(f"Created: {context['conversation_id']}")
    print(f"Timestamp: {context['created_at']}")
    
    # Example 2: Update context with industry/process
    print("\n=== Example 2: Update Context ===")
    cm.update_context("conv_demo_123", {
        "current_industry": "sugar",
        "current_process": "sugar_production"
    })
    context = cm.get_context("conv_demo_123")
    print(f"Industry: {context['current_industry']}")
    print(f"Process: {context['current_process']}")
    
    # Example 3: Track tool execution
    print("\n=== Example 3: Add Tool Execution ===")
    cm.add_tool_execution("conv_demo_123", "validate_process_inputs", {
        "status": "success",
        "process_id": "sugar_production",
        "valid": True
    })
    
    tools = cm.get_executed_tools("conv_demo_123")
    print(f"Executed tools: {tools}")
    
    # Example 4: Check validation
    print("\n=== Example 4: Check Validation ===")
    validation = cm.get_last_validation("conv_demo_123", "sugar_production")
    if validation:
        print(f"Validation found: {validation['tool_name']}")
        print(f"Status: {validation['status']}")
        print(f"Valid: {validation['valid']}")
        print(f"Timestamp: {validation['timestamp']}")
        
        # Check age
        age = datetime.utcnow() - validation['timestamp']
        print(f"Age: {age.total_seconds()} seconds")
        print(f"Still valid (< 10 min): {age.total_seconds() < 600}")
    
    # Example 5: Multi-turn simulation
    print("\n=== Example 5: Multi-Turn Conversation ===")
    
    # Turn 1: Validate
    cm.add_tool_execution("conv_demo_123", "validate_process_inputs", {
        "status": "success",
        "process_id": "sugar_production",
        "valid": True
    })
    print("Turn 1: Validated ✓")
    
    # Turn 2: Simulate (check if validation exists)
    tools = cm.get_executed_tools("conv_demo_123")
    if "validate_process_inputs" in tools:
        cm.add_tool_execution("conv_demo_123", "simulate_process", {
            "status": "success",
            "process_id": "sugar_production",
            "run_id": "run_demo_789"
        })
        print("Turn 2: Simulated ✓")
        
        context = cm.get_context("conv_demo_123")
        print(f"Last run ID: {context['last_run_id']}")
    
    # Cleanup
    print("\n=== Cleanup ===")
    cm.clear_context("conv_demo_123")
    print("Context cleared")
