"""
Policy Engine

Enforces business rules and constraints for tool execution.
Prevents invalid workflows and ensures data integrity.

Key Rules:
- Validate before simulate
- Validation expires after 10 minutes
- Parameters changed = must re-validate
- Max 10 tools per conversation
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum


class PolicyDecision(Enum):
    """Policy enforcement decision"""
    ALLOW = "allow"
    DENY = "deny"
    WARNING = "warning"


@dataclass
class PolicyResult:
    """Result of policy check"""
    decision: PolicyDecision
    reason: str
    metadata: Optional[Dict[str, Any]] = None


class PolicyEngine:
    """
    Enforces business rules for tool execution.
    
    Rules:
    1. Prerequisite Check: Must validate before simulate
    2. Validation TTL: Validation expires after 10 minutes
    3. Parameter Change: Changed params require re-validation
    4. Tool Budget: Max 10 tools per conversation
    
    Usage:
        engine = PolicyEngine()
        result = engine.enforce_policy(
            tool_name="simulate_process",
            context=context_data,
            params={"cane_input": 100}
        )
        
        if result.decision == PolicyDecision.DENY:
            return {"error": result.reason}
    """
    
    # Configuration constants
    VALIDATION_TTL_MINUTES = 10
    MAX_TOOLS_PER_CONVERSATION = 10
    
    # Tool categorization (Manual for MVP simplicity)
    # These match the tools defined in Tool Registry
    # TODO: Switch to dynamic Tool Registry lookup post-MVP if tool count grows
    
    # ========================================
    # PROCESS SERVER - Validation Tools
    # ========================================
    PROCESS_VALIDATION_TOOLS = {
        "validate_process_inputs",
        "validate_process_connections",
        "validate_equipment_inputs"
    }
    
    # ========================================
    # CALC ENGINE SERVER - Validation Tools
    # ========================================
    CALC_ENGINE_VALIDATION_TOOLS = {
        "validate_parameters"
    }
    
    # All validation tools
    VALIDATION_TOOLS = PROCESS_VALIDATION_TOOLS | CALC_ENGINE_VALIDATION_TOOLS
    
    # ========================================
    # PROCESS SERVER - Simulation Tools
    # ========================================
    PROCESS_SIMULATION_TOOLS = {
        "simulate_process",
        "simulate_equipment"
    }
    
    # ========================================
    # CALC ENGINE SERVER - Simulation Tools
    # ========================================
    CALC_ENGINE_SIMULATION_TOOLS = {
        "calc_simulate_process"
    }
    
    # All simulation tools
    SIMULATION_TOOLS = PROCESS_SIMULATION_TOOLS | CALC_ENGINE_SIMULATION_TOOLS
    
    # ========================================
    # CALC ENGINE SERVER - Parameter Editing Tools
    # ========================================
    CALC_ENGINE_PARAMETER_TOOLS = {
        "get_editable_parameters",
        # "edit_parameters",  # REMOVED: auto-saved by calc_simulate_process now
        "get_user_parameters",
        "switch_parameter_version",
        "compare_parameters"
    }
    
    # ========================================
    # CALC ENGINE SERVER - Discovery Tools
    # ========================================
    CALC_ENGINE_DISCOVERY_TOOLS = {
        "calc_list_processes",
        "calc_get_process"
    }
    
    # ========================================
    # CALC ENGINE SERVER - Run Management Tools
    # ========================================
    CALC_ENGINE_RUN_TOOLS = {
        "calc_get_run",
        "calc_list_runs"
    }
    
    # ========================================
    # PREREQUISITE MAPPINGS
    # Maps simulation tools to their required validation tools
    # ========================================
    PREREQUISITE_MAP = {
        # Process Server: simulate_process requires validate_process_inputs
        "simulate_process": ["validate_process_inputs"],
        # Process Server: simulate_equipment requires validate_equipment_inputs
        "simulate_equipment": ["validate_equipment_inputs"],
        # Calc Engine: calc_simulate_process requires validate_parameters
        "calc_simulate_process": ["validate_parameters"],
    }
    
    # ========================================
    # PARAMETER TOOL PREREQUISITES
    # Maps parameter tools to their required discovery tools
    # ========================================
    PARAMETER_PREREQ_MAP = {
        # Must get editable parameters before editing
        # "edit_parameters": ["get_editable_parameters"],  # REMOVED: tool no longer needed
    }
    
    def __init__(self):
        """Initialize policy engine"""
        pass
    
    def enforce_policy(
        self,
        tool_name: str,
        context: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None
    ) -> PolicyResult:
        """
        Main policy enforcement entry point.
        Runs all applicable checks and returns decision.
        
        Args:
            tool_name: Name of tool to execute
            context: Conversation context from Context Manager
            params: Tool parameters (for change detection)
            
        Returns:
            PolicyResult with decision and reason
        """
        # Check 1: Tool budget (applies to all tools)
        budget_result = self.check_tool_budget(context)
        if budget_result.decision == PolicyDecision.DENY:
            return budget_result
        
        # Check 2: Parameter tool prerequisites (for calc engine edit tools)
        if tool_name in self.PARAMETER_PREREQ_MAP:
            prereq_result = self.check_parameter_tool_prerequisite(tool_name, context)
            if prereq_result.decision == PolicyDecision.DENY:
                return prereq_result
        
        # Check 3: Prerequisite check (only for simulation tools)
        if tool_name in self.SIMULATION_TOOLS:
            prereq_result = self.check_prerequisite(tool_name, context)
            if prereq_result.decision == PolicyDecision.DENY:
                return prereq_result
            
            # Check 4: Validation age (if validation exists) - pass tool_name for server-aware checking
            age_result = self.check_validation_age(context, tool_name)
            if age_result.decision != PolicyDecision.ALLOW:
                return age_result
            
            # Check 5: Parameter changes (if params provided)
            if params:
                param_result = self.check_parameters_changed(context, params)
                if param_result.decision == PolicyDecision.DENY:
                    return param_result
        
        # All checks passed - include age_result metadata if available
        metadata = {}
        if tool_name in self.SIMULATION_TOOLS:
            age_result = self.check_validation_age(context, tool_name)
            if age_result.metadata:
                metadata = age_result.metadata
        
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"All policy checks passed for tool: {tool_name}",
            metadata=metadata if metadata else None
        )
    
    def check_parameter_tool_prerequisite(
        self,
        tool_name: str,
        context: Dict[str, Any]
    ) -> PolicyResult:
        """
        Check if parameter editing tools have their prerequisites met.
        
        Rule: Must call get_editable_parameters before edit_parameters
        
        Args:
            tool_name: Tool to check prerequisites for
            context: Conversation context
            
        Returns:
            ALLOW if prerequisites met, DENY otherwise
        """
        if tool_name not in self.PARAMETER_PREREQ_MAP:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="No parameter tool prerequisites required"
            )
        
        required_tools = self.PARAMETER_PREREQ_MAP[tool_name]
        executed_tools = context.get("executed_tools", [])
        executed_tool_names = {tool["tool_name"] for tool in executed_tools}
        
        missing_prereqs = [t for t in required_tools if t not in executed_tool_names]
        
        if missing_prereqs:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=(
                    f"Cannot run {tool_name} without first calling {', '.join(missing_prereqs)}. "
                    f"This ensures you know what parameters are available to edit."
                ),
                metadata={"missing_prerequisites": missing_prereqs}
            )
        
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Parameter tool prerequisites met"
        )
    
    def check_prerequisite(
        self,
        tool_name: str,
        context: Dict[str, Any]
    ) -> PolicyResult:
        """
        Check if prerequisite tools have been executed.
        
        Rule: Must run validation before simulation
        
        Uses PREREQUISITE_MAP for precise tool-to-prerequisite mappings:
        - simulate_process → validate_process_inputs
        - simulate_equipment → validate_equipment_inputs
        
        Falls back to validation tool sets if not in map.
        
        Args:
            tool_name: Tool to check prerequisites for
            context: Conversation context
            
        Returns:
            ALLOW if prerequisites met, DENY otherwise
        """
        # Only simulation tools have prerequisites
        if tool_name not in self.SIMULATION_TOOLS:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="No prerequisites required"
            )
        
        # Check PREREQUISITE_MAP first for precise requirements
        if tool_name in self.PREREQUISITE_MAP:
            required_tools = self.PREREQUISITE_MAP[tool_name]
            executed_tools = context.get("executed_tools", [])
            executed_tool_names = {tool["tool_name"] for tool in executed_tools}
            
            # Check if ALL required prerequisites were executed
            missing_prereqs = [t for t in required_tools if t not in executed_tool_names]
            
            if missing_prereqs:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=(
                        f"Cannot run {tool_name} without prerequisite validation. "
                        f"Please run {', '.join(missing_prereqs)} first."
                    ),
                    metadata={"required_tools": required_tools, "missing": missing_prereqs}
                )
            
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason=f"Prerequisite(s) {', '.join(required_tools)} found"
            )
        
        # Fallback: check if any validation tool was executed
        required_validation_tools = self.VALIDATION_TOOLS
        suggestion = "Please run validate_process_inputs first."
        
        # Check if appropriate validation tool was executed
        executed_tools = context.get("executed_tools", [])
        validation_executed = any(
            tool["tool_name"] in required_validation_tools
            for tool in executed_tools
        )
        
        if not validation_executed:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=(
                    f"Cannot run {tool_name} without validation. "
                    f"{suggestion}"
                ),
                metadata={"required_tools": list(required_validation_tools)}
            )
        
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Prerequisite validation found"
        )
    
    def check_validation_age(
        self,
        context: Dict[str, Any],
        tool_name: str = None
    ) -> PolicyResult:
        """
        Check if validation is still fresh (< 10 minutes old).
        
        Rule: Validation expires after 10 minutes
        
        Args:
            context: Conversation context
            tool_name: Optional simulation tool name for checking
            
        Returns:
            ALLOW if validation fresh, DENY if expired
        """
        executed_tools = context.get("executed_tools", [])
        
        # Check for validation from process server validation tools
        target_validation_tools = self.VALIDATION_TOOLS
        
        # Find most recent validation
        validation_tool = None
        for tool in reversed(executed_tools):
            if tool["tool_name"] in target_validation_tools:
                validation_tool = tool
                break
        
        if not validation_tool:
            # No validation found (should be caught by prerequisite check)
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason="No validation found"
            )
        
        # Parse timestamp
        timestamp_str = validation_tool.get("timestamp")
        if not timestamp_str:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason="Validation timestamp missing"
            )
        
        # Calculate age
        try:
            # Handle both string and datetime objects
            if isinstance(timestamp_str, str):
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp = timestamp_str
            
            now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
            age = now - timestamp
            age_minutes = age.total_seconds() / 60
            
            if age_minutes > self.VALIDATION_TTL_MINUTES:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=(
                        f"Validation expired ({age_minutes:.1f} minutes old, "
                        f"max {self.VALIDATION_TTL_MINUTES} minutes). "
                        "Please re-validate before simulating."
                    ),
                    metadata={
                        "age_minutes": age_minutes,
                        "max_age_minutes": self.VALIDATION_TTL_MINUTES
                    }
                )
            
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason=f"Validation fresh ({age_minutes:.1f} minutes old)",
                metadata={"age_minutes": age_minutes}
            )
            
        except (ValueError, TypeError) as e:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Invalid validation timestamp: {e}"
            )
    
    def check_parameters_changed(
        self,
        context: Dict[str, Any],
        new_params: Dict[str, Any]
    ) -> PolicyResult:
        """
        Check if parameters changed since last validation.
        
        Rule: Changed parameters require re-validation
        
        Args:
            context: Conversation context
            new_params: New parameters for simulation
            
        Returns:
            ALLOW if params unchanged, DENY if changed
        """
        # Get stored parameters from context
        stored_params = context.get("simulation_params", {})
        
        # If no stored params, this is first run - allow
        if not stored_params:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="First simulation run, no previous params to compare"
            )
        
        # Compare parameters
        changed_params = {}
        for key, new_value in new_params.items():
            stored_value = stored_params.get(key)
            if stored_value != new_value:
                changed_params[key] = {
                    "old": stored_value,
                    "new": new_value
                }
        
        if changed_params:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=(
                    f"Parameters changed since last validation: {list(changed_params.keys())}. "
                    "Please re-validate with new parameters before simulating."
                ),
                metadata={"changed_params": changed_params}
            )
        
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Parameters unchanged since validation"
        )
    
    def check_tool_budget(
        self,
        context: Dict[str, Any]
    ) -> PolicyResult:
        """
        Check if conversation has exceeded tool budget.
        
        Rule: Max 10 tools per conversation
        
        Args:
            context: Conversation context
            
        Returns:
            ALLOW if under budget, DENY if exceeded
        """
        executed_tools = context.get("executed_tools", [])
        tool_count = len(executed_tools)
        
        if tool_count >= self.MAX_TOOLS_PER_CONVERSATION:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=(
                    f"Tool budget exceeded ({tool_count}/{self.MAX_TOOLS_PER_CONVERSATION}). "
                    "Please start a new conversation."
                ),
                metadata={
                    "tool_count": tool_count,
                    "max_tools": self.MAX_TOOLS_PER_CONVERSATION
                }
            )
        
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"Tool budget OK ({tool_count}/{self.MAX_TOOLS_PER_CONVERSATION})",
            metadata={"tool_count": tool_count}
        )
    
    def get_policy_summary(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get summary of current policy state for a conversation.
        Useful for debugging and user feedback.
        
        Args:
            context: Conversation context
            
        Returns:
            Dictionary with policy state summary
        """
        executed_tools = context.get("executed_tools", [])
        tool_count = len(executed_tools)
        
        # Find last validation
        last_validation = None
        validation_age_minutes = None
        for tool in reversed(executed_tools):
            if tool["tool_name"] in self.VALIDATION_TOOLS:
                last_validation = tool
                timestamp_str = tool.get("timestamp")
                if timestamp_str:
                    try:
                        if isinstance(timestamp_str, str):
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            timestamp = timestamp_str
                        now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
                        age = now - timestamp
                        validation_age_minutes = age.total_seconds() / 60
                    except (ValueError, TypeError):
                        pass
                break
        
        return {
            "tool_count": tool_count,
            "max_tools": self.MAX_TOOLS_PER_CONVERSATION,
            "tools_remaining": self.MAX_TOOLS_PER_CONVERSATION - tool_count,
            "last_validation": last_validation.get("tool_name") if last_validation else None,
            "validation_age_minutes": validation_age_minutes,
            "validation_ttl_minutes": self.VALIDATION_TTL_MINUTES,
            "validation_fresh": validation_age_minutes < self.VALIDATION_TTL_MINUTES if validation_age_minutes else False,
            "can_simulate": last_validation is not None and (
                validation_age_minutes < self.VALIDATION_TTL_MINUTES if validation_age_minutes else False
            )
        }


