"""
Test Gemini LLM Provider with MCP Tools

Tests the integration between Gemini API and MCP tools.
Verifies tool conversion, message creation, and response parsing.
"""
import asyncio
import sys
import os
import json
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv('.env')

from app.core.llm_gemini_provider import GeminiProvider
from app.core import MCPClient, MCPServerConfig


async def test_tool_conversion():
    """
    Test 1: MCP Tool → Gemini Format Conversion
    Verify tools are properly converted for Gemini
    """
    print("=" * 70)
    print("Test 1: Tool Conversion (MCP → Gemini Format)")
    print("=" * 70)
    
    # Setup MCP client
    os.environ['MCP_SERVER_COMMAND'] = '/Users/akashnikam/arken/calculation_engine/mcp_process_server/venv/bin/python'
    os.environ['MCP_SERVER_ARGS'] = '/Users/akashnikam/arken/calculation_engine/mcp_process_server/server.py'
    os.environ['MCP_SERVER_ENV_CALC_ENGINE_URL'] = 'http://localhost:8000'
    os.environ['MCP_SERVER_ENV_MAX_STORED_RUNS'] = '1000'
    os.environ['MCP_SERVER_ENV_MONGODB_URI'] = 'mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin'
    
    config = MCPServerConfig.from_env()
    mcp_client = MCPClient(config)
    
    try:
        await mcp_client.connect()
        print("✓ MCP server connected\n")
        
        # Get MCP tools
        mcp_tools = await mcp_client.list_tools()
        print(f"✓ Retrieved {len(mcp_tools)} MCP tools")
        
        # Convert to Gemini format
        from app.core.llm_gemini_provider import convert_mcp_tools_to_gemini
        gemini_tools = convert_mcp_tools_to_gemini(mcp_tools)
        
        print(f"✓ Converted to {len(gemini_tools)} Gemini function declarations\n")
        
        # Verify structure
        tool = gemini_tools[0]
        print(f"Sample tool structure:")
        print(f"  name: {tool['name']}")
        print(f"  description: {tool['description'][:60]}...")
        print(f"  parameters keys: {list(tool['parameters'].keys())}")
        
        # Verify all required fields
        for idx, tool in enumerate(gemini_tools, 1):
            assert 'name' in tool, f"Tool {idx} missing 'name'"
            assert 'description' in tool, f"Tool {idx} missing 'description'"
            assert 'parameters' in tool, f"Tool {idx} missing 'parameters'"
            assert tool['parameters'].get('type') == 'object', f"Tool {idx} invalid schema type"
        
        print(f"\n✓ All {len(gemini_tools)} tools validated")
        
        await mcp_client.disconnect()
        print("\n✓ Tool conversion test complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        if mcp_client.is_connected():
            await mcp_client.disconnect()
        return False


async def test_gemini_simple_message():
    """
    Test 2: Simple Message (No Tools)
    Test basic Gemini communication without MCP tools
    """
    print("\n" + "=" * 70)
    print("Test 2: Simple Gemini Message (No Tools)")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("GOOGLE_API_KEY"):
        print("⚠️  GOOGLE_API_KEY not set - skipping test")
        print("   Set it in .env or export GOOGLE_API_KEY=...")
        return False
    
    try:
        provider = GeminiProvider()
        print(f"✓ Gemini provider initialized: {provider}\n")
        
        # Simple question
        messages = [
            {"role": "user", "content": "What is 2+2? Answer in one word."}
        ]
        
        print("Sending message to Gemini...")
        response = await provider.create_message(messages)
        
        print(f"✓ Response received")
        print(f"  Text: {response.text}")
        print(f"  Stop reason: {response.stop_reason}")
        print(f"  Tokens: {response.usage['input_tokens']} in, {response.usage['output_tokens']} out")
        print(f"  Has function calls: {response.has_tool_calls}")
        
        await provider.close()
        print("\n✓ Simple message test complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_gemini_with_tools():
    """
    Test 3: Gemini with MCP Tools
    Test Gemini's ability to understand and call MCP tools
    """
    print("\n" + "=" * 70)
    print("Test 3: Gemini with MCP Tools")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("GOOGLE_API_KEY"):
        print("⚠️  GOOGLE_API_KEY not set - skipping test")
        return False
    
    # Setup
    config = MCPServerConfig.from_env()
    mcp_client = MCPClient(config)
    provider = GeminiProvider()
    
    try:
        await mcp_client.connect()
        print("✓ MCP server connected")
        
        # Get tools
        mcp_tools = await mcp_client.list_tools()
        print(f"✓ Retrieved {len(mcp_tools)} MCP tools\n")
        
        # Ask Gemini a question that should trigger tool use
        messages = [
            {
                "role": "user",
                "content": "What industries are available in the process simulator? Use the appropriate tool to find out."
            }
        ]
        
        print("Sending message to Gemini with tools...")
        response = await provider.create_message(
            messages=messages,
            tools=mcp_tools,
            system="You are a helpful assistant for process engineering. Use the provided tools to answer questions."
        )
        
        print(f"✓ Response received")
        print(f"  Stop reason: {response.stop_reason}")
        print(f"  Has function calls: {response.has_tool_calls}")
        
        if response.text:
            print(f"  Text: {response.text[:100]}...")
        
        if response.has_tool_calls:
            print(f"\n✓ Gemini requested {len(response.tool_calls)} function call(s):")
            for call in response.tool_calls:
                print(f"    Function: {call['name']}")
                print(f"    Args: {json.dumps(call['input'], indent=6)}")
                
                # Actually execute the tool
                print(f"\n  Executing {call['name']}...")
                result = await mcp_client.call_tool(call['name'], call['input'])
                result_data = json.loads(result)
                
                if 'industries' in result_data:
                    print(f"  ✓ Got {len(result_data['industries'])} industries")
                    for ind in result_data['industries']:
                        print(f"    - {ind['name']}")
                elif 'status' in result_data:
                    print(f"  ✓ Status: {result_data['status']}")
        else:
            print("  ⚠️  No function calls (Gemini might have answered without tools)")
        
        await mcp_client.disconnect()
        await provider.close()
        print("\n✓ Gemini with tools test complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        if mcp_client.is_connected():
            await mcp_client.disconnect()
        await provider.close()
        return False


async def test_streaming():
    """
    Test 4: Streaming Response
    Test real-time streaming from Gemini
    """
    print("\n" + "=" * 70)
    print("Test 4: Streaming Response")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("GOOGLE_API_KEY"):
        print("⚠️  GOOGLE_API_KEY not set - skipping test")
        return False
    
    try:
        provider = GeminiProvider()
        print(f"✓ Gemini provider initialized\n")
        
        messages = [
            {
                "role": "user",
                "content": "Count from 1 to 5, one number per line."
            }
        ]
        
        print("Starting streaming response...")
        print("─" * 50)
        
        text_received = ""
        chunk_count = 0
        
        async for chunk in provider.create_message_stream(messages):
            chunk_count += 1
            
            # Extract text from chunk
            if chunk.candidates and chunk.candidates[0].content:
                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        print(part.text, end="", flush=True)
                        text_received += part.text
        
        print("\n─" * 50)
        print(f"\n✓ Streaming test complete")
        print(f"  Chunks received: {chunk_count}")
        print(f"  Text length: {len(text_received)} characters")
        
        await provider.close()
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_gemini_simulation_workflow():
    """
    Test 5: Full Simulation Workflow with Gemini
    Test Gemini calling multiple tools in sequence
    """
    print("\n" + "=" * 70)
    print("Test 5: Gemini Simulation Workflow")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("GOOGLE_API_KEY"):
        print("⚠️  GOOGLE_API_KEY not set - skipping test")
        return False
    
    # Setup
    config = MCPServerConfig.from_env()
    mcp_client = MCPClient(config)
    provider = GeminiProvider()
    
    try:
        await mcp_client.connect()
        print("✓ MCP server connected")
        
        # Get tools
        mcp_tools = await mcp_client.list_tools()
        print(f"✓ Retrieved {len(mcp_tools)} MCP tools\n")
        
        # Multi-step question
        messages = [
            {
                "role": "user",
                "content": "I want to simulate a sugar factory. First, list the available processes for the sugar industry, then get details about the sugar_factory process."
            }
        ]
        
        print("Sending multi-step request to Gemini...")
        response = await provider.create_message(
            messages=messages,
            tools=mcp_tools,
            system="You are a process engineering assistant. Use tools step by step to help users."
        )
        
        print(f"✓ Response received")
        
        if response.has_tool_calls:
            print(f"\n✓ Gemini requested {len(response.tool_calls)} function call(s):")
            
            for idx, call in enumerate(response.tool_calls, 1):
                print(f"\n  Step {idx}: {call['name']}")
                print(f"  Args: {json.dumps(call['input'], indent=4)}")
                
                # Execute the tool
                result = await mcp_client.call_tool(call['name'], call['input'])
                print(f"  ✓ Executed successfully")
                
                # Show summary
                result_data = json.loads(result)
                if 'processes' in result_data:
                    print(f"    Found {len(result_data['processes'])} processes")
                elif 'display_name' in result_data:
                    print(f"    Process: {result_data['display_name']}")
                    print(f"    Equipment: {result_data['equipment_count']} units")
        
        if response.text:
            print(f"\n✓ Gemini's explanation:")
            print(f"  {response.text[:200]}...")
        
        await mcp_client.disconnect()
        await provider.close()
        print("\n✓ Simulation workflow test complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        if mcp_client.is_connected():
            await mcp_client.disconnect()
        await provider.close()
        return False


async def main():
    """Run all Gemini provider tests."""
    print("\n" + "=" * 70)
    print("GEMINI LLM PROVIDER TESTS")
    print("Testing Gemini API integration with MCP tools")
    print("=" * 70)
    
    results = []
    
    # Test 1: Tool conversion (always runs)
    results.append(("Tool Conversion", await test_tool_conversion()))
    
    # Test 2-5: Require GOOGLE_API_KEY
    if os.getenv("GOOGLE_API_KEY"):
        results.append(("Simple Message", await test_gemini_simple_message()))
        results.append(("Gemini with Tools", await test_gemini_with_tools()))
        results.append(("Streaming", await test_streaming()))
        results.append(("Simulation Workflow", await test_gemini_simulation_workflow()))
    else:
        print("\n" + "=" * 70)
        print("⚠️  GOOGLE_API_KEY not set")
        print("Set it to run full Gemini API tests:")
        print("  export GOOGLE_API_KEY='your-api-key'")
        print("  Or add to backend/.env file")
        print("=" * 70)
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{status} - {name}")
    
    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)
    
    print(f"\nTotal: {passed_count}/{total_count} tests passed")
    
    if passed_count == total_count:
        print("\n🎉 All tests passed!")
    elif passed_count > 0:
        print("\n⚠️  Some tests passed, check failures above")
    
    return passed_count == total_count


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
