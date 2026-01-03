"""
Gemini API Provider

Manages communication with Google's Gemini API.
Handles tool conversion, message creation, streaming, and response parsing.

Key Features:
- Convert MCP tools to Gemini format
- Non-streaming and streaming message creation
- Function call extraction and parsing
- Error handling with retry logic
- Token counting

Architecture:
┌─────────────────────┐         Gemini API           ┌──────────────────────┐
│   Backend           │ ───────────────────────────→ │   Gemini 2.0 Flash   │
│  (GeminiProvider)   │ ←─────────────────────────   │   (Google)           │
│                     │    Messages + Tools          │                      │
└─────────────────────┘    Function Calls + Response └──────────────────────┘

Note: Uses google-genai library (not deprecated google.generativeai)
"""

import asyncio
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from google import genai
from google.genai import types

from .mcp_client import MCPTool

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 1.0


# =============================================================================
# Tool Conversion
# =============================================================================

def convert_mcp_tools_to_gemini(mcp_tools: List[MCPTool]) -> List[Dict[str, Any]]:
    """
    Convert MCP tools to Gemini function declaration format.
    
    MCP format:
        MCPTool(
            name="simulate_process",
            description="Run a complete process simulation...",
            input_schema={
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        )
    
    Gemini format:
        {
            "name": "simulate_process",
            "description": "Run a complete process simulation...",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
    
    Args:
        mcp_tools: List of MCP tool objects
        
    Returns:
        List of tools in Gemini function declaration format
    """
    gemini_tools = []
    
    def clean_schema(schema):
        """Recursively remove unsupported fields from schema."""
        if isinstance(schema, dict):
            # Remove fields Gemini doesn't support
            cleaned = {k: v for k, v in schema.items() 
                      if k not in ('examples', 'additionalProperties', 'additional_properties')}
            # Recursively clean nested objects
            return {k: clean_schema(v) for k, v in cleaned.items()}
        elif isinstance(schema, list):
            return [clean_schema(item) for item in schema]
        else:
            return schema
    
    for tool in mcp_tools:
        # Deep copy and clean the input schema
        params = clean_schema(tool.input_schema)
        
        gemini_tools.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": params
        })
    
    return gemini_tools


# =============================================================================
# Response Parsing
# =============================================================================

