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

import asyncio
import base64
import io
import logging
import traceback
from asyncio import CancelledError
from pathlib import Path
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

# ─────────────────────────────────────────────────────────────────────
# User-friendly error messages
# ─────────────────────────────────────────────────────────────────────

_CONNECTION_ERROR_MESSAGE = (
    "Unable to reach the AI service. "
    "Please check your internet connection and try again."
)

_RATE_LIMIT_ERROR_MESSAGE = (
    "The AI service is temporarily overloaded. "
    "Please wait a moment and try again."
)

_BILLING_ERROR_MESSAGE = (
    "The AI service is unavailable: the provider account is out of credits. "
    "Please contact your administrator to top up the Anthropic API balance."
)

_GENERIC_ERROR_MESSAGE = (
    "Something went wrong while processing your request. "
    "Please try again. If the problem persists, contact support."
)


def _user_friendly_error(exc: Exception) -> str:
    """Map raw exceptions to concise, user-facing messages."""
    type_name = type(exc).__name__
    if type_name in ("APIConnectionError", "ConnectError", "ConnectionError"):
        return _CONNECTION_ERROR_MESSAGE
    if type_name in ("RateLimitError",):
        return _RATE_LIMIT_ERROR_MESSAGE
    if type_name in ("AuthenticationError", "PermissionDeniedError"):
        return "AI service authentication failed. Please contact support."
    # Anthropic returns HTTP 400 BadRequestError when the workspace credit
    # balance is exhausted. Detect by message content since the SDK does not
    # expose a dedicated billing exception type.
    if type_name in ("BadRequestError",):
        msg = str(exc).lower()
        if "credit balance" in msg or "billing" in msg or "plans & billing" in msg:
            return _BILLING_ERROR_MESSAGE
    return _GENERIC_ERROR_MESSAGE


# Maximum number of recent messages to include in LLM context
MAX_RECENT_MESSAGES = 20

# Maximum tool call iterations per user turn (validate + design = 2; guard against loops)
MAX_TOOL_TURNS = 5

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
ORCHESTRATION_SKILL_FILE = "orchestration.md"


def _load_skill(filename: str) -> str:
        """Load a required backend orchestration skill file."""
        path = SKILLS_DIR / filename
        try:
                return path.read_text(encoding="utf-8").rstrip()
        except OSError as exc:
                logger.error("Could not load required backend skill file %s: %s", path, exc)
                raise

CANCEL_KEY_PREFIX = "cancel:"
CANCEL_KEY_TTL = 60  # seconds

# Design report generation
REPORT_LLM_TIMEOUT = 30.0  # seconds — fallback to template if LLM hangs
REPORT_MAX_TOKENS = 1024   # keep report concise

DESIGN_REPORT_PROMPT = """You are a senior heat exchanger engineer writing a brief design completion report.

Given the step-by-step design results below, write a concise narrative summary (~150 words max).

Rules:
- Start with "### Design Complete ✓" heading
- State key results in ONE line: duty, TEMA type, area, tube count × length, TEMA class
- List items needing attention (warnings, corrections) as numbered bullet points with ⚠
- State overall confidence percentage with brief explanation
- End with: "Expand any step in the panel for full calculation details."
- Do NOT repeat raw numbers that the user can see in the step cards
- Focus on WHY decisions were made and what risks exist
- Be engineering-focused, use proper units

Step results:
{step_data}
"""

DESIGN_FAILURE_PROMPT = """You are a senior heat exchanger engineer explaining why a design pipeline failed.

The pipeline halted at a step that could not be resolved after multiple escalation attempts.
You have access to ALL step data from the pipeline run, including the failed step and all
preceding steps that completed successfully.

Rules:
- Start with "### ⚠️ Design Pipeline Failed" heading
- State WHICH step failed and WHY in clear engineering terms
- Explain the root cause — what physical inconsistency or data error caused the failure
- Summarise the escalation attempts: what options were presented, what the user chose, and why it didn’t help
- List the steps that DID complete successfully and their key results (so the user knows what worked)
- Provide concrete recommendations for what to change before re-running
- Be specific about parameter values (include units) — the user is an engineer
- Keep it under 300 words
- End with: "You can modify the inputs and re-run the design from chat."

Step results (all steps including the failed one):
{step_data}

Escalation history for the failed step:
{escalation_data}
"""

