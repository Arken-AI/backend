"""
Test Policy Engine - Calculation Engine Server Support

Tests for Policy Engine with dual-server support:
- Process Server tools and prerequisites
- Calculation Engine Server tools and prerequisites

Run with: pytest tests/test_policy_engine_calc_engine.py -v
"""
import pytest
from datetime import datetime, timedelta
from typing import Dict, Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.policy_engine import PolicyEngine, PolicyDecision, PolicyResult


@pytest.fixture
def engine():
    """Create PolicyEngine instance."""
    return PolicyEngine()


class TestCalcEngineToolClassification:
    """Test that calc engine tools are properly classified."""
    
    def test_calc_engine_validation_tools(self, engine):
        """Test calc engine validation tools are recognized."""
        assert "validate_parameters" in engine.CALC_ENGINE_VALIDATION_TOOLS
    
    def test_calc_engine_simulation_tools(self, engine):
        """Test calc engine simulation tools are recognized."""
        assert "calc_simulate_process" in engine.CALC_ENGINE_SIMULATION_TOOLS
    
    def test_calc_engine_parameter_tools(self, engine):
        """Test calc engine parameter tools are recognized."""
        expected = {
            "get_editable_parameters",
            "edit_parameters",
            "get_user_parameters",
            "switch_parameter_version",
            "compare_parameters"
        }
        for tool in expected:
            assert tool in engine.CALC_ENGINE_PARAMETER_TOOLS
    
    def test_calc_engine_discovery_tools(self, engine):
        """Test calc engine discovery tools are recognized."""
        assert "calc_list_processes" in engine.CALC_ENGINE_DISCOVERY_TOOLS
        assert "calc_get_process" in engine.CALC_ENGINE_DISCOVERY_TOOLS
    
    def test_calc_engine_run_tools(self, engine):
        """Test calc engine run management tools are recognized."""
        assert "calc_get_run" in engine.CALC_ENGINE_RUN_TOOLS
        assert "calc_list_runs" in engine.CALC_ENGINE_RUN_TOOLS


class TestCalcEnginePrerequisites:
    """Test prerequisite checking for calc engine tools."""
    
    def test_calc_simulate_requires_validate_parameters(self, engine):
        """Test that calc_simulate_process requires validate_parameters."""
        # Without validation
        context = {"executed_tools": []}
        result = engine.enforce_policy("calc_simulate_process", context)
        
        assert result.decision == PolicyDecision.DENY
        assert "validate" in result.reason.lower()
    
    def test_calc_simulate_allowed_after_validate(self, engine):
        """Test calc_simulate_process allowed after validate_parameters."""
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_parameters",
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
            ]
        }
        result = engine.enforce_policy("calc_simulate_process", context)
        
        assert result.decision == PolicyDecision.ALLOW
    
    def test_edit_parameters_requires_get_editable(self, engine):
        """Test that edit_parameters requires get_editable_parameters."""
        # Without get_editable_parameters
        context = {"executed_tools": []}
        result = engine.enforce_policy("edit_parameters", context)
        
        assert result.decision == PolicyDecision.DENY
        assert "get_editable_parameters" in result.reason.lower()
    
    def test_edit_parameters_allowed_after_get_editable(self, engine):
        """Test edit_parameters allowed after get_editable_parameters."""
        context = {
            "executed_tools": [
                {
                    "tool_name": "get_editable_parameters",
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
            ]
        }
        result = engine.enforce_policy("edit_parameters", context)
        
        assert result.decision == PolicyDecision.ALLOW


class TestCalcEngineValidationAge:
    """Test validation age checking for calc engine tools."""
    
    def test_fresh_validation_allows_simulate(self, engine):
        """Test fresh validation (< 10 min) allows simulation."""
        fresh_time = datetime.now() - timedelta(minutes=5)
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_parameters",
                    "timestamp": fresh_time.isoformat(),
                    "status": "success"
                }
            ]
        }
        result = engine.enforce_policy("calc_simulate_process", context)
        
        assert result.decision == PolicyDecision.ALLOW
    
    def test_expired_validation_denies_simulate(self, engine):
        """Test expired validation (> 10 min) denies simulation."""
        old_time = datetime.now() - timedelta(minutes=15)
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_parameters",
                    "timestamp": old_time.isoformat(),
                    "status": "success"
                }
            ]
        }
        result = engine.enforce_policy("calc_simulate_process", context)
        
        assert result.decision == PolicyDecision.DENY
        assert "expire" in result.reason.lower() or "age" in result.reason.lower()


