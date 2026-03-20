"""
Claude API Provider

Manages communication with Anthropic's Claude API.
Handles tool conversion, message creation, streaming, and response parsing.

Key Features:
- Convert MCP tools to Anthropic format
- Non-streaming and streaming message creation
- Tool call extraction and parsing
- Error handling with exponential backoff retry
- Token counting and cost estimation

Architecture:
┌─────────────────────┐         Anthropic API         ┌──────────────────────┐
│   Backend           │ ───────────────────────────→  │   Claude 3.5 Sonnet  │
│  (ClaudeProvider)   │ ←─────────────────────────    │   (Anthropic)        │
│                     │    Messages + Tools           │                      │
└─────────────────────┘    Tool Calls + Responses     └──────────────────────┘
"""

import asyncio
import traceback
import httpx
from typing import Any, Dict, List, Optional, Union
from anthropic import Anthropic, AsyncAnthropic
from anthropic.types import Message
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming



# =============================================================================
# Configuration
# =============================================================================

# Model is always passed from settings.llm_model (single source of truth)
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 1.0


# =============================================================================
# Tool Conversion
# =============================================================================

def convert_tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert tool definitions to Anthropic tool format.
    
    Accepts dicts with 'name', 'description', 'input_schema' keys.
    
    Args:
        tools: List of tool definition dicts
        
    Returns:
        List of tools in Anthropic format
    """
    return [
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema", {"type": "object", "properties": {}})
        }
        for tool in tools
    ]


# =============================================================================
# Response Parsing
# =============================================================================

class ParsedResponse:
    """Parsed Claude response with extracted content."""
    
    def __init__(self, message: Message):
        self.message = message
        self.stop_reason = message.stop_reason
        self.model = message.model
        self.usage = message.usage
        
        # Extract content blocks
        self.text_blocks = []
        self.tool_calls = []
        
        for block in message.content:
            if block.type == "text":
                self.text_blocks.append(block.text)
            elif block.type == "tool_use":
                self.tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })
    
    @property
    def text(self) -> str:
        """Combined text from all text blocks."""
        return "".join(self.text_blocks)
    
    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "text": self.text,
            "tool_calls": self.tool_calls,
            "stop_reason": self.stop_reason,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens
            }
        }


# =============================================================================
# Message Conversion for Agentic Loop
# =============================================================================

def convert_messages_to_anthropic(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert conversation history from orchestration format to Anthropic API format.
    
    Orchestration format:
        - {"role": "user", "content": "..."}
        - {"role": "assistant", "tool_calls": [...]}
        - {"role": "tool", "tool_results": [...]}
    
    Anthropic format:
        - {"role": "user", "content": "..."}
        - {"role": "assistant", "content": [{"type": "tool_use", ...}]}
        - {"role": "user", "content": [{"type": "tool_result", ...}]}
    
    Args:
        messages: Conversation history in orchestration format
        
    Returns:
        Messages in Anthropic API format
    """
    import json
    import uuid
    
    converted = []
    # Track pending tool call IDs that need results
    # Each assistant message with tool_calls creates entries here
    # The next tool message consumes them in order
    pending_tool_ids = []  # List of (tool_name, tool_id) tuples
    
    for msg in messages:
        role = msg.get("role")
        
        if role == "user":
            # User message — can be plain text or multimodal (list of content blocks)
            content = msg.get("content", "")
            if isinstance(content, str) and not content.strip():
                continue
            if isinstance(content, list):
                # Already a list of content blocks (text + image)
                # Convert to Anthropic's expected format
                anthropic_blocks = []
                for block in content:
                    block_type = block.get("type")
                    if block_type == "text":
                        anthropic_blocks.append({"type": "text", "text": block["text"]})
                    elif block_type == "image":
                        # Anthropic expects: {"type": "image", "source": {"type": "base64", ...}}
                        anthropic_blocks.append({
                            "type": "image",
                            "source": block["source"]
                        })
                    else:
                        # Pass through unknown block types
                        anthropic_blocks.append(block)
                converted.append({"role": "user", "content": anthropic_blocks})
            elif isinstance(content, str):
                converted.append({"role": "user", "content": content})
            else:
                converted.append({"role": "user", "content": content})
                
        elif role == "assistant":
            # Check if this is a tool call message or text message
            if "tool_calls" in msg:
                # Convert tool calls to Anthropic tool_use format
                content_blocks = []
                tool_calls = msg["tool_calls"]
                
                # Step 1.3: Include any text content BEFORE tool calls
                # Claude can send text like "I'll validate your inputs..." along with tool calls
                text_content = msg.get("content", "")
                if text_content and isinstance(text_content, str) and text_content.strip():
                    content_blocks.append({
                        "type": "text",
                        "text": text_content
                    })
                
                for tc in tool_calls:
                    # Use the ID from the tool call if available, otherwise generate new one
                    tool_id = tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
                    tool_name = tc.get("name", "unknown")
                    tool_input = tc.get("input", tc.get("arguments", {}))
                    
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": tool_input
                    })
                    
                    # Queue this tool ID to be matched with its result
                    pending_tool_ids.append((tool_name, tool_id))
                
                converted.append({
                    "role": "assistant",
                    "content": content_blocks
                })
            else:
                # Regular text message - skip empty content
                content = msg.get("content", "")
                if isinstance(content, str) and not content.strip():
                    continue
                if isinstance(content, str):
                    converted.append({"role": "assistant", "content": content})
                else:
                    converted.append({"role": "assistant", "content": content})
                    
        elif role == "tool":
            # Convert tool results to Anthropic tool_result format
            # Tool results must be sent as a "user" message with tool_result content
            tool_results = msg.get("tool_results", [])
            content_blocks = []
            
            for tr in tool_results:
                tool_name = tr.get("name", "unknown")
                result = tr.get("result", {})
                
                # Find the matching tool ID from pending queue
                tool_id = None
                for i, (pending_name, pending_id) in enumerate(pending_tool_ids):
                    if pending_name == tool_name:
                        tool_id = pending_id
                        pending_tool_ids.pop(i)  # Remove from queue
                        break
                
                # If no matching ID found, generate a new one (shouldn't happen normally)
                if not tool_id:
                    tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
                
                # Convert result to string if it's a dict
                if isinstance(result, dict):
                    result_content = json.dumps(result)
                else:
                    result_content = str(result)
                
                # Check if this is an error
                is_error = result.get("status") == "error" if isinstance(result, dict) else False
                
                content_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_content,
                    "is_error": is_error
                })
            
            if content_blocks:
                converted.append({
                    "role": "user",
                    "content": content_blocks
                })
        
        elif role == "system":
            # System messages are handled separately in Anthropic API
            # Skip them here as they're passed as a separate parameter
            pass
    
    return converted


