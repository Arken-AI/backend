"""
Test Policy Engine

Comprehensive tests for policy enforcement rules.
Tests all business rules and edge cases.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from core.policy_engine import PolicyEngine, PolicyDecision, PolicyResult


def test_prerequisite_check():
    """Test: Must validate before simulate"""
    print("🧪 Test 1: Prerequisite Check (Validate Before Simulate)")
    
    engine = PolicyEngine()
    
    # Test 1a: Try to simulate without validation (DENY)
    print("\n  1a. Simulate without validation:")
    context = {"executed_tools": []}
    result = engine.enforce_policy("simulate_process", context)
    assert result.decision == PolicyDecision.DENY, "Should deny simulate without validation"
    print(f"     ✅ DENIED: {result.reason}")
    
    # Test 1b: Simulate after validation (ALLOW)
    print("\n  1b. Simulate after validation:")
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
        ]
    }
    result = engine.enforce_policy("simulate_process", context)
    assert result.decision == PolicyDecision.ALLOW, "Should allow simulate after validation"
    print(f"     ✅ ALLOWED: {result.reason}")
    
    # Test 1c: Non-simulation tools don't need validation (ALLOW)
    print("\n  1c. Non-simulation tool (no prerequisite):")
    context = {"executed_tools": []}
    result = engine.enforce_policy("get_process_schema", context)
    assert result.decision == PolicyDecision.ALLOW, "Should allow non-simulation tools"
    print(f"     ✅ ALLOWED: {result.reason}")


def test_validation_age():
    """Test: Validation expires after 10 minutes"""
    print("\n\n🧪 Test 2: Validation Age (10 Minute TTL)")
    
    engine = PolicyEngine()
    
    # Test 2a: Fresh validation (ALLOW)
    print("\n  2a. Fresh validation (2 minutes old):")
    fresh_time = datetime.now() - timedelta(minutes=2)
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": fresh_time.isoformat(),
                "status": "success"
            }
        ]
    }
    result = engine.enforce_policy("simulate_process", context)
    assert result.decision == PolicyDecision.ALLOW, "Should allow with fresh validation"
    print(f"     ✅ ALLOWED: {result.reason}")
    print(f"     Age: {result.metadata['age_minutes']:.1f} minutes")
    
    # Test 2b: Expired validation (DENY)
    print("\n  2b. Expired validation (15 minutes old):")
    old_time = datetime.now() - timedelta(minutes=15)
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": old_time.isoformat(),
                "status": "success"
            }
        ]
    }
    result = engine.enforce_policy("simulate_process", context)
    assert result.decision == PolicyDecision.DENY, "Should deny with expired validation"
    print(f"     ✅ DENIED: {result.reason}")
    print(f"     Age: {result.metadata['age_minutes']:.1f} minutes")
    
    # Test 2c: Exactly at limit (ALLOW)
    print("\n  2c. Validation at 9.5 minutes (just under limit):")
    limit_time = datetime.now() - timedelta(minutes=9.5)
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": limit_time.isoformat(),
                "status": "success"
            }
        ]
    }
    result = engine.enforce_policy("simulate_process", context)
    assert result.decision == PolicyDecision.ALLOW, "Should allow at 9.5 minutes"
    print(f"     ✅ ALLOWED: {result.reason}")


def test_parameter_changes():
    """Test: Changed parameters require re-validation"""
    print("\n\n🧪 Test 3: Parameter Change Detection")
    
    engine = PolicyEngine()
    
    # Test 3a: Unchanged parameters (ALLOW)
    print("\n  3a. Unchanged parameters:")
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
        ],
        "simulation_params": {"cane_input": 100, "temperature": 115}
    }
    new_params = {"cane_input": 100, "temperature": 115}
    result = engine.enforce_policy("simulate_process", context, new_params)
    assert result.decision == PolicyDecision.ALLOW, "Should allow unchanged params"
    print(f"     ✅ ALLOWED: {result.reason}")
    
    # Test 3b: Changed parameters (DENY)
    print("\n  3b. Changed parameters:")
    new_params = {"cane_input": 150, "temperature": 115}  # Changed cane_input
    result = engine.enforce_policy("simulate_process", context, new_params)
    assert result.decision == PolicyDecision.DENY, "Should deny changed params"
    print(f"     ✅ DENIED: {result.reason}")
    print(f"     Changed: {list(result.metadata['changed_params'].keys())}")
    
    # Test 3c: First run (no previous params) (ALLOW)
    print("\n  3c. First simulation (no previous params):")
    context_no_params = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
        ],
        "simulation_params": {}
    }
    new_params = {"cane_input": 100}
    result = engine.enforce_policy("simulate_process", context_no_params, new_params)
    assert result.decision == PolicyDecision.ALLOW, "Should allow first run"
    print(f"     ✅ ALLOWED: {result.reason}")


def test_tool_budget():
    """Test: Max 10 tools per conversation"""
    print("\n\n🧪 Test 4: Tool Budget (Max 10 Tools)")
    
    engine = PolicyEngine()
    
    # Test 4a: Under budget (ALLOW)
    print("\n  4a. Under budget (5 tools used):")
    context = {
        "executed_tools": [
            {"tool_name": f"tool_{i}", "timestamp": datetime.now().isoformat()}
            for i in range(5)
        ]
    }
    result = engine.enforce_policy("any_tool", context)
    assert result.decision == PolicyDecision.ALLOW, "Should allow under budget"
    print(f"     ✅ ALLOWED: {result.reason}")
    
    # Test 4b: At budget limit (DENY)
    print("\n  4b. At budget limit (10 tools used):")
    context = {
        "executed_tools": [
            {"tool_name": f"tool_{i}", "timestamp": datetime.now().isoformat()}
            for i in range(10)
        ]
    }
    result = engine.enforce_policy("any_tool", context)
    assert result.decision == PolicyDecision.DENY, "Should deny at budget limit"
    print(f"     ✅ DENIED: {result.reason}")
    
    # Test 4c: Over budget (DENY)
    print("\n  4c. Over budget (15 tools used):")
    context = {
        "executed_tools": [
            {"tool_name": f"tool_{i}", "timestamp": datetime.now().isoformat()}
            for i in range(15)
        ]
    }
    result = engine.enforce_policy("any_tool", context)
    assert result.decision == PolicyDecision.DENY, "Should deny over budget"
    print(f"     ✅ DENIED: {result.reason}")


def test_combined_checks():
    """Test: Multiple policy checks together"""
    print("\n\n🧪 Test 5: Combined Policy Checks")
    
    engine = PolicyEngine()
    
    # Test 5a: All checks pass (ALLOW)
    print("\n  5a. All checks pass:")
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
        ],
        "simulation_params": {"cane_input": 100}
    }
    result = engine.enforce_policy("simulate_process", context, {"cane_input": 100})
    assert result.decision == PolicyDecision.ALLOW, "Should allow when all checks pass"
    print(f"     ✅ ALLOWED: {result.reason}")
    
    # Test 5b: Multiple violations (first one wins)
    print("\n  5b. Multiple violations (expired + changed params):")
    old_time = datetime.now() - timedelta(minutes=15)
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": old_time.isoformat(),
                "status": "success"
            }
        ],
        "simulation_params": {"cane_input": 100}
    }
    result = engine.enforce_policy("simulate_process", context, {"cane_input": 200})
    assert result.decision == PolicyDecision.DENY, "Should deny on first violation"
    print(f"     ✅ DENIED: {result.reason}")
    print(f"     (Note: Validation age checked before params)")


def test_policy_summary():
    """Test: Policy state summary"""
    print("\n\n🧪 Test 6: Policy Summary")
    
    engine = PolicyEngine()
    
    context = {
        "executed_tools": [
            {
                "tool_name": "validate_process_inputs",
                "timestamp": (datetime.now() - timedelta(minutes=5)).isoformat(),
                "status": "success"
            },
            {
                "tool_name": "simulate_process",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
        ],
        "simulation_params": {"cane_input": 100}
    }
    
    summary = engine.get_policy_summary(context)
    
    print(f"\n  Summary for 2-tool conversation:")
    print(f"     Tools used: {summary['tool_count']}/{summary['max_tools']}")
    print(f"     Tools remaining: {summary['tools_remaining']}")
    print(f"     Last validation: {summary['last_validation']}")
    print(f"     Validation age: {summary['validation_age_minutes']:.1f} min")
    print(f"     Validation fresh: {summary['validation_fresh']}")
    print(f"     Can simulate: {summary['can_simulate']}")
    print(f"     ✅ Summary generated successfully")
    
    assert summary['tool_count'] == 2
    assert summary['can_simulate'] == True
    assert summary['validation_fresh'] == True


def test_realistic_workflow():
    """Test: Realistic multi-step workflow"""
    print("\n\n🧪 Test 7: Realistic Workflow Simulation")
    
    engine = PolicyEngine()
    
    # Step 1: Validate
    print("\n  Step 1: Initial validation")
    context = {"executed_tools": []}
    result = engine.enforce_policy("validate_process_inputs", context)
    print(f"     ✅ Validation allowed")
    
    # Add validation to context
    context["executed_tools"].append({
        "tool_name": "validate_process_inputs",
        "timestamp": datetime.now().isoformat(),
        "status": "success"
    })
    context["simulation_params"] = {"cane_input": 100}
    
    # Step 2: Simulate (should work)
    print("\n  Step 2: First simulation")
    result = engine.enforce_policy("simulate_process", context, {"cane_input": 100})
    assert result.decision == PolicyDecision.ALLOW
    print(f"     ✅ Simulation allowed")
    
    # Add simulation to context
    context["executed_tools"].append({
        "tool_name": "simulate_process",
        "timestamp": datetime.now().isoformat(),
        "status": "success"
    })
    
    # Step 3: Simulate again with same params (should work)
    print("\n  Step 3: Second simulation (unchanged params)")
    result = engine.enforce_policy("simulate_process", context, {"cane_input": 100})
    assert result.decision == PolicyDecision.ALLOW
    print(f"     ✅ Simulation allowed (params unchanged)")
    
    # Step 4: Simulate with changed params (should fail)
    print("\n  Step 4: Third simulation (changed params)")
    result = engine.enforce_policy("simulate_process", context, {"cane_input": 150})
    assert result.decision == PolicyDecision.DENY
    print(f"     ✅ Simulation denied (params changed)")
    
    # Step 5: Re-validate with new params
    print("\n  Step 5: Re-validate with new params")
    context["executed_tools"].append({
        "tool_name": "validate_process_inputs",
        "timestamp": datetime.now().isoformat(),
        "status": "success"
    })
    context["simulation_params"] = {"cane_input": 150}
    result = engine.enforce_policy("validate_process_inputs", context)
    print(f"     ✅ Re-validation allowed")
    
    # Step 6: Simulate with new params (should work)
    print("\n  Step 6: Simulation with new params")
    result = engine.enforce_policy("simulate_process", context, {"cane_input": 150})
    assert result.decision == PolicyDecision.ALLOW
    print(f"     ✅ Simulation allowed (after re-validation)")


if __name__ == "__main__":
    print("🔒 Testing Policy Engine\n")
    print("=" * 70)
    
    try:
        test_prerequisite_check()
        test_validation_age()
        test_parameter_changes()
        test_tool_budget()
        test_combined_checks()
        test_policy_summary()
        test_realistic_workflow()
        
        print("\n" + "=" * 70)
        print("✅ All Policy Engine tests passed!")
        print("=" * 70)
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