class TestNoPrerequisiteTools:
    """Test tools that don't require prerequisites."""
    
    def test_discovery_tools_no_prereq(self, engine):
        """Test discovery tools don't require prerequisites."""
        discovery_tools = ["calc_list_processes", "calc_get_process"]
        
        for tool in discovery_tools:
            context = {"executed_tools": []}
            result = engine.enforce_policy(tool, context)
            
            assert result.decision == PolicyDecision.ALLOW, f"{tool} should be allowed"
    
    def test_run_tools_no_prereq(self, engine):
        """Test run management tools don't require prerequisites."""
        run_tools = ["calc_get_run", "calc_list_runs"]
        
        for tool in run_tools:
            context = {"executed_tools": []}
            result = engine.enforce_policy(tool, context)
            
            assert result.decision == PolicyDecision.ALLOW, f"{tool} should be allowed"
    
    def test_get_editable_parameters_no_prereq(self, engine):
        """Test get_editable_parameters doesn't require prerequisites."""
        context = {"executed_tools": []}
        result = engine.enforce_policy("get_editable_parameters", context)
        
        assert result.decision == PolicyDecision.ALLOW
    
    def test_validate_parameters_no_prereq(self, engine):
        """Test validate_parameters doesn't require prerequisites."""
        context = {"executed_tools": []}
        result = engine.enforce_policy("validate_parameters", context)
        
        assert result.decision == PolicyDecision.ALLOW
    
    def test_get_user_parameters_no_prereq(self, engine):
        """Test get_user_parameters doesn't require prerequisites."""
        context = {"executed_tools": []}
        result = engine.enforce_policy("get_user_parameters", context)
        
        assert result.decision == PolicyDecision.ALLOW


class TestMixedServerPrerequisites:
    """Test that prerequisites are server-aware."""
    
    def test_process_validation_doesnt_satisfy_calc(self, engine):
        """Test process server validation doesn't satisfy calc engine simulation."""
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_process_inputs",  # Process server validation
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
            ]
        }
        result = engine.enforce_policy("calc_simulate_process", context)
        
        # Should still deny - needs validate_parameters, not validate_process_inputs
        assert result.decision == PolicyDecision.DENY
    
    def test_calc_validation_doesnt_satisfy_process(self, engine):
        """Test calc engine validation doesn't satisfy process server simulation."""
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_parameters",  # Calc engine validation
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
            ]
        }
        result = engine.enforce_policy("simulate_process", context)
        
        # Should still deny - needs validate_process_inputs, not validate_parameters
        assert result.decision == PolicyDecision.DENY


class TestToolBudget:
    """Test tool budget limits apply to all servers."""
    
    def test_budget_applies_to_calc_tools(self, engine):
        """Test tool budget applies to calc engine tools."""
        # Create context with max tools already used
        context = {
            "executed_tools": [
                {"tool_name": f"tool_{i}", "timestamp": datetime.now().isoformat()}
                for i in range(10)
            ]
        }
        
        result = engine.enforce_policy("calc_simulate_process", context)
        
        assert result.decision == PolicyDecision.DENY
        assert "budget" in result.reason.lower() or "limit" in result.reason.lower()
    
    def test_budget_mixed_server_tools(self, engine):
        """Test budget counts tools from all servers."""
        # Mix of process and calc engine tools
        context = {
            "executed_tools": [
                {"tool_name": "list_industries", "timestamp": datetime.now().isoformat()},
                {"tool_name": "calc_list_processes", "timestamp": datetime.now().isoformat()},
                {"tool_name": "validate_process_inputs", "timestamp": datetime.now().isoformat()},
                {"tool_name": "validate_parameters", "timestamp": datetime.now().isoformat()},
                {"tool_name": "simulate_process", "timestamp": datetime.now().isoformat()},
                {"tool_name": "calc_simulate_process", "timestamp": datetime.now().isoformat()},
                {"tool_name": "get_run", "timestamp": datetime.now().isoformat()},
                {"tool_name": "calc_get_run", "timestamp": datetime.now().isoformat()},
                {"tool_name": "get_editable_parameters", "timestamp": datetime.now().isoformat()},
                {"tool_name": "edit_parameters", "timestamp": datetime.now().isoformat()},
            ]
        }
        
        result = engine.enforce_policy("any_tool", context)
        
        assert result.decision == PolicyDecision.DENY


class TestPrerequisiteMap:
    """Test prerequisite mapping configuration."""
    
    def test_prerequisite_map_includes_calc_engine(self, engine):
        """Test PREREQUISITE_MAP includes calc engine mappings."""
        assert "calc_simulate_process" in engine.PREREQUISITE_MAP
        assert "validate_parameters" in engine.PREREQUISITE_MAP["calc_simulate_process"]
    
    def test_parameter_prereq_map(self, engine):
        """Test PARAMETER_PREREQ_MAP is configured."""
        assert "edit_parameters" in engine.PARAMETER_PREREQ_MAP
        assert "get_editable_parameters" in engine.PARAMETER_PREREQ_MAP["edit_parameters"]


class TestCombinedPolicyChecks:
    """Test combined policy checks for calc engine."""
    
    def test_all_checks_pass(self, engine):
        """Test when all checks pass for calc engine simulation."""
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_parameters",
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
            ],
            "simulation_params": {"feed_rate": 100}
        }
        result = engine.enforce_policy(
            "calc_simulate_process", 
            context, 
            {"feed_rate": 100}  # Same params
        )
        
        assert result.decision == PolicyDecision.ALLOW
    
    def test_parameter_change_requires_revalidation(self, engine):
        """Test that changed parameters require re-validation."""
        context = {
            "executed_tools": [
                {
                    "tool_name": "validate_parameters",
                    "timestamp": datetime.now().isoformat(),
                    "status": "success"
                }
            ],
            "simulation_params": {"feed_rate": 100}
        }
        result = engine.enforce_policy(
            "calc_simulate_process",
            context,
            {"feed_rate": 200}  # Changed params
        )
        
        assert result.decision == PolicyDecision.DENY
        assert "param" in result.reason.lower() or "change" in result.reason.lower()


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
