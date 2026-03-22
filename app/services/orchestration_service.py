"""
Orchestration Service - Generic Claude Chatbot

Simple conversational AI orchestration:
1. User message → Load conversation history
2. Send message + history to Claude (streaming)
3. Stream response back via SSE events
4. Save to conversation history

No tools, no MCP, no agentic loop. Just clean conversational AI.
When HX Engine tools become available, they will be added here.
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
from app.core.llm_provider import ClaudeProvider

logger = logging.getLogger(__name__)

# Maximum number of recent messages to include in LLM context
MAX_RECENT_MESSAGES = 20

# System prompt for ARKEN AI assistant
SYSTEM_PROMPT = """You are ARKEN AI, an intelligent assistant for process engineering and simulation.

You are helpful, knowledgeable, and conversational. You can discuss:
- Chemical and process engineering concepts
- Equipment design (heat exchangers, reactors, distillation columns, etc.)
- Thermodynamics, fluid mechanics, and mass transfer
- Process simulation and optimization
- General science and engineering topics
- Any other topic the user wants to discuss

Be concise but thorough. Use proper engineering terminology when appropriate.
Format your responses with markdown for readability.
If you're unsure about something, say so rather than guessing.

You do NOT currently have access to simulation tools. If a user asks you to run a simulation,
let them know that simulation capabilities are coming soon and offer to help them with the
engineering concepts, equations, or methodology in the meantime."""


CANCEL_KEY_PREFIX = "cancel:"
CANCEL_KEY_TTL = 60  # seconds


class OrchestrationService:
    """
    Simple Claude chatbot orchestration.

    Maintains conversation history via ContextManager and streams
    responses to the frontend via EventEmitter (Redis Streams → SSE).
    """

    def __init__(
        self,
        context_manager: ContextManager,
        event_emitter: EventEmitter,
        llm_provider: ClaudeProvider,
        redis_client=None,
        anthropic_api_key: str = None,  # kept for interface compat, unused
    ):
        self.context_manager = context_manager
        self.event_emitter = event_emitter
        self.llm_provider = llm_provider
        self._redis = redis_client

    async def set_cancel_flag(self, conversation_id: str):
        """Set the cancellation flag for a conversation."""
        if self._redis:
            await self._redis.setex(
                f"{CANCEL_KEY_PREFIX}{conversation_id}", CANCEL_KEY_TTL, "1"
            )

    async def _is_cancelled(self, conversation_id: str) -> bool:
        if not self._redis:
            return False
        return bool(await self._redis.get(f"{CANCEL_KEY_PREFIX}{conversation_id}"))

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

        This is the main entry point called by chat.py. It:
        1. Ensures a conversation context exists
        2. Saves the user message
        3. Builds the LLM message history
        4. Streams Claude's response, emitting SSE events
        5. Saves the assistant message

        Args:
            conversation_id: Unique conversation identifier
            user_message: The user's message text
            user_id: Optional user identifier
            metadata: Optional request metadata (contains request_id)
            attachments: Optional file attachments (images, PDFs)

        Returns:
            Dict with status, message, conversation_id, token_usage, etc.
        """
        request_id = (metadata or {}).get("request_id", conversation_id)
        start_time = datetime.utcnow()

        try:
            # ── 1. Ensure conversation context exists ────────────────────
            context = await self.context_manager.get_context(conversation_id)
            if not context:
                context = await self.context_manager.create_context(
                    conversation_id, user_id=user_id
                )

            # ── 2. Save user message to history ──────────────────────────
            user_content = self._build_user_content(user_message, attachments)

            msg_id = await self.context_manager.add_message(
                conversation_id,
                role="user",
                content=user_message,  # Store plain text for display
            )

            # Persist attachment data in a dedicated collection so retries can
            # re-send files to the LLM without bloating the conversation document.
            if attachments:
                await self.context_manager.store_message_attachments(msg_id, attachments)

            # ── 3. Build messages for Claude ─────────────────────────────
            messages = await self._build_llm_messages(
                conversation_id, user_content
            )

            # ── 4. Emit thinking start ───────────────────────────────────
            await self.event_emitter.emit_thinking_start(
                request_id=request_id,
            )

            # ── 5. Stream Claude's response ──────────────────────────────
            full_response = ""
            input_tokens = 0
            output_tokens = 0

            async with self.llm_provider.create_message_stream(
                messages=messages,
                system=SYSTEM_PROMPT,
            ) as stream:
                # Simplified text streaming — SDK filters to just text chunks
                async for text in stream.text_stream:
                    if await self._is_cancelled(conversation_id):
                        logger.info("Request cancelled by user: %s", conversation_id)
                        raise CancelledError(f"Cancelled by user: {conversation_id}")
                    full_response += text
                    await self.event_emitter.emit_message_delta(
                        request_id=request_id,
                        delta=text,
                        accumulated_length=len(full_response),
                    )

                # Get the complete message for token usage
                final_message = await stream.get_final_message()
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens

            # ── 6. Emit thinking end + final message ─────────────────────
            elapsed_so_far = int(
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )
            await self.event_emitter.emit_thinking_end(
                request_id=request_id,
                duration_ms=elapsed_so_far,
            )
            await self.event_emitter.emit_message_final(
                request_id=request_id,
                content=full_response,
                role="assistant",
                metadata={"conversation_id": conversation_id},
            )

            # ── 7. Save assistant message to history ─────────────────────
            token_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }

            await self.context_manager.add_message(
                conversation_id,
                role="assistant",
                content=full_response,
                metadata={"token_usage": token_usage},
            )

            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            return {
                "status": "success",
                "message": full_response,
                "tool_calls": [],
                "iterations": 1,
                "conversation_id": conversation_id,
                "token_usage": token_usage,
                "elapsed_ms": round(elapsed_ms, 1),
            }

        except CancelledError:
            # ── User cancelled — save whatever was streamed so far ────
            logger.info(
                "Request cancelled by user: %s (partial_len=%d)",
                conversation_id,
                len(full_response),
            )

            # Emit thinking end so the frontend knows processing stopped
            elapsed_so_far = int(
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )
            await self.event_emitter.emit_thinking_end(
                request_id=request_id,
                duration_ms=elapsed_so_far,
            )

            # Save partial assistant message to DB so it persists across reloads.
            # The cancel endpoint may also try to save — check first to avoid dupes.
            # If the cancel endpoint already saved, update with the longer version
            # (backend may have accumulated more text than the frontend captured).
            if full_response.strip():
                existing_msgs = await self.context_manager.get_messages(conversation_id)
                last = existing_msgs[-1] if existing_msgs else None
                if (
                    last
                    and last.get("role") == "assistant"
                    and last.get("status") == "cancelled"
                ):
                    # Cancel endpoint already saved — update if ours is longer
                    if len(full_response) > len(last.get("content", "")):
                        await self.context_manager.update_message(
                            conversation_id,
                            message_id=last["message_id"],
                            content=full_response,
                            metadata={"cancelled": True, "saved_by": "orchestration"},
                        )
                else:
                    await self.context_manager.add_message(
                        conversation_id,
                        role="assistant",
                        content=full_response,
                        status="cancelled",
                        metadata={"cancelled": True},
                    )

            # Clear the cancel flag so it doesn't interfere with future requests
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
            logger.error(f"Orchestration error: {e}\n{traceback.format_exc()}")

            error_message = f"Error: {str(e)}"

            # Emit error event
            await self.event_emitter.emit_app_error(
                request_id=request_id,
                error_type="system_error",
                error_message=str(e),
                details={"exception_type": type(e).__name__},
                recoverable=True,
            )

            # Save error as an assistant message so it persists across reloads
            try:
                await self.context_manager.add_message(
                    conversation_id,
                    role="assistant",
                    content=error_message,
                    status="error",
                    metadata={
                        "error": True,
                        "error_type": type(e).__name__,
                    },
                )
            except Exception as save_err:
                logger.warning(
                    "Failed to save error message to DB: %s", save_err
                )

            return {
                "status": "error",
                "message": error_message,
                "tool_calls": [],
                "iterations": 0,
                "conversation_id": conversation_id,
                "token_usage": {},
            }

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _build_user_content(
        self,
        text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """
        Build user content for Claude. Returns a string for text-only,
        or a list of content blocks for multimodal (text + images/docs).

        Frontend sends: {media_type, data (base64), filename}
        """
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
                    blocks.append({
                        "type": "text",
                        "text": f"[Document: {filename}]\n{extracted}",
                    })
                except Exception as e:
                    logger.warning(f"Failed to extract text from docx {filename}: {e}")
                    blocks.append({
                        "type": "text",
                        "text": f"[Document: {filename} — could not extract text: {e}]",
                    })

        # Always include the user's text message
        if text:
            blocks.append({"type": "text", "text": text})

        return blocks if blocks else text

    async def _build_llm_messages(
        self,
        conversation_id: str,
        current_user_content: Any,
    ) -> List[Dict[str, Any]]:
        """
        Build the message list for Claude from conversation history.

        Retrieves recent messages from ContextManager, converts them,
        and appends the current user message.
        """
        history = await self.context_manager.get_messages(conversation_id)

        # Take only the most recent messages (excluding the one we just added)
        # We added the current user msg to storage already, so it's in history.
        # Use all but the last (current) message as history, then add current.
        prior = history[:-1] if history else []
        recent = prior[-MAX_RECENT_MESSAGES:]

        messages = []
        for msg in recent:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Append the current user message (may be multimodal)
        messages.append({"role": "user", "content": current_user_content})

        return messages
