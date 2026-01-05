"""
Context Manager Tests

Simple tests to verify Context Manager functionality.
Run with: python test_context_manager.py
"""

import sys
import redis
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, '/Users/akashnikam/arken/calculation_engine/backend')

from app.services.context_manager import ContextManager
from motor.motor_asyncio import AsyncIOMotorClient


def test_context_manager():
    """Test Context Manager functionality."""
    
    print("🧪 Testing Context Manager\n")
    
    # Setup
    redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    mongo_client = AsyncIOMotorClient("mongodb://localhost:27017")
    
    cm = ContextManager(redis_client, mongo_client)
    
    # Test 1: Create context
    print("✅ Test 1: Create Context")
    context = cm.create_context("test_conv_001", user_id="test_user")
    assert context["conversation_id"] == "test_conv_001"
    assert context["user_id"] == "test_user"
    assert context["executed_tools"] == []
    print(f"   Created conversation: {context['conversation_id']}")
    print(f"   User: {context['user_id']}")
    print()
    
    # Test 2: Update context
    print("✅ Test 2: Update Context")
    cm.update_context("test_conv_001", {
        "current_industry": "sugar",
        "current_process": "sugar_production",
        "simulation_params": {"cane_input": 100}
    })
    
    context = cm.get_context("test_conv_001")
    assert context["current_industry"] == "sugar"
    assert context["current_process"] == "sugar_production"
    assert context["simulation_params"]["cane_input"] == 100
    print(f"   Industry: {context['current_industry']}")
    print(f"   Process: {context['current_process']}")
    print(f"   Params: {context['simulation_params']}")
    print()
    
    # Test 3: Add tool execution - validation
    print("✅ Test 3: Track Validation Tool")
    cm.add_tool_execution("test_conv_001", "validate_process_inputs", {
        "status": "success",
        "process_id": "sugar_production",
        "valid": True
    })
    
    tools = cm.get_executed_tools("test_conv_001")
    assert "validate_process_inputs" in tools
    print(f"   Executed tools: {tools}")
    print()
    
    # Test 4: Check validation exists
    print("✅ Test 4: Check Validation")
    validation = cm.get_last_validation("test_conv_001", "sugar_production")
    assert validation is not None
    assert validation["valid"] == True
    assert validation["status"] == "success"
    
    age = datetime.utcnow() - validation['timestamp']
    print(f"   Found validation: {validation['tool_name']}")
    print(f"   Status: {validation['status']}")
    print(f"   Valid: {validation['valid']}")
    print(f"   Age: {age.total_seconds():.2f} seconds")
    print(f"   Fresh (< 10 min): {age.total_seconds() < 600}")
    print()
    
    # Test 5: Add simulation tool
    print("✅ Test 5: Track Simulation Tool")
    cm.add_tool_execution("test_conv_001", "simulate_process", {
        "status": "success",
        "process_id": "sugar_production",
        "run_id": "run_test_123"
    })
    
    context = cm.get_context("test_conv_001")
    tools = cm.get_executed_tools("test_conv_001")
    
    assert "simulate_process" in tools
    assert context["last_run_id"] == "run_test_123"
    print(f"   Executed tools: {tools}")
    print(f"   Last run ID: {context['last_run_id']}")
    print()
    
    # Test 6: Multi-conversation isolation
    print("✅ Test 6: Conversation Isolation")
    context2 = cm.create_context("test_conv_002", user_id="another_user")
    cm.update_context("test_conv_002", {"current_industry": "hydrogen"})
    
    # Verify contexts are separate
    context1 = cm.get_context("test_conv_001")
    context2 = cm.get_context("test_conv_002")
    
    assert context1["current_industry"] == "sugar"
    assert context2["current_industry"] == "hydrogen"
    print(f"   Conv 1 industry: {context1['current_industry']}")
    print(f"   Conv 2 industry: {context2['current_industry']}")
    print(f"   ✓ Conversations are isolated")
    print()
    
    # Test 7: Tool execution history
    print("✅ Test 7: Full Execution History")
    context = cm.get_context("test_conv_001")
    print(f"   Total tools executed: {len(context['executed_tools'])}")
    
    for i, exec_record in enumerate(context["executed_tools"], 1):
        print(f"   {i}. {exec_record['tool_name']}")
        print(f"      Status: {exec_record['status']}")
        print(f"      Time: {exec_record['timestamp']}")
    print()
    
    # Cleanup
    print("🧹 Cleanup")
    cm.clear_context("test_conv_001")
    cm.clear_context("test_conv_002")
    print("   Contexts cleared")
    print()
    
    print("=" * 50)
    print("✅ All tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    test_context_manager()
