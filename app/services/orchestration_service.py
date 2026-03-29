"""
Orchestration Service — Claude + HX Engine tool dispatch

Handles browser-based chat with full HX design tool support (Path 2):
1. User message → load conversation history
2. Call Claude with HX tools (hx_validate_requirements, hx_design)
3. If Claude calls a tool → dispatch to HX Engine → return tool_result
4. Loop until Claude produces a text response or max_turns reached
5. Stream text response back via SSE events

Tool dispatch loop:
  - hx_validate_requirements: validate → emit agent_text with result
  - hx_design: start design → emit hx_design_started (session_id + stream_url)
    so the frontend can open an EventSource to the HX Engine SSE stream
  - On engine down: emit app_error, stop loop gracefully
"""

import base64
import io
import logging
import traceback
from asyncio import CancelledError
from typing import Dict, Any, List, Optional
from datetime import datetime

from docx import Document as DocxDocument

from app.config import settings
from app.services.context_manager import ContextManager
from app.services.event_emitter import EventEmitter
from app.services.tool_registry import ToolRegistry
from app.core.engine_client import HXEngineClient
from app.core.llm_provider import ClaudeProvider

logger = logging.getLogger(__name__)

# Maximum number of recent messages to include in LLM context
MAX_RECENT_MESSAGES = 20

# Maximum tool call iterations per user turn (validate + design = 2; guard against loops)
MAX_TOOL_TURNS = 5

SYSTEM_PROMPT = """You are ARKEN AI, an expert heat exchanger design assistant.

You have access to the following tools:
- hx_validate_requirements: Validate HX design parameters for physical feasibility.
  ALWAYS call this first when a user provides HX design parameters.
- hx_design: Start the design pipeline. Call ONLY after hx_validate_requirements
  returns valid=true, passing the token it returned.

Workflow:
  1. Extract structured parameters from the user's natural-language request.
     If any required parameter is missing, ask for ALL missing ones in a single message.
  2. Call hx_validate_requirements with the extracted parameters.
     If valid=false: relay the specific error(s) to the user, ask for corrections, loop.
     If valid=true: IMMEDIATELY call hx_design in the same turn (do NOT wait for user).
  3. Call hx_design with the SAME parameters plus token=<token from step 2>.
     Design results stream to the right-hand panel automatically — do NOT describe
     or list the pipeline steps yourself. The user can see them live.

Required minimum parameters (do NOT call any tool until you have all of these):
  - hot_fluid_name and cold_fluid_name
  - T_hot_in_C and T_cold_in_C (inlet temperatures for both sides)
  - m_dot_hot_kg_s (hot-side mass flow rate)
  - The system MUST be fully determined for energy balance. That means you need
    BOTH of the following:
      a) T_hot_out_C  (hot-side outlet temperature)
      b) at least one of: T_cold_out_C OR m_dot_cold_kg_s
    Without (b), the cold side is underdetermined and the engine cannot compute
    fluid properties. If the user provides T_hot_out but neither T_cold_out nor
    m_dot_cold, you MUST ask for one of them before calling any tool.

After hx_design starts:
  - Say something brief like "Design started — watch the progress panel on the right."
  - Do NOT list the pipeline steps, do NOT describe what each step does,
    do NOT generate tables of steps. The frontend shows live step cards.
  - Keep the confirmation to 1–2 sentences max.

Be concise and engineering-focused. Use proper units (°C, kg/s, Pa, W/m²K).
"""

CANCEL_KEY_PREFIX = "cancel:"
CANCEL_KEY_TTL = 60  # seconds