# Fields to extract per step for the report prompt and follow-up Q&A
REPORT_KEY_FIELDS = [
    "Q_W", "U_W_m2K", "A_m2", "LMTD_K", "tema_type", "tema_class",
    "N_tubes", "tube_length_m", "tube_od_m", "shell_id_m",
    "overdesign_pct", "dP_shell", "dP_tube", "dP_shell_limit", "dP_tube_limit",
    "cost_usd", "vibration_safe", "flow_regime", "baffle_cut_pct",
    "confidence", "fouling_factor_tube", "fouling_factor_shell",
]


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

            # ── 3. Build Claude message list ─────────────────────────
            messages = await self._build_llm_messages(conversation_id, user_content)

            # ── 3b. Build system prompt (enriched with step data if present)
            system_prompt = await self._build_system_prompt(conversation_id)

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
                    system=system_prompt,
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
                        conversation_id=conversation_id,
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
            user_message = _user_friendly_error(e)
            await self.event_emitter.emit_app_error(
                request_id=request_id,
                error_type="system_error",
                error_message=user_message,
                details={"exception_type": type(e).__name__},
                recoverable=True,
            )
            try:
                await self.context_manager.add_message(
                    conversation_id, role="assistant", content=user_message,
                    status="error", metadata={"error": True, "error_type": type(e).__name__},
                )
            except Exception as save_err:
                logger.warning("Failed to save error message: %s", save_err)
            return {
                "status": "error",
                "message": user_message,
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
        conversation_id: str,
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

                # Persist step records to MongoDB once the pipeline finishes.
                # Runs in the background so it never blocks the chat response.
                # NOTE: conversation_id (not request_id) is used for MongoDB
                # persistence. request_id is ephemeral (per-request SSE stream);
                # conversation_id is the persistent key for context + messages.
                asyncio.create_task(
                    self._persist_hx_steps(
                        conversation_id=conversation_id,
                        session_id=session_id,
                    )
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

    async def _persist_hx_steps(
        self,
        conversation_id: str,
        session_id: str,
        poll_interval_s: float = 3.0,
        max_polls: int = 200,  # 10 minutes at 3s intervals
    ) -> None:
        """
        Background task: poll the HX Engine status endpoint until the pipeline
        sets is_complete=True, then:
        1. Persist step_records to MongoDB (survives Redis TTL expiry)
        2. Generate a design report (LLM narrative with template fallback)
        3. Save report to MongoDB context + add as assistant message

        The frontend picks up the report via context re-fetch after DESIGN_COMPLETE.
        """
        if not self._engine_client:
            return

        for _ in range(max_polls):
            await asyncio.sleep(poll_interval_s)
            try:
                status = await self._engine_client.get_design_status(session_id)
            except Exception as exc:
                logger.warning(
                    "_persist_hx_steps: status fetch for %s failed: %s — retrying",
                    session_id, exc,
                )
                continue

            is_complete = status.get("is_complete", False)
            pipeline_status = status.get("pipeline_status", "running")
            waiting_for_user = status.get("waiting_for_user", False)
            is_error = pipeline_status == "error"

            # Safety net: if the status endpoint didn't set waiting_for_user but
            # the last step record has ai_decision=ESCALATE, treat it as waiting.
            if not waiting_for_user and not is_complete and not is_error:
                step_records_check = status.get("step_records", [])
                if step_records_check and step_records_check[-1].get("ai_decision") == "ESCALATE":
                    waiting_for_user = True

            if not is_complete and not waiting_for_user and not is_error:
                continue  # pipeline still running, nothing to persist yet

            step_records = status.get("step_records", [])
            property_provenance = status.get("property_provenance")

            # ── Pipeline failed with error — generate failure report ────
            if is_error:
                escalation_history = status.get("escalation_history", {})
                # Persist step records so they survive Redis TTL
                try:
                    await self.context_manager.update_context(
                        conversation_id,
                        {
                            "hx_session_id": session_id,
                            "hx_steps": step_records,
                            "hx_waiting_for_user": False,
                            "hx_pipeline_status": "error",
                        },
                    )
                except Exception as exc:
                    logger.error(
                        "_persist_hx_steps: failed to persist error steps for %s: %s",
                        conversation_id, exc,
                    )

                # Generate a detailed failure report via LLM
                try:
                    report = await self._generate_failure_report(
                        step_records, escalation_history
                    )
                except Exception as exc:
                    logger.error(
                        "_persist_hx_steps: failure report generation crashed for %s: %s",
                        conversation_id, exc,
                    )
                    report = self._build_fallback_failure_report(step_records, escalation_history)

                # Save failure report as assistant message in chat
                try:
                    await self.context_manager.update_context(
                        conversation_id,
                        {"hx_design_report": report},
                    )
                    await self.context_manager.add_message(
                        conversation_id,
                        role="assistant",
                        content=report,
                        metadata={"type": "design_failure_report", "session_id": session_id},
                    )
                    logger.info(
                        "_persist_hx_steps: saved failure report for %s (%d chars)",
                        conversation_id, len(report),
                    )
                except Exception as exc:
                    logger.error(
                        "_persist_hx_steps: failed to save failure report for %s: %s",
                        conversation_id, exc,
                    )
                return

            # Persist partial state mid-pipeline when waiting for user input so that
            # a page refresh can restore the escalation card with options intact.
            if waiting_for_user and not is_complete:
                try:
                    await self.context_manager.update_context(
                        conversation_id,
                        {
                            "hx_session_id": session_id,
                            "hx_steps": step_records,
                            "hx_waiting_for_user": True,
                        },
                    )
                    logger.info(
                        "_persist_hx_steps: persisted %d step records (waiting_for_user) "
                        "for session %s → conversation %s",
                        len(step_records), session_id, conversation_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "_persist_hx_steps: failed to persist mid-pipeline steps for %s: %s",
                        conversation_id, exc,
                    )
                continue  # keep polling until is_complete

            # ── 1. Persist step records ──────────────────────────────────
            try:
                context_update = {
                    "hx_session_id": session_id,
                    "hx_steps": step_records,
                    "hx_waiting_for_user": False,  # pipeline complete, clear the flag
                }
                if property_provenance is not None:
                    context_update["hx_fluid_property_sources"] = property_provenance
                await self.context_manager.update_context(
                    conversation_id,
                    context_update,
                )
                logger.info(
                    "_persist_hx_steps: persisted %d step records "
                    "for session %s → conversation %s",
                    len(step_records), session_id, conversation_id,
                )
                if property_provenance is not None:
                    logger.info(
                        "_persist_hx_steps: persisted fluid_property_sources for session %s",
                        session_id,
                    )
            except Exception as exc:
                logger.error(
                    "_persist_hx_steps: failed to persist steps for %s: %s",
                    conversation_id, exc,
                )
                return  # can't generate report without saved steps

            # ── 2. Generate design report ────────────────────────────────
            try:
                report = await self._generate_design_report(step_records)
            except Exception as exc:
                logger.error(
                    "_persist_hx_steps: report generation crashed for %s: %s",
                    conversation_id, exc,
                )
                report = self._build_fallback_report(step_records)

            # ── 3. Save report to context + as assistant message ─────────
            try:
                await self.context_manager.update_context(
                    conversation_id,
                    {"hx_design_report": report},
                )
                await self.context_manager.add_message(
                    conversation_id,
                    role="assistant",
                    content=report,
                    metadata={"type": "design_report", "session_id": session_id},
                )
                logger.info(
                    "_persist_hx_steps: saved design report for %s (%d chars)",
                    conversation_id, len(report),
                )
            except Exception as exc:
                logger.error(
                    "_persist_hx_steps: failed to save report for %s: %s",
                    conversation_id, exc,
                )
            return

        # ── Timeout — pipeline never completed ───────────────────────────
        logger.warning(
            "_persist_hx_steps: timed out waiting for session %s to complete",
            session_id,
        )
        timeout_report = (
            "### Design Status\n\n"
            "The design pipeline did not complete within the expected time. "
            "Check the progress panel for the last known state.\n\n"
            "You can try running the design again if the issue persists."
        )
        try:
            await self.context_manager.update_context(
                conversation_id,
                {"hx_design_report": timeout_report},
            )
            await self.context_manager.add_message(
                conversation_id,
                role="assistant",
                content=timeout_report,
                metadata={"type": "design_report", "is_timeout": True, "session_id": session_id},
            )
        except Exception as exc:
            logger.error(
                "_persist_hx_steps: failed to save timeout report for %s: %s",
                conversation_id, exc,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Design report generation
    # ─────────────────────────────────────────────────────────────────────

    async def _generate_design_report(self, step_records: List[Dict[str, Any]]) -> str:
        """
        Generate a narrative design report from step records.

        Uses the LLM with a 30s timeout. Falls back to a deterministic
        template if the LLM call fails or times out.
        """
        step_data = self._format_steps_for_report(step_records)
        prompt = DESIGN_REPORT_PROMPT.format(step_data=step_data)

        try:
            response = await asyncio.wait_for(
                self.llm_provider.create_message(
                    messages=[{"role": "user", "content": prompt}],
                    system="You are a senior heat exchanger design engineer.",
                    max_tokens=REPORT_MAX_TOKENS,
                    temperature=0.3,
                ),
                timeout=REPORT_LLM_TIMEOUT,
            )
            report = response.text.strip()
            if report:
                return report
            # Empty response — fall through to template
            logger.warning("_generate_design_report: LLM returned empty response")
        except asyncio.TimeoutError:
            logger.warning(
                "_generate_design_report: LLM timed out after %.0fs — using fallback",
                REPORT_LLM_TIMEOUT,
            )
        except Exception as exc:
            logger.error(
                "_generate_design_report: LLM call failed: %s — using fallback", exc
            )

        return self._build_fallback_report(step_records)

    @staticmethod
    def _format_steps_for_report(step_records: List[Dict[str, Any]]) -> str:
        """
        Format step records into a compact text block for the LLM report prompt.
        Includes step name, AI decision, key outputs, and warnings.
        """
        lines = []
        for rec in step_records:
            step_id = rec.get("step_id", "?")
            step_name = rec.get("step_name", f"Step {step_id}")
            ai_decision = rec.get("ai_decision") or "PROCEED"
            duration = rec.get("duration_s") or 0

            lines.append(f"\n--- Step {step_id}: {step_name} [{ai_decision}] ({duration:.1f}s) ---")

            # Key outputs
            outputs = rec.get("outputs") or {}
            key_vals = [
                f"  {k}: {outputs[k]}"
                for k in REPORT_KEY_FIELDS
                if k in outputs and outputs[k] is not None
            ]
            if key_vals:
                lines.extend(key_vals)

            # AI review
            ai_review = rec.get("ai_review") or {}
            if ai_review.get("reasoning"):
                lines.append(f"  AI reasoning: {ai_review['reasoning'][:200]}")
            if ai_review.get("observation"):
                lines.append(f"  AI observation: {ai_review['observation'][:200]}")

            # Corrections
            corrections = ai_review.get("corrections") or []
            for corr in corrections[:3]:  # cap at 3
                lines.append(
                    f"  Correction: {corr.get('field', '?')} "
                    f"{corr.get('old_value', '?')} → {corr.get('new_value', '?')} "
                    f"({corr.get('reason', '')})"
                )

            # Warnings
            warnings = rec.get("warnings") or []
            for w in warnings[:3]:
                lines.append(f"  ⚠ {w}")

        return "\n".join(lines) if lines else "No step data available."

    @staticmethod
    def _build_fallback_report(step_records: List[Dict[str, Any]]) -> str:
        """
        Deterministic template-based report when LLM is unavailable.
        Extracts key values from step outputs and formats them directly.
        """
        # Merge all outputs (later steps overwrite earlier)
        all_outputs = {}
        all_warnings = []
        for rec in step_records:
            all_outputs.update(rec.get("outputs") or {})
            all_warnings.extend(rec.get("warnings") or [])
            ai_review = rec.get("ai_review") or {}
            if ai_review.get("observation"):
                all_warnings.append(ai_review["observation"])

        # Extract key results
        duty = all_outputs.get("Q_W")
        tema_type = all_outputs.get("tema_type", "N/A")
        area = all_outputs.get("A_m2")
        n_tubes = all_outputs.get("N_tubes")
        tube_length = all_outputs.get("tube_length_m")
        overdesign = all_outputs.get("overdesign_pct")

        lines = ["### Design Complete ✓\n"]

        # Key results line
        result_parts = []
        if duty is not None:
            result_parts.append(f"{duty:,.0f} W duty")
        result_parts.append(f"{tema_type} shell-and-tube")
        if area is not None:
            result_parts.append(f"{area:.1f} m² area")
        if n_tubes is not None and tube_length is not None:
            result_parts.append(f"{n_tubes} tubes × {tube_length:.2f} m")
        if result_parts:
            lines.append(f"**Key results:** {', '.join(result_parts)}.\n")

        if overdesign is not None:
            lines.append(f"**Overdesign:** {overdesign:.1f}%\n")

        # Warnings
        # Deduplicate while preserving order
        seen = set()
        unique_warnings = []
        for w in all_warnings:
            w_stripped = w.strip()
            if w_stripped and w_stripped not in seen:
                seen.add(w_stripped)
                unique_warnings.append(w_stripped)

        if unique_warnings:
            lines.append(f"**⚠ {len(unique_warnings)} item(s) need attention:**")
            for i, w in enumerate(unique_warnings[:5], 1):  # cap at 5
                lines.append(f"{i}. {w}")
            lines.append("")

        lines.append("Expand any step in the panel for full calculation details.")
        lines.append("Ask me anything about the design — I have all step data available.")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # Failure report generation (pipeline halted with unresolved error)
    # ─────────────────────────────────────────────────────────────────────

    async def _generate_failure_report(
        self,
        step_records: List[Dict[str, Any]],
        escalation_history: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        """
        Generate a narrative failure report from step records + escalation history.
        Uses the LLM with a 30s timeout. Falls back to a deterministic template
        if the LLM call fails or times out.
        """
        step_data = self._format_steps_for_report(step_records)
        escalation_data = self._format_escalation_history(escalation_history)
        prompt = DESIGN_FAILURE_PROMPT.format(
            step_data=step_data, escalation_data=escalation_data,
        )

        try:
            response = await asyncio.wait_for(
                self.llm_provider.create_message(
                    messages=[{"role": "user", "content": prompt}],
                    system="You are a senior heat exchanger design engineer diagnosing a pipeline failure.",
                    max_tokens=1500,
                    temperature=0.3,
                ),
                timeout=REPORT_LLM_TIMEOUT,
            )
            report = response.text.strip()
            if report:
                return report
            logger.warning("_generate_failure_report: LLM returned empty response")
        except asyncio.TimeoutError:
            logger.warning(
                "_generate_failure_report: LLM timed out after %.0fs — using fallback",
                REPORT_LLM_TIMEOUT,
            )
        except Exception as exc:
            logger.error(
                "_generate_failure_report: LLM call failed: %s — using fallback", exc
            )

        return self._build_fallback_failure_report(step_records, escalation_history)

    @staticmethod
    def _format_escalation_history(
        escalation_history: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        """Format escalation history into a readable text block for the LLM prompt."""
        if not escalation_history:
            return "No escalation attempts recorded."

        lines = []
        for step_key, attempts in escalation_history.items():
            lines.append(f"\nStep {step_key}:")
            for entry in attempts:
                attempt_num = entry.get("attempt", "?")
                options = entry.get("options", [])
                recommendation = entry.get("recommendation", "")
                user_chose = entry.get("user_chose", "")
                lines.append(f"  Attempt {attempt_num}:")
                if options:
                    for i, opt in enumerate(options):
                        lines.append(f"    Option {chr(65+i)}: {opt[:150]}")
                if recommendation:
                    lines.append(f"    AI recommendation: {recommendation[:150]}")
                lines.append(f"    User chose: {user_chose[:150]}")
        return "\n".join(lines)

    @staticmethod
    def _build_fallback_failure_report(
        step_records: List[Dict[str, Any]],
        escalation_history: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        """Deterministic failure report when LLM is unavailable."""
        # Find the failed step
        failed_step = None
        completed_steps = []
        for rec in step_records:
            decision = rec.get("ai_decision", "PROCEED")
            if decision in ("ESCALATE",):
                failed_step = rec
            else:
                completed_steps.append(rec)

        lines = ["### ⚠️ Design Pipeline Failed\n"]

        if failed_step:
            step_id = failed_step.get("step_id", "?")
            step_name = failed_step.get("step_name", f"Step {step_id}")
            lines.append(f"**Failed at:** Step {step_id} — {step_name}\n")

            ai_review = failed_step.get("ai_review") or {}
            reasoning = ai_review.get("reasoning", "")
            if reasoning:
                lines.append(f"**Root cause:** {reasoning}\n")

            # Escalation attempts
            step_key = str(step_id)
            attempts = escalation_history.get(step_key, [])
            if attempts:
                lines.append(f"**Escalation attempts ({len(attempts)}):**")
                for entry in attempts:
                    user_chose = entry.get("user_chose", "N/A")
                    lines.append(f"- Attempt {entry.get('attempt', '?')}: user chose \"{user_chose}\"")
                lines.append("")

            # Key outputs from failed step
            outputs = failed_step.get("outputs") or {}
            notable = {k: v for k, v in outputs.items()
                       if k in ("h_shell_W_m2K", "Re_shell", "G_s_kg_m2s", "mu_wall_Pa_s",
                                "kern_divergence_pct", "visc_correction") and v is not None}
            if notable:
                lines.append("**Failed step outputs:**")
                for k, v in notable.items():
                    lines.append(f"- {k}: {v}")
                lines.append("")

        if completed_steps:
            lines.append(f"**Completed steps ({len(completed_steps)}):** "
                         + ", ".join(f"Step {r.get('step_id', '?')}" for r in completed_steps))
            lines.append("")

        lines.append("**Recommendation:** Review the input parameters (fluid assignments, "
                     "fluid properties, geometry) and re-run the design.\n")
        lines.append("You can modify the inputs and re-run the design from chat.")

        return "\n".join(lines)

    @staticmethod
    def _build_step_digest(step_records: List[Dict[str, Any]]) -> str:
        """
        Build a compact step digest for system prompt injection.
        Only includes steps with non-PROCEED decisions to save tokens.
        PROCEED steps are omitted (nothing interesting happened).
        """
        lines = []
        for rec in step_records:
            decision = rec.get("ai_decision") or "PROCEED"
            # Skip routine PROCEED steps to keep token budget low
            if decision in ("PROCEED", "APPROVED", None):
                continue

            step_name = rec.get("step_name", f"Step {rec.get('step_id', '?')}")
            lines.append(f"- {step_name} [{decision}]")

            # Key outputs (3 max)
            outputs = rec.get("outputs") or {}
            key_vals = [
                f"    {k}: {outputs[k]}"
                for k in REPORT_KEY_FIELDS
                if k in outputs and outputs[k] is not None
            ][:3]
            lines.extend(key_vals)

            # AI observation/reasoning
            ai_review = rec.get("ai_review") or {}
            observation = ai_review.get("observation") or ai_review.get("reasoning", "")
            if observation:
                lines.append(f"    Note: {observation[:150]}")

            # Warnings
            for w in (rec.get("warnings") or [])[:2]:
                lines.append(f"    ⚠ {w[:100]}")

        if not lines:
            # All steps were PROCEED — include a summary of final outputs
            if step_records:
                last = step_records[-1]
                outputs = last.get("outputs") or {}
                summary_fields = ["Q_W", "A_m2", "tema_type", "overdesign_pct"]
                for k in summary_fields:
                    if k in outputs and outputs[k] is not None:
                        lines.append(f"- {k}: {outputs[k]}")
            return "All steps passed without issues.\n" + "\n".join(lines)

        return "\n".join(lines)

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

    async def _build_system_prompt(self, conversation_id: str) -> str:
        """
        Build the system prompt, enriched with step data when a design exists.

        When hx_steps are present in the conversation context, appends a
        compact step digest so Claude can answer follow-up questions about
        the design without the user having to re-state results.
        """
        context = await self.context_manager.get_context(conversation_id)
        orchestration_skill = _load_skill(ORCHESTRATION_SKILL_FILE)
        if not context:
            return orchestration_skill

        hx_steps = context.get("hx_steps", [])
        if not hx_steps:
            return orchestration_skill

        hx_session_id = context.get("hx_session_id", "unknown")
        step_digest = self._build_step_digest(hx_steps)

        return orchestration_skill + f"""

## Most recent design results
Session: {hx_session_id}
Completed steps: {len(hx_steps)}

Step summaries (non-routine steps only):
{step_digest}

Use this data to answer follow-up questions about the design.
Do not repeat raw numbers — explain the engineering reasoning.
"""
