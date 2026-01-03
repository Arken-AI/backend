"""
Test MCP Client - Verify connection to MCP Process Server
"""
import asyncio
import sys
import os

from app.core.mcp_client import MCPClient, MCPServerConfig


async def test_mcp_connection():
    """Test 1: Connect to MCP server"""
    print("=" * 60)
    print("Test 1: MCP Connection")
    print("=" * 60)
    
    # Load config from environment
    os.environ['MCP_SERVER_COMMAND'] = '/Users/akashnikam/arken/calculation_engine/mcp_process_server/venv/bin/python'
    os.environ['MCP_SERVER_ARGS'] = '/Users/akashnikam/arken/calculation_engine/mcp_process_server/server.py'
    os.environ['MCP_SERVER_ENV_CALC_ENGINE_URL'] = 'http://localhost:8000'
    os.environ['MCP_SERVER_ENV_MAX_STORED_RUNS'] = '1000'
    os.environ['MCP_SERVER_ENV_MONGODB_URI'] = 'mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin'
    
    config = MCPServerConfig.from_env()
    client = MCPClient(config)
    
    try:
        await client.connect()
        print(f"✓ MCP client connected (state: {client.state})")
        print(f"✓ Process alive: {client._process is not None}")
        print(f"✓ Process PID: {client._process.pid if client._process else 'N/A'}")
        
        return client
    
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return None


async def test_list_tools(client: MCPClient):
    """Test 2: List available tools"""
    print("\n" + "=" * 60)
    print("Test 2: List Tools")
    print("=" * 60)
    
    try:
        tools = await client.list_tools()
        print(f"✓ Found {len(tools)} tools:")
        
        for tool in tools:
            print(f"  - {tool.name}: {tool.description[:60]}...")
        
        return True
    
    except Exception as e:
        print(f"✗ List tools failed: {e}")
        return False


async def test_call_tool(client: MCPClient):
    """Test 3: Call a simple tool (list_industries)"""
    print("\n" + "=" * 60)
    print("Test 3: Call Tool (list_industries)")
    print("=" * 60)
    
    try:
        result = await client.call_tool("list_industries", {})
        print(f"✓ Tool call successful")
        print(f"  Result: {result[:200]}..." if len(str(result)) > 200 else f"  Result: {result}")
        
        return True
    
    except Exception as e:
        print(f"✗ Tool call failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_health_check(client: MCPClient):
    """Test 4: Health check"""
    print("\n" + "=" * 60)
    print("Test 4: Health Check")
    print("=" * 60)
    
    try:
        healthy = await client.health_check()
        if healthy:
            print(f"✓ MCP server is healthy")
        else:
            print(f"✗ MCP server health check failed")
        
        return healthy
    
    except Exception as e:
        print(f"✗ Health check error: {e}")
        return False


async def test_disconnect(client: MCPClient):
    """Test 5: Graceful disconnect"""
    print("\n" + "=" * 60)
    print("Test 5: Disconnect")
    print("=" * 60)
    
    try:
        await client.disconnect()
        print(f"✓ Disconnected gracefully")
        print(f"✓ State: {client.state}")
        print(f"✓ Process cleaned up: {client._process is None}")
        
        return True
    
    except Exception as e:
        print(f"✗ Disconnect failed: {e}")
        return False


async def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("MCP Client Tests")
    print("=" * 60 + "\n")
    
    results = []
    client = None
    
    try:
        # Test 1: Connection
        client = await test_mcp_connection()
        results.append(client is not None)
        
        if not client:
            print("\n✗ Cannot proceed without connection")
            return
        
        # Test 2: List tools
        result = await test_list_tools(client)
        results.append(result)
        
        # Test 3: Call tool
        result = await test_call_tool(client)
        results.append(result)
        
        # Test 4: Health check
        result = await test_health_check(client)
        results.append(result)
        
        # Test 5: Disconnect
        result = await test_disconnect(client)
        results.append(result)
    
    except Exception as e:
        print(f"\n✗ Test suite error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Ensure cleanup
        if client and client.is_connected():
            await client.disconnect()
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All tests passed!")
    else:
        print(f"✗ {total - passed} test(s) failed")


if __name__ == "__main__":
    asyncio.run(main())