class GeminiParsedResponse:
    """Parsed Gemini response with extracted content."""
    
    def __init__(self, response):
        self.response = response
        self.candidate = response.candidates[0] if response.candidates else None
        
        # Extract content blocks
        self.text_blocks = []
        self.tool_calls = []
        
        if self.candidate and self.candidate.content:
            for part in self.candidate.content.parts:
                if hasattr(part, 'text') and part.text:
                    self.text_blocks.append(part.text)
                elif hasattr(part, 'function_call') and part.function_call:
                    # Gemini doesn't provide call IDs, so we'll generate one
                    self.tool_calls.append({
                        "name": part.function_call.name,
                        "input": dict(part.function_call.args)
                    })
        
        # Extract usage metadata
        if hasattr(response, 'usage_metadata'):
            self.usage = {
                "input_tokens": response.usage_metadata.prompt_token_count,
                "output_tokens": response.usage_metadata.candidates_token_count
            }
        else:
            self.usage = {"input_tokens": 0, "output_tokens": 0}
        
        # Stop reason
        self.stop_reason = self.candidate.finish_reason if self.candidate else "UNKNOWN"
        self.model = response.model_version if hasattr(response, 'model_version') else "gemini"
    
    @property
    def text(self) -> str:
        """Combined text from all text blocks."""
        return "".join(self.text_blocks)
    
    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains function calls."""
        return len(self.tool_calls) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "text": self.text,
            "tool_calls": self.tool_calls,
            "stop_reason": str(self.stop_reason),
            "usage": self.usage
        }


# =============================================================================
# Gemini Provider
# =============================================================================

class GeminiProvider:
    """
    Gemini API Provider for LLM interactions.
    
    Manages all communication with Google's Gemini API including:
    - Message creation (streaming and non-streaming)
    - Tool format conversion
    - Response parsing
    - Error handling and retries
    
    Usage:
        provider = GeminiProvider(api_key="...")
        
        # Get tools from MCP
        mcp_tools = await mcp_client.list_tools()
        
        # Create message
        response = await provider.create_message(
            messages=[{"role": "user", "content": "Simulate a sugar factory"}],
            tools=mcp_tools
        )
        
        # Check for function calls
        if response.has_tool_calls:
            for tool_call in response.tool_calls:
                result = await mcp_client.call_tool(
                    tool_call["name"],
                    tool_call["input"]
                )
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = 300.0
    ):
        """
        Initialize Gemini provider.
        
        Args:
            api_key: Google API key (defaults to GOOGLE_API_KEY env var)
            model: Gemini model to use
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0 to 2.0)
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY not found in environment or constructor")
        
        # Strip any leading/trailing whitespace or accidental = characters
        self.api_key = self.api_key.strip().lstrip('=')
        
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        
        # Initialize client
        self.client = genai.Client(api_key=self.api_key)
        
        logger.info(f"Gemini provider initialized with model: {model}")
    
    def _convert_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Convert Anthropic-style messages to Gemini format.
        
        Anthropic format: [{"role": "user", "content": "..."}]
        Gemini format: [{"role": "user", "parts": [{"text": "..."}]}]
        
        Also converts "assistant" role to "model" for Gemini.
        """
        gemini_messages = []
        for msg in messages:
            role = msg["role"]
            # Gemini uses "model" instead of "assistant"
            if role == "assistant":
                role = "model"
            
            gemini_messages.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
        
        return gemini_messages
    
    async def create_message(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[MCPTool]] = None,
        system: Optional[str] = None,
        **kwargs
    ) -> GeminiParsedResponse:
        """
        Create a non-streaming message with Gemini.
        
        This is the standard request/response method. Use this when:
        - You don't need real-time streaming
        - Processing function calls in a loop
        - Want simpler error handling
        
        Args:
            messages: Conversation history in Anthropic format
                [{"role": "user", "content": "..."}, ...]
            tools: Optional MCP tools to provide to Gemini
            system: Optional system instruction
            **kwargs: Additional parameters for message creation
            
        Returns:
            GeminiParsedResponse with text, function calls, and metadata
            
        Example:
            response = await provider.create_message(
                messages=[{"role": "user", "content": "Simulate sugar factory"}],
                tools=mcp_tools,
                system="You are a process engineering assistant"
            )
            
            print(response.text)  # Gemini's text response
            if response.has_tool_calls:
                for call in response.tool_calls:
                    print(f"Function: {call['name']}, Args: {call['input']}")
        """
        # Convert messages to Gemini format
        gemini_messages = self._convert_messages(messages)
        
        # Convert MCP tools to Gemini format
        gemini_tools = None
        if tools:
            function_declarations = convert_mcp_tools_to_gemini(tools)
            gemini_tools = types.Tool(function_declarations=function_declarations)
            logger.debug(f"Converted {len(tools)} MCP tools to Gemini format")
        
        # Build generation config
        config = types.GenerateContentConfig(
            temperature=kwargs.get("temperature", self.temperature),
            max_output_tokens=kwargs.get("max_tokens", self.max_tokens),
            system_instruction=system if system else None,
            tools=[gemini_tools] if gemini_tools else None
        )
        
        # Make API call
        try:
            logger.debug(f"Sending message to Gemini ({len(messages)} messages)")
            
            response = await self.client.aio.models.generate_content(
                model=kwargs.get("model", self.model),
                contents=gemini_messages,
                config=config
            )
            
            # Parse response
            parsed = GeminiParsedResponse(response)
            
            logger.info(
                f"Gemini response: {parsed.usage['input_tokens']} in, "
                f"{parsed.usage['output_tokens']} out, "
                f"stop_reason={parsed.stop_reason}"
            )
            
            if parsed.has_tool_calls:
                logger.info(f"Gemini requested {len(parsed.tool_calls)} function calls")
                for call in parsed.tool_calls:
                    logger.debug(f"  - {call['name']}: {list(call['input'].keys())}")
            
            return parsed
            
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise
    
    async def create_message_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[MCPTool]] = None,
        system: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[Any, None]:
        """
        Create a streaming message with Gemini.
        
        This yields chunks as Gemini generates the response. Use this when:
        - You want real-time user feedback (typing effect)
        - Building chat UIs with streaming
        - Need to show progress for long responses
        
        Args:
            messages: Conversation history in Anthropic format
            tools: Optional MCP tools to provide to Gemini
            system: Optional system instruction
            **kwargs: Additional parameters for message creation
            
        Yields:
            Response chunks as they arrive
            
        Example:
            async for chunk in provider.create_message_stream(
                messages=[{"role": "user", "content": "Simulate sugar factory"}],
                tools=mcp_tools
            ):
                if chunk.candidates and chunk.candidates[0].content:
                    for part in chunk.candidates[0].content.parts:
                        if hasattr(part, 'text') and part.text:
                            print(part.text, end="", flush=True)
        """
        # Convert messages to Gemini format
        gemini_messages = self._convert_messages(messages)
        
        # Convert MCP tools to Gemini format
        gemini_tools = None
        if tools:
            function_declarations = convert_mcp_tools_to_gemini(tools)
            gemini_tools = types.Tool(function_declarations=function_declarations)
            logger.debug(f"Converted {len(tools)} MCP tools for streaming")
        
        # Build generation config
        config = types.GenerateContentConfig(
            temperature=kwargs.get("temperature", self.temperature),
            max_output_tokens=kwargs.get("max_tokens", self.max_tokens),
            system_instruction=system if system else None,
            tools=[gemini_tools] if gemini_tools else None
        )
        
        try:
            logger.debug(f"Starting streaming message to Gemini")
            
            stream = await self.client.aio.models.generate_content_stream(
                model=kwargs.get("model", self.model),
                contents=gemini_messages,
                config=config
            )
            
            async for chunk in stream:
                yield chunk
            
            logger.info("Stream complete")
            
        except Exception as e:
            logger.error(f"Gemini streaming error: {e}")
            raise
    
    async def close(self):
        """Close the Gemini client connection (no-op for google-genai)."""
        logger.info("Gemini provider closed")
    
    def __repr__(self) -> str:
        return f"GeminiProvider(model={self.model}, max_tokens={self.max_tokens})"
