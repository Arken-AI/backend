"""
Test Claude LLM Provider with MCP Tools

Tests the integration between Claude API and MCP tools.
Verifies tool conversion, message creation, and response parsing.
"""
import asyncio
import sys
import os
import json

from app.core import ClaudeProvider, MCPClient, MCPServerConfig


async def test_tool_conversion():
    """
    Test 1: MCP Tool → Anthropic Format Conversion
    Verify tools are properly converted for Claude
    """
    print("=" * 70)
    print("Test 1: Tool Conversion (MCP → Anthropic Format)")
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
        
        # Convert to Anthropic format
        from app.core.llm_provider import convert_mcp_tools_to_anthropic
        anthropic_tools = convert_mcp_tools_to_anthropic(mcp_tools)
        
        print(f"✓ Converted to {len(anthropic_tools)} Anthropic tools\n")
        
        # Verify structure
        tool = anthropic_tools[0]
        print(f"Sample tool structure:")
        print(f"  name: {tool['name']}")
        print(f"  description: {tool['description'][:60]}...")
        print(f"  input_schema keys: {list(tool['input_schema'].keys())}")
        
        # Verify all required fields
        for idx, tool in enumerate(anthropic_tools, 1):
            assert 'name' in tool, f"Tool {idx} missing 'name'"
            assert 'description' in tool, f"Tool {idx} missing 'description'"
            assert 'input_schema' in tool, f"Tool {idx} missing 'input_schema'"
            assert tool['input_schema'].get('type') == 'object', f"Tool {idx} invalid schema type"
        
        print(f"\n✓ All {len(anthropic_tools)} tools validated")
        
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


async def test_claude_simple_message():
    """
    Test 2: Simple Message (No Tools)
    Test basic Claude communication without MCP tools
    """
    print("\n" + "=" * 70)
    print("Test 2: Simple Claude Message (No Tools)")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY not set - skipping test")
        print("   Set it in .env or export ANTHROPIC_API_KEY=sk-...")
        return True  # Skip, not fail
    
    try:
        provider = ClaudeProvider()
        print(f"✓ Claude provider initialized: {provider}\n")
        
        # Simple question
        messages = [
            {"role": "user", "content": "What is 2+2? Answer in one word."}
        ]
        
        print("Sending message to Claude...")
        response = await provider.create_message(messages)
        
        print(f"✓ Response received")
        print(f"  Text: {response.text}")
        print(f"  Stop reason: {response.stop_reason}")
        print(f"  Tokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")
        print(f"  Has tool calls: {response.has_tool_calls}")
        
        await provider.close()
        print("\n✓ Simple message test complete!")
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_claude_with_tools():
    """
    Test 3: Claude with MCP Tools
    Test Claude's ability to understand and call MCP tools
    """
    print("\n" + "=" * 70)
    print("Test 3: Claude with MCP Tools")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY not set - skipping test")
        return True
    
    # Setup
    config = MCPServerConfig.from_env()
    mcp_client = MCPClient(config)
    provider = ClaudeProvider()
    
    try:
        await mcp_client.connect()
        print("✓ MCP server connected")
        
        # Get tools
        mcp_tools = await mcp_client.list_tools()
        print(f"✓ Retrieved {len(mcp_tools)} MCP tools\n")
        
        # Ask Claude a question that should trigger tool use
        messages = [
            {
                "role": "user",
                "content": "What industries are available in the process simulator? Use the appropriate tool to find out."
            }
        ]
        
        print("Sending message to Claude with tools...")
        response = await provider.create_message(
            messages=messages,
            tools=mcp_tools,
            system="You are a helpful assistant for process engineering. Use the provided tools to answer questions."
        )
        
        print(f"✓ Response received")
        print(f"  Stop reason: {response.stop_reason}")
        print(f"  Has tool calls: {response.has_tool_calls}")
        
        if response.text:
            print(f"  Text: {response.text[:100]}...")
        
        if response.has_tool_calls:
            print(f"\n✓ Claude requested {len(response.tool_calls)} tool call(s):")
            for call in response.tool_calls:
                print(f"    Tool: {call['name']}")
                print(f"    Args: {json.dumps(call['input'], indent=6)}")
                
                # Actually execute the tool
                print(f"\n  Executing {call['name']}...")
                result = await mcp_client.call_tool(call['name'], call['input'])
                result_data = json.loads(result)
                
                if 'industries' in result_data:
                    print(f"  ✓ Got {len(result_data['industries'])} industries")
                elif 'status' in result_data:
                    print(f"  ✓ Status: {result_data['status']}")
        else:
            print("  ⚠️  No tool calls (Claude might have answered without tools)")
        
        await mcp_client.disconnect()
        await provider.close()
        print("\n✓ Claude with tools test complete!")
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
    Test real-time streaming from Claude
    """
    print("\n" + "=" * 70)
    print("Test 4: Streaming Response")
    print("=" * 70)
    
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY not set - skipping test")
        return True
    
    try:
        provider = ClaudeProvider()
        print(f"✓ Claude provider initialized\n")
        
        messages = [
            {
                "role": "user",
                "content": "Count from 1 to 5, one number per line."
            }
        ]
        
        print("Starting streaming response...")
        print("─" * 50)
        
        text_received = ""
        event_count = 0
        
        async for event in provider.create_message_stream(messages):
            event_count += 1
            
            if event.type == "content_block_delta":
                if hasattr(event.delta, 'text'):
                    print(event.delta.text, end="", flush=True)
                    text_received += event.delta.text
            elif event.type == "message_start":
                print("[Stream started]")
            elif event.type == "message_stop":
                print("\n[Stream complete]")
        
        print("─" * 50)
        print(f"\n✓ Streaming test complete")
        print(f"  Events received: {event_count}")
        print(f"  Text length: {len(text_received)} characters")
        
        await provider.close()
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all LLM provider tests."""
    print("\n" + "=" * 70)
    print("CLAUDE LLM PROVIDER TESTS")
    print("Testing Claude API integration with MCP tools")
    print("=" * 70)
    
    results = []
    
    # Test 1: Tool conversion (always runs)
    results.append(("Tool Conversion", await test_tool_conversion()))
    
    # Test 2-4: Require ANTHROPIC_API_KEY
    if os.getenv("ANTHROPIC_API_KEY"):
        results.append(("Simple Message", await test_claude_simple_message()))
        results.append(("Claude with Tools", await test_claude_with_tools()))
        results.append(("Streaming", await test_streaming()))
    else:
        print("\n" + "=" * 70)
        print("⚠️  ANTHROPIC_API_KEY not set")
        print("Set it to run full Claude API tests:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
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
    
    return passed_count == total_count


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
