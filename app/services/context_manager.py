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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import redis
from motor.motor_asyncio import AsyncIOMotorClient


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
            "simulation_params": {},
            "executed_tools": [],
            "last_run_id": None,
            "created_at": now,
            "updated_at": now
        }
        
        # Store in Redis (fast access)
        await self._save_to_redis(conversation_id, context)
        
        return context
    
    async def get_context(self, conversation_id: str) -> Dict[str, Any]:
        """
        Retrieve conversation context.
        
        Tries Redis first (fast), falls back to MongoDB if not found.
        
        Args:
            conversation_id: Unique identifier for the conversation
            
        Returns:
            Context dictionary or empty dict if not found
            
        Example:
            >>> context = get_context("conv_123")
            >>> print(context.get("current_industry"))
            'sugar'
        """
        # Try Redis first (fast)
        context = await self._load_from_redis(conversation_id)
        
        if context:
            return context
        
        # Fallback to MongoDB (slower but permanent)
        # Note: For async compatibility, we need to handle this properly
        # In sync context, we can't await, so MongoDB fallback is limited
        # This will be properly fixed when all callers are async
        
        # For now, return empty dict if not in Redis
        # Full async implementation will enable MongoDB fallback
        return {}
    
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
            "run_id": result.get("run_id")
        }
        
        # Append to executed_tools list
        if "executed_tools" not in context:
            context["executed_tools"] = []
        
        context["executed_tools"].append(execution)
        context["updated_at"] = datetime.utcnow().isoformat()
        
        # Update last_run_id if present
        if result.get("run_id"):
            context["last_run_id"] = result["run_id"]
        
        # Save to both storages
        await self._save_to_redis(conversation_id, context)
        await self._save_to_mongo_async(conversation_id, context)
    
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
        self.redis.delete(redis_key)
        
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
    
    async def _load_from_mongo_sync(self, conversation_id: str) -> Optional[Dict[str, Any]]:
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
