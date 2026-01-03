"""
Test MCP Client Workflow - Simulate Real Usage Without LLM

This test demonstrates how the backend will use MCP tools
in a real conversation flow, but WITHOUT involving Claude.
"""
import asyncio
import sys
import os
import json

from app.core import MCPClient, MCPServerConfig


async def test_discovery_workflow():
    """
    Test 1: Discovery Workflow
    Simulate: "What sugar processes are available?"
    """
    print("=" * 70)
    print("Test 1: Discovery Workflow (Without LLM)")
    print("User Question: 'What sugar processes are available?'")
    print("=" * 70)
    
    # Setup
    os.environ['MCP_SERVER_COMMAND'] = '/Users/akashnikam/arken/calculation_engine/mcp_process_server/venv/bin/python'
    os.environ['MCP_SERVER_ARGS'] = '/Users/akashnikam/arken/calculation_engine/mcp_process_server/server.py'
    os.environ['MCP_SERVER_ENV_CALC_ENGINE_URL'] = 'http://localhost:8000'
    os.environ['MCP_SERVER_ENV_MAX_STORED_RUNS'] = '1000'
    os.environ['MCP_SERVER_ENV_MONGODB_URI'] = 'mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin'
    
    config = MCPServerConfig.from_env()
    client = MCPClient(config)
    
    try:
        await client.connect()
        print("✓ MCP server connected\n")
        
        # Step 1: List industries
        print("Step 1: Calling list_industries...")
        result1 = await client.call_tool("list_industries", {})
        data1 = json.loads(result1)
        print(f"✓ Found {len(data1['industries'])} industry(ies)")
        for ind in data1['industries']:
            print(f"  - {ind['name']}: {ind['description']}")
        
        # Step 2: List processes for sugar
        print("\nStep 2: Calling list_processes for 'sugar'...")
        result2 = await client.call_tool("list_processes", {"industry_id": "sugar"})
        data2 = json.loads(result2)
        print(f"✓ Found {len(data2['processes'])} process(es)")
        for proc in data2['processes']:
            print(f"  - {proc['display_name']}: {proc['description'][:60]}...")
        
        # Step 3: Get detailed info on sugar_factory
        print("\nStep 3: Calling get_process for 'sugar_factory'...")
        result3 = await client.call_tool("get_process", {
            "process_id": "sugar_factory"
        })
        data3 = json.loads(result3)
        print(f"✓ Process: {data3['display_name']}")
        print(f"  Equipment count: {data3['equipment_count']}")
        print(f"  Industry: {data3['industry']}")
        
        await client.disconnect()
        print("\n✓ Discovery workflow complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Workflow failed: {e}")
        import traceback
        traceback.print_exc()
        if client.is_connected():
            await client.disconnect()
        return False


async def test_validation_workflow():
    """
    Test 2: Validation Workflow
    Simulate: "Can you validate my process inputs?"
    """
    print("\n" + "=" * 70)
    print("Test 2: Validation Workflow (Without LLM)")
    print("User Question: 'Validate my sugar mill inputs'")
    print("=" * 70)
    
    config = MCPServerConfig.from_env()
    client = MCPClient(config)
    
    try:
        await client.connect()
        print("✓ MCP server connected\n")
        
        # Prepare test inputs (matching schema from schemas.py)
        test_inputs = {
            "process_id": "sugar_factory",
            "feeds": {
                "cane_inlet": {
                    "flow_kg_hr": 100000,
                    "brix": 15.0,
                    "fiber_pct": 13.0
                }
            },
            "node_params": {}  # Empty = use defaults
        }
        
        print("Calling validate_process_inputs...")
        print(f"Feeds: {json.dumps(test_inputs['feeds'], indent=2)}\n")
        
        result = await client.call_tool("validate_process_inputs", test_inputs)
        
        # Result might be JSON or text error message
        try:
            data = json.loads(result)
            if data.get('status') == 'valid':
                print("✓ Validation PASSED")
                print(f"  Message: {data.get('message', 'OK')}")
            else:
                print("✗ Validation FAILED")
                print(f"  Errors: {data.get('errors', [])}")
        except json.JSONDecodeError:
            # Text response (likely error)
            if "error" in result.lower() or "required" in result.lower():
                print("✗ Validation FAILED")
                print(f"  Error: {result}")
            else:
                print("✓ Validation response:")
                print(f"  {result}")
        
        await client.disconnect()
        print("\n✓ Validation workflow complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Workflow failed: {e}")
        import traceback
        traceback.print_exc()
        if client.is_connected():
            await client.disconnect()
        return False