# Example usage
if __name__ == "__main__":
    print("🔒 Policy Engine Example\n")
    
    # Create engine
    engine = PolicyEngine()
    
    # Example 1: Try to simulate without validation (DENY)
    print("Example 1: Simulate without validation")
    context1 = {"executed_tools": []}
    result1 = engine.enforce_policy("simulate_process", context1)
    print(f"Decision: {result1.decision.value}")
    print(f"Reason: {result1.reason}\n")
    
    # Example 2: Simulate after validation (ALLOW)
    print("Example 2: Simulate after fresh validation")
    context2 = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
        ],
        "simulation_params": {"cane_input": 100}
    }
    result2 = engine.enforce_policy("simulate_process", context2, {"cane_input": 100})
    print(f"Decision: {result2.decision.value}")
    print(f"Reason: {result2.reason}\n")
    
    # Example 3: Simulate with expired validation (DENY)
    print("Example 3: Simulate with expired validation")
    old_time = datetime.now() - timedelta(minutes=15)
    context3 = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": old_time.isoformat(),
                "status": "success"
            }
        ]
    }
    result3 = engine.enforce_policy("simulate_process", context3)
    print(f"Decision: {result3.decision.value}")
    print(f"Reason: {result3.reason}\n")
    
    # Example 4: Policy summary
    print("Example 4: Policy summary")
    summary = engine.get_policy_summary(context2)
    print(f"Tools used: {summary['tool_count']}/{summary['max_tools']}")
    print(f"Can simulate: {summary['can_simulate']}")
    print(f"Validation age: {summary['validation_age_minutes']:.1f} min")