# =============================================================================
# Claude Provider
# =============================================================================

class ClaudeProvider:
    """
    Claude API Provider for LLM interactions.
    
    Manages all communication with Anthropic's Claude API including:
    - Message creation (streaming and non-streaming)
    - Tool format conversion
    - Response parsing
    - Error handling and retries
    
    Usage:
        provider = ClaudeProvider(api_key="sk-...")
        
        # Create message
        response = await provider.create_message(
            messages=[{"role": "user", "content": "Design a heat exchanger"}],
            system="You are a process engineering assistant"
        )
        
        print(response.text)  # Claude's text response
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-haiku-4-5",  # override via settings.llm_model
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        max_retries: int = 3,
        timeout: float = 300.0
    ):
        """
        Initialize Claude provider.
        
        Args:
            api_key: Anthropic API key (required)
            model: Claude model to use
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0 to 1.0)
            max_retries: Maximum retry attempts on failure
            timeout: Request timeout in seconds
        """
        if not api_key:
            raise ValueError("Anthropic API key is required")
        self.api_key = api_key
        
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        
        # Initialize async client with connection pool limits to prevent socket leak
        self.client = AsyncAnthropic(
            api_key=self.api_key,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=20,          # Max total connections
                    max_keepalive_connections=10, # Max idle keep-alive connections
                    keepalive_expiry=30,          # Close idle connections after 30s
                ),
                timeout=httpx.Timeout(timeout, connect=10.0),  # 10s connect timeout
            )
        )
    
    async def create_message(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        **kwargs
    ) -> ParsedResponse:
        """
        Create a non-streaming message with Claude.
        
        This is the standard request/response method. Use this when:
        - You don't need real-time streaming
        - Processing tool calls in a loop
        - Want simpler error handling
        
        Args:
            messages: Conversation history in Anthropic format
                [{"role": "user", "content": "..."}, ...]
            tools: Optional MCP tools to provide to Claude
            system: Optional system prompt
            **kwargs: Additional parameters for message creation
            
        Returns:
            ParsedResponse with text, tool calls, and metadata
            
        Example:
            response = await provider.create_message(
                messages=[{"role": "user", "content": "Simulate sugar factory"}],
                tools=mcp_tools,
                system="You are a process engineering assistant"
            )
            
            print(response.text)  # Claude's text response
            if response.has_tool_calls:
                for call in response.tool_calls:
                    print(f"Tool: {call['name']}, Args: {call['input']}")
        """
        # Convert tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = convert_tools_to_anthropic(tools)
        
        # Convert messages to Anthropic format (handles tool calls and results)
        anthropic_messages = convert_messages_to_anthropic(messages)
        
        # Build request parameters
        params: Dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if system:
            params["system"] = system
        
        if anthropic_tools:
            params["tools"] = anthropic_tools
        
        # Make API call with retry logic
        try:
            message = await self.client.messages.create(**params)
            
            # Parse response
            parsed = ParsedResponse(message)
            
            return parsed
            
        except Exception as e:
            print(f"ERROR: Claude API error: {e}")
            raise
    
    def create_message_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        **kwargs
    ):
        """
        Create a streaming message with Claude.
        
        Returns an async context manager (MessageStream) that provides:
        - stream.text_stream: async iterator yielding text chunks only
        - stream.get_final_message(): complete Message object after streaming
        
        Uses the Anthropic SDK's simplified streaming interface.
        
        Args:
            messages: Conversation history in Anthropic format
            tools: Optional MCP tools to provide to Claude
            system: Optional system prompt
            **kwargs: Additional parameters for message creation
            
        Returns:
            AsyncContextManager[MessageStream] — use with ``async with``
            
        Example:
            async with provider.create_message_stream(
                messages=[{"role": "user", "content": "Hello"}],
                system="You are a helpful assistant"
            ) as stream:
                async for text in stream.text_stream:
                    print(text, end="", flush=True)
                
                final = await stream.get_final_message()
                print(final.usage)
        """
        # Convert tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = convert_tools_to_anthropic(tools)
        
        # Convert messages to Anthropic format (handles tool calls and results)
        anthropic_messages = convert_messages_to_anthropic(messages)
        
        # Build request parameters
        params: Dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if system:
            params["system"] = system
        
        if anthropic_tools:
            params["tools"] = anthropic_tools
        
        # Return the stream context manager directly
        # Caller uses: async with provider.create_message_stream(...) as stream:
        return self.client.messages.stream(**params)
    
    async def close(self):
        """Close the Anthropic client connection."""
        await self.client.close()
    
    def __repr__(self) -> str:
        return f"ClaudeProvider(model={self.model}, max_tokens={self.max_tokens})"