async def test_simulation_workflow():
    """
    Test 3: Full Simulation Workflow
    Simulate: "Run a sugar mill simulation with 100 tons of cane"
    """
    print("\n" + "=" * 70)
    print("Test 3: Full Simulation Workflow (Without LLM)")
    print("User Question: 'Simulate sugar mill with 100 tons cane'")
    print("=" * 70)
    
    config = MCPServerConfig.from_env()
    client = MCPClient(config)
    
    try:
        await client.connect()
        print("✓ MCP server connected\n")
        
        # Step 1: Validate inputs first
        print("Step 1: Validating inputs...")
        validate_args = {
            "process_id": "sugar_factory",
            "feeds": {
                "cane_inlet": {
                    "flow_kg_hr": 100000,
                    "brix": 15.0,
                    "fiber_pct": 13.0
                }
            },
            "node_params": {}
        }
        validate_result = await client.call_tool("validate_process_inputs", validate_args)
        print(f"✓ Validation: {validate_result[:100]}...\n")
        
        # Step 2: Run simulation
        print("Step 2: Running simulation (this may take 10-30 seconds)...")
        sim_args = {
            "process_id": "sugar_factory",
            "feeds": {
                "cane_inlet": {
                    "flow_kg_hr": 100000,
                    "brix": 15.0,
                    "fiber_pct": 13.0
                }
            },
            "node_params": {}  # Empty = use defaults for all equipment
        }
        
        sim_result = await client.call_tool("simulate_process", sim_args)
        
        # Handle both JSON and text responses
        try:
            sim_data = json.loads(sim_result)
        except json.JSONDecodeError:
            print(f"✗ Simulation response (not JSON): {sim_result[:200]}...")
            await client.disconnect()
            return False
        
        if sim_data.get('status') == 'success':
            print("✓ Simulation COMPLETED")
            calc_run_id = sim_data.get('calc_run_id')
            print(f"\n  Run ID: {calc_run_id}")
            
            # Show key outputs if available
            if 'outputs' in sim_data:
                outputs = sim_data['outputs']
                print(f"  Outputs available: {len(outputs)} equipment blocks")
                # Show first output block
                first_key = list(outputs.keys())[0] if outputs else None
                if first_key:
                    print(f"  First output ({first_key}): {str(outputs[first_key])[:80]}...")
        else:
            print(f"✗ Simulation failed: {sim_data.get('error', 'Unknown error')}")
            await client.disconnect()
            return False
        
        # Step 3: Retrieve run details
        print(f"\nStep 3: Retrieving run details for {calc_run_id}...")
        run_result = await client.call_tool("get_run", {
            "calc_run_id": calc_run_id
        })
        run_data = json.loads(run_result)
        
        print(f"✓ Run retrieved")
        print(f"  Status: {run_data.get('status', 'N/A')}")
        if 'created_at' in run_data:
            print(f"  Created: {run_data['created_at']}")
        
        await client.disconnect()
        print("\n✓ Full simulation workflow complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Workflow failed: {e}")
        import traceback
        traceback.print_exc()
        if client.is_connected():
            await client.disconnect()
        return False


async def test_concurrent_discovery():
    """
    Test 4: Multiple tool calls in sequence (but still serialized by lock)
    """
    print("\n" + "=" * 70)
    print("Test 4: Sequential Tool Calls (Testing Lock Behavior)")
    print("=" * 70)
    
    config = MCPServerConfig.from_env()
    client = MCPClient(config)
    
    try:
        await client.connect()
        print("✓ MCP server connected\n")
        
        # Call 5 different discovery tools in sequence
        tools_to_call = [
            ("list_industries", {}),
            ("get_equipment_types", {"industry_id": "sugar"}),
            ("get_stream_schema", {"stream_type": "juice"}),
            ("list_processes", {"industry_id": "sugar"}),
            ("get_process", {"industry_id": "sugar", "process_id": "sugar_factory"}),
        ]
        
        for i, (tool_name, args) in enumerate(tools_to_call, 1):
            print(f"Call {i}/5: {tool_name}...")
            result = await client.call_tool(tool_name, args)
            print(f"  ✓ Completed (response length: {len(str(result))} chars)")
        
        await client.disconnect()
        print("\n✓ Sequential calls complete (lock ensures one at a time)!")
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        if client.is_connected():
            await client.disconnect()
        return False


async def main():
    """Run all workflow tests"""
    print("\n" + "=" * 70)
    print("MCP Client Workflow Tests (NO LLM INVOLVED)")
    print("Testing direct tool calls as backend will use them")
    print("=" * 70 + "\n")
    
    results = []
    
    # Test 1: Discovery
    result = await test_discovery_workflow()
    results.append(("Discovery Workflow", result))
    
    # Test 2: Validation
    result = await test_validation_workflow()
    results.append(("Validation Workflow", result))
    
    # Test 3: Simulation (this takes time!)
    print("\n⚠️  Next test will run a real simulation (30+ seconds)...")
    await asyncio.sleep(2)
    result = await test_simulation_workflow()
    results.append(("Simulation Workflow", result))
    
    # Test 4: Sequential calls
    result = await test_concurrent_discovery()
    results.append(("Sequential Calls", result))
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{status} - {test_name}")
    
    passed_count = sum(1 for _, p in results if p)
    total = len(results)
    print(f"\nTotal: {passed_count}/{total} workflows passed")
    
    if passed_count == total:
        print("\n🎉 All workflows completed successfully!")
        print("This proves MCP client works WITHOUT any LLM involvement!")


if __name__ == "__main__":
    asyncio.run(main())