class OrchestrationService:
    """
    Claude chatbot orchestration with HX Engine tool dispatch.

    Maintains conversation history via ContextManager and streams
    responses to the frontend via EventEmitter (Redis Streams → SSE).
    """

    def __init__(
        self,
        context_manager: ContextManager,
        event_emitter: EventEmitter,
        llm_provider: ClaudeProvider,
        redis_client=None,
        engine_client: HXEngineClient | None = None,
        tool_registry: ToolRegistry | None = None,
        anthropic_api_key: str = None,  # kept for interface compat, unused
    ):
        self.context_manager = context_manager
        self.event_emitter = event_emitter
        self.llm_provider = llm_provider
        self._redis = redis_client
        self._engine_client = engine_client
        self._tool_registry = tool_registry

    # ─────────────────────────────────────────────────────────────────────
    # Cancel support
    # ─────────────────────────────────────────────────────────────────────

    async def set_cancel_flag(self, conversation_id: str):
        if self._redis:
            await self._redis.setex(
                f"{CANCEL_KEY_PREFIX}{conversation_id}", CANCEL_KEY_TTL, "1"
            )

    async def _is_cancelled(self, conversation_id: str) -> bool:
        if not self._redis:
            return False
        return bool(await self._redis.get(f"{CANCEL_KEY_PREFIX}{conversation_id}"))

    # ─────────────────────────────────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────────────────────────────────

    async def process_message(
        self,
        conversation_id: str,
        user_message: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user message and stream Claude's response.

        If the user is requesting an HX design and tools are configured,
        runs the tool dispatch loop (validate → design → text response).
        Falls back to pure text chat when no engine is connected.
        """
        request_id = (metadata or {}).get("request_id", conversation_id)
        start_time = datetime.utcnow()
        full_response = ""
        input_tokens = 0
        output_tokens = 0

        try:
            # ── 1. Ensure conversation context ───────────────────────────
            context = await self.context_manager.get_context(conversation_id)
            if not context:
                context = await self.context_manager.create_context(
                    conversation_id, user_id=user_id
                )

            # ── 2. Save user message ─────────────────────────────────────
            user_content = self._build_user_content(user_message, attachments)
            msg_id = await self.context_manager.add_message(
                conversation_id, role="user", content=user_message,
            )
            if attachments:
                await self.context_manager.store_message_attachments(msg_id, attachments)

            # ── 3. Build Claude message list ─────────────────────────────
            messages = await self._build_llm_messages(conversation_id, user_content)

            # ── 4. Emit thinking start ───────────────────────────────────
            await self.event_emitter.emit_thinking_start(request_id=request_id)

            # ── 5. Tool dispatch loop ────────────────────────────────────
            tools = (
                self._tool_registry.get_tools_for_claude()
                if self._tool_registry
                else []
            )

            tool_turns = 0
            design_started = False  # set True after hx_design succeeds; drops tools from subsequent turns
            while tool_turns < MAX_TOOL_TURNS:
                if await self._is_cancelled(conversation_id):
                    raise CancelledError(f"Cancelled by user: {conversation_id}")

                # After hx_design succeeds, Claude only needs to write a confirmation
                # text response — don't offer tools again or it may re-validate.
                active_tools = tools if tools and not design_started else None

                # Call Claude (streaming to capture tool use + text)
                async with self.llm_provider.create_message_stream(
                    messages=messages,
                    system=SYSTEM_PROMPT,
                    tools=active_tools,
                ) as stream:
                    # Collect text deltas (may be empty when Claude only calls tools)
                    turn_text = ""
                    async for text in stream.text_stream:
                        if await self._is_cancelled(conversation_id):
                            raise CancelledError(f"Cancelled by user: {conversation_id}")
                        turn_text += text
                        full_response += text
                        await self.event_emitter.emit_message_delta(
                            request_id=request_id,
                            delta=text,
                            accumulated_length=len(full_response),
                        )

                    final_message = await stream.get_final_message()
                    input_tokens += final_message.usage.input_tokens
                    output_tokens += final_message.usage.output_tokens

                # Emit any intermediate text Claude produced alongside tool calls
                if turn_text.strip() and final_message.stop_reason == "tool_use":
                    await self.event_emitter.emit_agent_text(
                        request_id=request_id,
                        content=turn_text,
                        iteration=tool_turns,
                    )

                # No tool call — Claude is done
                if final_message.stop_reason != "tool_use":
                    break

                # ── Dispatch each tool call ──────────────────────────────
                tool_calls = [
                    b for b in final_message.content
                    if hasattr(b, "type") and b.type == "tool_use"
                ]

                # Build assistant turn (text + tool_use blocks) for message history
                assistant_turn_content = []
                if turn_text:
                    assistant_turn_content.append({"type": "text", "text": turn_text})
                for tc in tool_calls:
                    assistant_turn_content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                messages.append({"role": "assistant", "content": assistant_turn_content})

                # Execute all tool calls and build tool_result blocks
                tool_result_content = []
                engine_down = False

                for tc in tool_calls:
                    tool_result_str, engine_down = await self._dispatch_tool(
                        request_id=request_id,
                        tool_name=tc.name,
                        tool_input=tc.input,
                        user_id=user_id or "browser",
                    )
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": tool_result_str,
                    })

                    if tc.name == "hx_design" and not engine_down:
                        design_started = True

                    if engine_down:
                        break

                messages.append({"role": "user", "content": tool_result_content})
                tool_turns += 1

                if engine_down:
                    # Error already emitted in _dispatch_tool; stop the loop
                    break

            # ── 6. Emit thinking end + final message ─────────────────────
            elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            await self.event_emitter.emit_thinking_end(
                request_id=request_id, duration_ms=elapsed_ms,
            )
            await self.event_emitter.emit_message_final(
                request_id=request_id,
                content=full_response,
                role="assistant",
                metadata={"conversation_id": conversation_id},
            )

            # ── 7. Save assistant message ─────────────────────────────────
            token_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
            await self.context_manager.add_message(
                conversation_id, role="assistant", content=full_response,
                metadata={"token_usage": token_usage},
            )

            return {
                "status": "success",
                "message": full_response,
                "tool_calls": [],
                "tool_turns": tool_turns,
                "iterations": tool_turns + 1,
                "conversation_id": conversation_id,
                "token_usage": token_usage,
                "elapsed_ms": round(elapsed_ms, 1),
            }

        except CancelledError:
            elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            await self.event_emitter.emit_thinking_end(
                request_id=request_id, duration_ms=elapsed_ms,
            )
            if full_response.strip():
                existing_msgs = await self.context_manager.get_messages(conversation_id)
                last = existing_msgs[-1] if existing_msgs else None
                if (
                    last
                    and last.get("role") == "assistant"
                    and last.get("status") == "cancelled"
                ):
                    if len(full_response) > len(last.get("content", "")):
                        await self.context_manager.update_message(
                            conversation_id,
                            message_id=last["message_id"],
                            content=full_response,
                            metadata={"cancelled": True, "saved_by": "orchestration"},
                        )
                else:
                    await self.context_manager.add_message(
                        conversation_id, role="assistant", content=full_response,
                        status="cancelled", metadata={"cancelled": True},
                    )
            if self._redis:
                await self._redis.delete(f"{CANCEL_KEY_PREFIX}{conversation_id}")
            return {
                "status": "cancelled",
                "message": full_response,
                "tool_calls": [],
                "iterations": 0,
                "conversation_id": conversation_id,
                "token_usage": {},
            }

        except Exception as e:
            logger.error("Orchestration error: %s\n%s", e, traceback.format_exc())
            await self.event_emitter.emit_app_error(
                request_id=request_id,
                error_type="system_error",
                error_message=str(e),
                details={"exception_type": type(e).__name__},
                recoverable=True,
            )
            try:
                await self.context_manager.add_message(
                    conversation_id, role="assistant", content=f"Error: {e}",
                    status="error", metadata={"error": True, "error_type": type(e).__name__},
                )
            except Exception as save_err:
                logger.warning("Failed to save error message: %s", save_err)
            return {
                "status": "error",
                "message": f"Error: {e}",
                "tool_calls": [],
                "iterations": 0,
                "conversation_id": conversation_id,
                "token_usage": {},
            }

    # ─────────────────────────────────────────────────────────────────────
    # Tool dispatch
    # ─────────────────────────────────────────────────────────────────────

    async def _dispatch_tool(
        self,
        request_id: str,
        tool_name: str,
        tool_input: dict,
        user_id: str,
    ) -> tuple[str, bool]:
        """
        Dispatch a single tool call to the HX Engine.

        Returns:
            (tool_result_str, engine_down)
            engine_down=True means the engine is unreachable; the caller should
            stop the loop and not attempt further tool calls.
        """
        if not self._engine_client:
            return "HX Engine not configured. Cannot run design tools.", True

        try:
            if tool_name == "hx_validate_requirements":
                data = await self._engine_client.validate_requirements(
                    user_id=user_id, **tool_input
                )
                return self._format_validate_result(data), False

            elif tool_name == "hx_design":
                data = await self._engine_client.start_design(
                    user_id=user_id, **tool_input
                )
                session_id = data["session_id"]
                # Send the relative path as-is — the frontend resolves it via
                # VITE_HX_ENGINE_URL (dev) or nginx (prod).  Never prepend the
                # Docker-internal base_url here; the browser cannot reach it.
                stream_url = data["stream_url"]

                # Emit hx_design_started so the frontend can open the HX stream
                await self.event_emitter.emit_hx_design_started(
                    request_id=request_id,
                    session_id=session_id,
                    stream_url=stream_url,
                )

                return (
                    f"Design started. Session ID: {session_id}. "
                    f"The frontend is now streaming progress from the HX Engine."
                ), False

            else:
                return f"Unknown tool: {tool_name}", False

        except Exception as e:
            err_str = str(e)
            # Distinguish connection errors from other failures
            is_connect_error = "connect" in err_str.lower() or "connection" in err_str.lower()

            await self.event_emitter.emit_app_error(
                request_id=request_id,
                error_type="tool_error",
                error_message=(
                    "Cannot connect to the HX Engine. Please make sure it is running."
                    if is_connect_error
                    else f"HX Engine error: {err_str}"
                ),
                details={"tool": tool_name, "exception": err_str},
                recoverable=True,
            )
            return f"Tool error: {err_str}", is_connect_error

    @staticmethod
    def _format_validate_result(data: dict) -> str:
        """
        Convert the /api/v1/hx/requirements response dict into a Claude-readable string.
        Mirrors the format used by hx_mcp/server.py so Claude's tool-use behavior
        is consistent between MCP (Claude Desktop) and browser (Path 2).
        """
        if not data.get("valid"):
            errors = data.get("errors", [])
            lines = ["Requirements validation failed:\n"]
            for err in errors:
                field = err.get("field", "")
                message = err.get("message", "")
                suggestion = err.get("suggestion", "")
                valid_range = err.get("valid_range", "")
                lines.append(f"  • {field}: {message}")
                if valid_range:
                    lines.append(f"    Valid range: {valid_range}")
                if suggestion:
                    lines.append(f"    Suggestion: {suggestion}")
            lines.append("\nAsk the user to correct the above and try again.")
            return "\n".join(lines)

        token = data.get("token", "")
        user_message = data.get("user_message", "Requirements valid.")
        warnings = data.get("warnings", [])

        lines = [f"VALID — token: {token}", f"\n{user_message}"]
        if warnings:
            lines.append("\nNotes:")
            for w in warnings:
                lines.append(f"  • {w}")
        lines.append("\nPROCEED: call hx_design now with the same parameters and token above.")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _build_user_content(
        self,
        text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """Build user content for Claude. Returns string or multimodal block list."""
        if not attachments:
            return text

        DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        blocks = []
        for att in attachments:
            media_type = att.get("media_type", "")
            data = att.get("data", "")
            filename = att.get("filename", "file")

            if media_type.startswith("image/"):
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                })
            elif media_type == "application/pdf":
                blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                })
            elif media_type == DOCX_MIME:
                try:
                    raw = base64.b64decode(data)
                    doc = DocxDocument(io.BytesIO(raw))
                    extracted = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                    blocks.append({"type": "text", "text": f"[Document: {filename}]\n{extracted}"})
                except Exception as exc:
                    logger.warning("Failed to extract docx %s: %s", filename, exc)
                    blocks.append({"type": "text", "text": f"[Document: {filename} — could not extract: {exc}]"})

        if text:
            blocks.append({"type": "text", "text": text})

        return blocks if blocks else text

    async def _build_llm_messages(
        self,
        conversation_id: str,
        current_user_content: Any,
    ) -> List[Dict[str, Any]]:
        """Build the message list for Claude from conversation history."""
        history = await self.context_manager.get_messages(conversation_id)
        prior = history[:-1] if history else []
        recent = prior[-MAX_RECENT_MESSAGES:]

        messages = []
        for msg in recent:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": current_user_content})
        return messages
