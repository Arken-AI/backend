"""
End-to-End Conversation Tests — Issue #2: Second Message Disappears

Tests the full conversation lifecycle across two messages to verify:
  1. conversation_id is stable and passed correctly on every request
  2. user_id is stored in context and available on message 2
  3. Message history accumulates — message 2 sees message 1 in LLM context
  4. SSE events are emitted with the correct request_id (= conversation_id)
  5. SSE after_sequence correctly skips events from previous messages
  6. Context is NOT reset between messages
  7. Two conversations don't bleed into each other

Run with:
    pytest tests/test_e2e_conversation.py -v -s

All external calls (Claude API, HX Engine, Redis, MongoDB) are mocked.
For storage tests that hit real Redis/Mongo, use the --integration flag:
    pytest tests/test_e2e_conversation.py -v -s -m integration
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.services.orchestration_service import OrchestrationService
from app.services.event_emitter import EventEmitter
from app.services.context_manager import ContextManager


# ─────────────────────────────────────────────────────────────────────────────
# Shared constants
# ─────────────────────────────────────────────────────────────────────────────

CONV_ID = "conv_test_abc123"
USER_ID = "user_test_xyz"
MSG1 = "What fluids are available for HX design?"
MSG2 = "Can you design a steam-water exchanger?"  # Second message in same conversation


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — mimic the real Claude API message objects
# ─────────────────────────────────────────────────────────────────────────────

def _make_final_message(stop_reason="end_turn", text="", input_tokens=10, output_tokens=20):
    """Build a mock Anthropic FinalMessage with a text content block."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    msg = MagicMock()
    msg.stop_reason = stop_reason
    msg.content = [text_block]
    msg.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return msg


def _make_streaming_context(text="OK", stop_reason="end_turn"):
    """
    Return a context manager that yields `text` from text_stream and
    resolves get_final_message() — matching the pattern used in OrchestrationService.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)

    async def _text_stream():
        yield text

    ctx.text_stream = _text_stream()
    ctx.get_final_message = AsyncMock(return_value=_make_final_message(stop_reason, text))
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Async Redis mock with counters for sequence tracking."""
    r = AsyncMock()
    r.incr = AsyncMock(side_effect=iter(range(1, 200)))  # 1, 2, 3, ...
    r.expire = AsyncMock(return_value=True)
    r.xadd = AsyncMock(return_value=b"1-0")
    r.xrange = AsyncMock(return_value=[])
    r.xlen = AsyncMock(return_value=0)
    r.delete = AsyncMock(return_value=1)
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock(return_value=True)
    return r


@pytest.fixture
def mock_context_manager():
    """
    ContextManager mock that simulates real accumulating state.
    Messages list grows as add_message is called, matching production behavior.
    """
    messages: list = []

    cm = AsyncMock()

    # get_context returns a dict with the accumulated messages
    async def _get_context(conv_id):
        return {"conversation_id": conv_id, "user_id": USER_ID, "messages": list(messages)}

    async def _add_message(_conv_id, role, content, metadata=None, status="complete"):
        msg_id = f"msg_{len(messages):04d}"
        messages.append({"message_id": msg_id, "role": role, "content": content, "status": status})
        return msg_id

    async def _get_messages(_conv_id):
        return list(messages)

    cm.get_context = AsyncMock(side_effect=_get_context)
    cm.create_context = AsyncMock(side_effect=_get_context)
    cm.add_message = AsyncMock(side_effect=_add_message)
    cm.get_messages = AsyncMock(side_effect=_get_messages)
    cm.store_message_attachments = AsyncMock()
    cm.update_message = AsyncMock(return_value=True)
    return cm, messages


@pytest.fixture
def mock_event_emitter():
    """EventEmitter mock that records every emission call."""
    ee = AsyncMock()
    ee.emit_thinking_start = AsyncMock(return_value=1)
    ee.emit_thinking_end = AsyncMock(return_value=2)
    ee.emit_message_delta = AsyncMock(return_value=3)
    ee.emit_message_final = AsyncMock(return_value=4)
    ee.emit_agent_text = AsyncMock(return_value=5)
    ee.emit_app_error = AsyncMock(return_value=6)
    ee.emit_hx_design_started = AsyncMock(return_value=7)
    return ee


@pytest.fixture
def mock_llm_provider():
    """
    ClaudeProvider mock returning a simple text response.
    Uses side_effect so each call gets a *fresh* streaming context — the async
    generator inside is exhausted after one iteration, so returning the same
    object on both calls would leave message 2 with an empty response.
    """
    provider = MagicMock()
    provider.create_message_stream = MagicMock(
        side_effect=lambda **_kw: _make_streaming_context("Sure, steam and water are both supported.")
    )
    return provider


@pytest.fixture
def orchestration_service(mock_context_manager, mock_event_emitter, mock_llm_provider):
    cm, _ = mock_context_manager
    return OrchestrationService(
        context_manager=cm,
        event_emitter=mock_event_emitter,
        llm_provider=mock_llm_provider,
        redis_client=None,  # No cancel support needed for these tests
        engine_client=None,
        tool_registry=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. conversation_id persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestConversationIdPersistence:
    """conversation_id must be the same object passed to every service call."""

    @pytest.mark.asyncio
    async def test_conversation_id_passed_to_get_context(self, orchestration_service, mock_context_manager):
        cm, _ = mock_context_manager
        await orchestration_service.process_message(
            conversation_id=CONV_ID,
            user_message=MSG1,
            user_id=USER_ID,
        )
        # ContextManager must receive the exact conversation_id
        cm.get_context.assert_awaited_with(CONV_ID)

    @pytest.mark.asyncio
    async def test_conversation_id_passed_to_add_message(self, orchestration_service, mock_context_manager):
        cm, _ = mock_context_manager
        await orchestration_service.process_message(
            conversation_id=CONV_ID,
            user_message=MSG1,
            user_id=USER_ID,
        )
        # Both user and assistant messages must carry the same conversation_id
        for add_call in cm.add_message.await_args_list:
            assert add_call.args[0] == CONV_ID, (
                f"add_message called with wrong conv_id: {add_call.args[0]!r}"
            )

    @pytest.mark.asyncio
    async def test_conversation_id_stable_across_two_messages(self, orchestration_service, mock_context_manager):
        cm, _ = mock_context_manager
        # Message 1
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        # Message 2
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        # Every single add_message call must use the same CONV_ID
        for add_call in cm.add_message.await_args_list:
            assert add_call.args[0] == CONV_ID


# ─────────────────────────────────────────────────────────────────────────────
# 2. user_id persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestUserIdPersistence:
    """user_id must survive in context and be reachable on message 2."""

    @pytest.mark.asyncio
    async def test_user_id_stored_in_context_on_message_1(self, orchestration_service, mock_context_manager):
        cm, _ = mock_context_manager
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        ctx = await cm.get_context(CONV_ID)
        assert ctx["user_id"] == USER_ID

    @pytest.mark.asyncio
    async def test_user_id_available_on_message_2(self, orchestration_service, mock_context_manager):
        cm, _ = mock_context_manager
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        ctx = await cm.get_context(CONV_ID)
        assert ctx["user_id"] == USER_ID, "user_id must not be wiped between messages"

    @pytest.mark.asyncio
    async def test_user_id_propagated_to_orchestration(self, mock_context_manager, mock_event_emitter):
        """user_id from metadata dict must be extracted and passed through."""
        cm, _ = mock_context_manager
        provider = MagicMock()
        provider.create_message_stream = MagicMock(
            return_value=_make_streaming_context("Hello!")
        )
        svc = OrchestrationService(
            context_manager=cm,
            event_emitter=mock_event_emitter,
            llm_provider=provider,
        )
        result = await svc.process_message(
            conversation_id=CONV_ID,
            user_message=MSG1,
            user_id=USER_ID,
            metadata={"user_id": USER_ID},
        )
        assert result["status"] == "success"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Message history accumulation
# ─────────────────────────────────────────────────────────────────────────────

class TestMessageHistoryAccumulation:
    """
    The LLM context for message 2 must include message 1 (user + assistant).
    A blank second message is the symptom of history being wiped or ignored.
    """

    @pytest.mark.asyncio
    async def test_message_1_appears_in_message_2_llm_context(
        self, orchestration_service, mock_context_manager
    ):
        cm, messages = mock_context_manager

        # Process message 1
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        # After message 1: history = [user_msg1, assistant_msg1]
        assert len(messages) == 2, f"Expected 2 messages after turn 1, got {len(messages)}"
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

        # Process message 2
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        # After message 2: history = [user_msg1, assistant_msg1, user_msg2, assistant_msg2]
        assert len(messages) == 4, f"Expected 4 messages after turn 2, got {len(messages)}"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == MSG2
        assert messages[3]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_llm_receives_prior_history_on_message_2(
        self, orchestration_service, mock_llm_provider
    ):
        """
        _build_llm_messages must pass the message-1 exchange to the LLM on turn 2.
        We verify by inspecting the `messages` argument in the second stream call.
        """
        # Capture the stream call arguments
        call_args_list = []
        original_stream = mock_llm_provider.create_message_stream

        def _capturing_stream(**kwargs):
            call_args_list.append(kwargs)
            return original_stream(**kwargs)

        mock_llm_provider.create_message_stream = MagicMock(side_effect=_capturing_stream)

        # Message 1
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        # Message 2
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )

        # The second stream call's messages list must contain message 1 history
        assert len(call_args_list) >= 2, "LLM should have been called at least twice"
        second_call_messages = call_args_list[1]["messages"]
        # Must have at least: [user_msg1, assistant_msg1, user_msg2]
        assert len(second_call_messages) >= 3, (
            f"Message 2 LLM context only had {len(second_call_messages)} entries; "
            f"expected at least 3 (user1, assistant1, user2)"
        )
        roles = [m["role"] for m in second_call_messages]
        assert roles[0] == "user"
        assert roles[1] == "assistant"
        assert roles[-1] == "user"

    @pytest.mark.asyncio
    async def test_context_not_wiped_between_messages(self, orchestration_service, mock_context_manager):
        """
        get_context must NOT return None or an empty messages list on the second call.
        This is the direct check for the 'messages array replaced instead of appended' bug.
        """
        cm, messages = mock_context_manager

        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        snapshot_after_msg1 = len(messages)

        # Simulate what happens on message 2 startup — get_context must return prior state
        ctx = await cm.get_context(CONV_ID)
        assert ctx is not None, "Context must not be None before message 2"
        assert len(ctx["messages"]) == snapshot_after_msg1, (
            "Context messages were wiped between messages — this is the root cause of Issue 2"
        )

    @pytest.mark.asyncio
    async def test_get_messages_returns_full_history(self, orchestration_service, mock_context_manager):
        cm, _ = mock_context_manager
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        history = await cm.get_messages(CONV_ID)
        assert len(history) == 4
        contents = [m["content"] for m in history]
        assert MSG1 in contents
        assert MSG2 in contents


# ─────────────────────────────────────────────────────────────────────────────
# 4. SSE event emission — request_id alignment
# ─────────────────────────────────────────────────────────────────────────────

class TestSSEEventEmission:
    """
    Events are keyed by request_id in Redis.
    request_id defaults to conversation_id (from metadata fallback in orchestration_service.py:145).
    The SSE stream endpoint reads events:{conversation_id}.
    These two must align or the frontend receives no events.
    """

    @pytest.mark.asyncio
    async def test_thinking_start_emitted_for_message_1(self, orchestration_service, mock_event_emitter):
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        mock_event_emitter.emit_thinking_start.assert_awaited_once()
        call_kwargs = mock_event_emitter.emit_thinking_start.await_args
        # request_id must be conversation_id (because metadata has no request_id key)
        assert call_kwargs.kwargs.get("request_id") == CONV_ID or call_kwargs.args[0] == CONV_ID

    @pytest.mark.asyncio
    async def test_thinking_end_emitted_for_message_1(self, orchestration_service, mock_event_emitter):
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        mock_event_emitter.emit_thinking_end.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_message_final_emitted_with_conversation_id(self, orchestration_service, mock_event_emitter):
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        mock_event_emitter.emit_message_final.assert_awaited_once()
        args = mock_event_emitter.emit_message_final.await_args
        # metadata must carry conversation_id so the frontend can route the final message
        metadata = args.kwargs.get("metadata", {}) or (args.args[3] if len(args.args) > 3 else {})
        assert metadata.get("conversation_id") == CONV_ID

    @pytest.mark.asyncio
    async def test_events_emitted_for_both_messages(self, orchestration_service, mock_event_emitter):
        """thinking_start + thinking_end should fire once per message."""
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        assert mock_event_emitter.emit_thinking_start.await_count == 2, (
            "emit_thinking_start must be called once per message"
        )
        assert mock_event_emitter.emit_thinking_end.await_count == 2, (
            "emit_thinking_end must be called once per message"
        )
        assert mock_event_emitter.emit_message_final.await_count == 2

    @pytest.mark.asyncio
    async def test_request_id_is_conversation_id_when_metadata_empty(
        self, orchestration_service, mock_event_emitter
    ):
        """
        If no request_id in metadata, orchestration_service falls back to conversation_id.
        The SSE stream reads events:{conversation_id} — these must be the same key.
        """
        await orchestration_service.process_message(
            conversation_id=CONV_ID,
            user_message=MSG1,
            user_id=USER_ID,
            metadata={},  # No request_id — must fall back to CONV_ID
        )
        start_call = mock_event_emitter.emit_thinking_start.await_args
        actual_request_id = start_call.kwargs.get("request_id") or start_call.args[0]
        assert actual_request_id == CONV_ID, (
            f"request_id={actual_request_id!r} but SSE stream reads events:{CONV_ID}. "
            f"Mismatch causes the frontend to receive no events for this message."
        )

    @pytest.mark.asyncio
    async def test_request_id_respects_metadata_override(self, mock_context_manager, mock_event_emitter):
        """
        If the frontend sends metadata.request_id, events go to events:{request_id}.
        The SSE stream must use the same ID — this test documents the contract.
        """
        cm, _ = mock_context_manager
        provider = MagicMock()
        provider.create_message_stream = MagicMock(
            return_value=_make_streaming_context("Hello!")
        )
        svc = OrchestrationService(
            context_manager=cm,
            event_emitter=mock_event_emitter,
            llm_provider=provider,
        )
        custom_req_id = "req_custom_99"
        await svc.process_message(
            conversation_id=CONV_ID,
            user_message=MSG1,
            user_id=USER_ID,
            metadata={"request_id": custom_req_id},
        )
        start_call = mock_event_emitter.emit_thinking_start.await_args
        actual_request_id = start_call.kwargs.get("request_id") or start_call.args[0]
        assert actual_request_id == custom_req_id, (
            "When metadata.request_id is set, all events must use that ID — "
            "the SSE stream must connect to events:{custom_req_id}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. SSE event ordering and sequence
# ─────────────────────────────────────────────────────────────────────────────

class TestSSEEventOrdering:
    """
    Sequence numbers must be monotonically increasing within a request_id.
    after_sequence filtering must skip events from prior messages.
    """

    @pytest.mark.asyncio
    async def test_event_emitter_sequence_increments(self, mock_redis):
        """Each _emit_event call gets the next integer from Redis INCR."""
        mock_redis.incr = AsyncMock(side_effect=[1, 2, 3, 4])
        emitter = EventEmitter(mock_redis)

        seq1 = await emitter.emit_thinking_start(request_id=CONV_ID)
        seq2 = await emitter.emit_message_delta(
            request_id=CONV_ID, delta="Hello", accumulated_length=5
        )
        seq3 = await emitter.emit_message_final(
            request_id=CONV_ID, content="Hello world"
        )
        seq4 = await emitter.emit_thinking_end(request_id=CONV_ID, duration_ms=500)

        assert seq1 == 1
        assert seq2 == 2
        assert seq3 == 3
        assert seq4 == 4

    @pytest.mark.asyncio
    async def test_event_stream_key_uses_request_id(self, mock_redis):
        """Events must be written to events:{request_id} in Redis."""
        mock_redis.incr = AsyncMock(return_value=1)
        emitter = EventEmitter(mock_redis)
        await emitter.emit_thinking_start(request_id=CONV_ID)

        xadd_call = mock_redis.xadd.await_args
        stream_key = xadd_call.args[0]
        assert stream_key == f"events:{CONV_ID}", (
            f"Events written to {stream_key!r} but SSE reads events:{CONV_ID}"
        )

    @pytest.mark.asyncio
    async def test_get_events_filters_by_after_sequence(self, mock_redis):
        """
        after_sequence=N must return only events with sequence > N.
        This is how message 2's SSE stream avoids replaying message 1's events.

        Each event type must include its required Pydantic fields, otherwise
        from_redis_dict raises a validation error and the event is silently
        skipped — causing the test to see fewer events than expected.
        """
        def _make_redis_entry(seq: int, event_type: str, extra: dict = None):
            payload = {
                "event_type": event_type,
                "request_id": CONV_ID,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence": seq,
            }
            if extra:
                payload.update(extra)
            event_data = json.dumps(payload)
            return (f"{seq}-0".encode(), {"event_type": event_type, "data": event_data})

        # Simulate 6 events: seq 1-3 from message 1, seq 4-6 from message 2.
        # Include required fields for each concrete event type.
        all_entries = [
            _make_redis_entry(1, "thinking_start"),
            _make_redis_entry(2, "message_delta", {"delta": "Hi", "accumulated_length": 2}),
            _make_redis_entry(3, "thinking_end", {"duration_ms": 100}),
            _make_redis_entry(4, "thinking_start"),
            _make_redis_entry(5, "message_delta", {"delta": "There", "accumulated_length": 7}),
            _make_redis_entry(6, "thinking_end", {"duration_ms": 200}),
        ]
        mock_redis.xrange = AsyncMock(return_value=all_entries)
        emitter = EventEmitter(mock_redis)

        # after_sequence=3 → should return only events 4, 5, 6
        events = await emitter.get_events(CONV_ID, after_sequence=3)
        assert len(events) == 3, f"Expected 3 events after seq=3, got {len(events)}"
        seqs = [e.sequence for e in events]
        assert seqs == [4, 5, 6]

    @pytest.mark.asyncio
    async def test_thinking_start_before_thinking_end(self, orchestration_service, mock_event_emitter):
        """thinking_start must be emitted before thinking_end (never reversed)."""
        emission_order = []
        mock_event_emitter.emit_thinking_start.side_effect = (
            lambda **_kw: emission_order.append("start") or 1
        )
        mock_event_emitter.emit_thinking_end.side_effect = (
            lambda **_kw: emission_order.append("end") or 2
        )

        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )

        assert emission_order.index("start") < emission_order.index("end"), (
            "thinking_start must precede thinking_end"
        )

    @pytest.mark.asyncio
    async def test_message_final_emitted_after_thinking_end(
        self, orchestration_service, mock_event_emitter
    ):
        emission_order = []
        mock_event_emitter.emit_thinking_end.side_effect = (
            lambda **_kw: emission_order.append("thinking_end") or 2
        )
        mock_event_emitter.emit_message_final.side_effect = (
            lambda **_kw: emission_order.append("message_final") or 4
        )

        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )

        assert "thinking_end" in emission_order
        assert "message_final" in emission_order
        assert emission_order.index("thinking_end") < emission_order.index("message_final"), (
            "thinking_end must come before message_final"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Context isolation between conversations
# ─────────────────────────────────────────────────────────────────────────────

class TestConversationIsolation:
    """Two different conversations must never share state."""

    @pytest.mark.asyncio
    async def test_two_conversations_do_not_bleed(self, mock_event_emitter):
        """
        Messages added to conv_A must not appear in conv_B's history.
        """
        # Two fully independent context stores
        messages_a: list = []
        messages_b: list = []

        async def _get_context_a(cid):
            return {"conversation_id": cid, "user_id": "user_A", "messages": list(messages_a)}

        async def _get_context_b(cid):
            return {"conversation_id": cid, "user_id": "user_B", "messages": list(messages_b)}

        async def _add_message_a(_cid, role, content, **_kw):
            messages_a.append({"role": role, "content": content})
            return f"msg_{len(messages_a)}"

        async def _add_message_b(_cid, role, content, **_kw):
            messages_b.append({"role": role, "content": content})
            return f"msg_{len(messages_b)}"

        cm_a = AsyncMock()
        cm_a.get_context = AsyncMock(side_effect=_get_context_a)
        cm_a.create_context = AsyncMock(side_effect=_get_context_a)
        cm_a.add_message = AsyncMock(side_effect=_add_message_a)
        cm_a.get_messages = AsyncMock(side_effect=lambda _: list(messages_a))
        cm_a.store_message_attachments = AsyncMock()

        cm_b = AsyncMock()
        cm_b.get_context = AsyncMock(side_effect=_get_context_b)
        cm_b.create_context = AsyncMock(side_effect=_get_context_b)
        cm_b.add_message = AsyncMock(side_effect=_add_message_b)
        cm_b.get_messages = AsyncMock(side_effect=lambda _: list(messages_b))
        cm_b.store_message_attachments = AsyncMock()

        def _make_svc(cm):
            p = MagicMock()
            p.create_message_stream = MagicMock(
                return_value=_make_streaming_context("Resp")
            )
            return OrchestrationService(context_manager=cm, event_emitter=mock_event_emitter, llm_provider=p)

        svc_a = _make_svc(cm_a)
        svc_b = _make_svc(cm_b)

        await svc_a.process_message("conv_A", "Hello from A", user_id="user_A")
        await svc_b.process_message("conv_B", "Hello from B", user_id="user_B")

        assert len(messages_a) == 2  # user + assistant
        assert len(messages_b) == 2
        # No cross-contamination
        a_contents = [m["content"] for m in messages_a]
        b_contents = [m["content"] for m in messages_b]
        assert "Hello from B" not in a_contents
        assert "Hello from A" not in b_contents


# ─────────────────────────────────────────────────────────────────────────────
# 7. Full two-turn round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestTwoTurnRoundTrip:
    """
    Integration-style test (all mocked, no real I/O) for the complete scenario
    described in Issue 2: first message works, second message must also work.
    """

    @pytest.mark.asyncio
    async def test_two_messages_both_return_success(self, orchestration_service):
        result1 = await orchestration_service.process_message(
            conversation_id=CONV_ID,
            user_message=MSG1,
            user_id=USER_ID,
            metadata={"user_id": USER_ID},
        )
        assert result1["status"] == "success", f"Message 1 failed: {result1}"
        assert result1["conversation_id"] == CONV_ID

        result2 = await orchestration_service.process_message(
            conversation_id=CONV_ID,
            user_message=MSG2,
            user_id=USER_ID,
            metadata={"user_id": USER_ID},
        )
        assert result2["status"] == "success", f"Message 2 failed: {result2}"
        assert result2["conversation_id"] == CONV_ID

    @pytest.mark.asyncio
    async def test_message_2_response_is_not_blank(self, orchestration_service):
        """The 'goes blank' bug manifests as an empty response string."""
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        result2 = await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        assert result2["message"], (
            "Message 2 response is blank — this is the exact symptom of Issue 2. "
            "Check that the streaming context manager yields text for both messages."
        )

    @pytest.mark.asyncio
    async def test_history_has_four_entries_after_two_turns(
        self, orchestration_service, mock_context_manager
    ):
        _, messages = mock_context_manager
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        assert len(messages) == 4, (
            f"Expected [user1, assistant1, user2, assistant2] = 4 entries, "
            f"got {len(messages)}: {[m['role'] for m in messages]}"
        )

    @pytest.mark.asyncio
    async def test_event_count_doubles_on_second_message(
        self, orchestration_service, mock_event_emitter
    ):
        """Both messages emit the full thinking+final event set."""
        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        count_after_msg1 = mock_event_emitter.emit_thinking_start.await_count

        await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        count_after_msg2 = mock_event_emitter.emit_thinking_start.await_count

        assert count_after_msg2 == count_after_msg1 + 1, (
            "emit_thinking_start should fire once more for message 2"
        )

    @pytest.mark.asyncio
    async def test_tool_turns_counter_resets_per_message(self, orchestration_service):
        """tool_turns in the result must reflect only the current message's turns."""
        result1 = await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG1, user_id=USER_ID,
        )
        result2 = await orchestration_service.process_message(
            conversation_id=CONV_ID, user_message=MSG2, user_id=USER_ID,
        )
        # For a plain text response (no tools), tool_turns = 0
        assert result1.get("tool_turns", 0) == 0
        assert result2.get("tool_turns", 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 8. ContextManager unit tests — dual storage
# ─────────────────────────────────────────────────────────────────────────────

class TestContextManagerStorage:
    """
    Unit tests for ContextManager with mocked Redis and MongoDB.
    Verify that add_message appends (not replaces) and both stores are updated.
    """

    def _make_redis(self, initial_context: dict = None):
        r = AsyncMock()
        store = {}
        if initial_context:
            store[f"context:{CONV_ID}"] = json.dumps(initial_context)

        async def _get(key):
            return store.get(key)

        async def _setex(key, _ttl, value):
            store[key] = value

        r.get = AsyncMock(side_effect=_get)
        r.setex = AsyncMock(side_effect=_setex)
        r.delete = AsyncMock(return_value=1)
        return r, store

    def _make_mongo(self, initial_context: dict = None):
        mongo = MagicMock()
        db = MagicMock()
        coll = AsyncMock()
        mongo.__getitem__ = MagicMock(return_value=db)
        db.__getitem__ = MagicMock(return_value=coll)
        db.conversations = coll
        db.message_attachments = AsyncMock()

        stored = {}
        if initial_context:
            stored[CONV_ID] = dict(initial_context)

        async def _find_one(query):
            doc = stored.get(query.get("conversation_id"))
            return dict(doc) if doc else None

        async def _update_one(query, update, upsert=False):  # noqa: ARG001
            cid = query["conversation_id"]
            doc = update.get("$set", {})
            stored[cid] = dict(doc)

        coll.find_one = AsyncMock(side_effect=_find_one)
        coll.update_one = AsyncMock(side_effect=_update_one)
        coll.delete_one = AsyncMock(return_value=None)
        return mongo, coll, stored

    @pytest.mark.asyncio
    async def test_add_message_appends_not_replaces(self):
        """add_message must append to the messages list, never replace it."""
        redis_client, _ = self._make_redis()
        mongo_client, _, _ = self._make_mongo()

        cm = ContextManager(redis_client, mongo_client)
        await cm.create_context(CONV_ID, user_id=USER_ID)

        await cm.add_message(CONV_ID, "user", MSG1)
        await cm.add_message(CONV_ID, "assistant", "Sure!")
        await cm.add_message(CONV_ID, "user", MSG2)

        messages = await cm.get_messages(CONV_ID)
        assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"
        assert messages[0]["content"] == MSG1
        assert messages[1]["content"] == "Sure!"
        assert messages[2]["content"] == MSG2

    @pytest.mark.asyncio
    async def test_add_message_writes_to_redis(self):
        """Each add_message must persist to Redis."""
        redis_client, store = self._make_redis()
        mongo_client, _, _ = self._make_mongo()

        cm = ContextManager(redis_client, mongo_client)
        await cm.create_context(CONV_ID, user_id=USER_ID)
        await cm.add_message(CONV_ID, "user", MSG1)

        redis_key = f"context:{CONV_ID}"
        assert redis_key in store, "Context not written to Redis after add_message"
        saved = json.loads(store[redis_key])
        assert len(saved["messages"]) == 1

    @pytest.mark.asyncio
    async def test_add_message_writes_to_mongo(self):
        """Each add_message must persist to MongoDB."""
        redis_client, _ = self._make_redis()
        mongo_client, coll, stored = self._make_mongo()

        cm = ContextManager(redis_client, mongo_client)
        await cm.create_context(CONV_ID, user_id=USER_ID)
        await cm.add_message(CONV_ID, "user", MSG1)

        coll.update_one.assert_awaited()
        assert CONV_ID in stored, "Context not written to MongoDB after add_message"

    @pytest.mark.asyncio
    async def test_get_context_falls_back_to_mongo_when_redis_empty(self):
        """If Redis key is missing (expired), get_context must restore from MongoDB."""
        # Redis is empty
        redis_client, _ = self._make_redis()
        # MongoDB has the context
        existing = {
            "conversation_id": CONV_ID,
            "user_id": USER_ID,
            "messages": [{"role": "user", "content": MSG1}],
        }
        mongo_client, _, _ = self._make_mongo(initial_context=existing)

        cm = ContextManager(redis_client, mongo_client)
        ctx = await cm.get_context(CONV_ID)

        assert ctx is not None, "Context must be restored from MongoDB when Redis is empty"
        assert ctx["conversation_id"] == CONV_ID
        assert len(ctx["messages"]) == 1

    @pytest.mark.asyncio
    async def test_redis_recached_after_mongo_fallback(self):
        """After a MongoDB fallback, Redis must be repopulated for fast subsequent access."""
        redis_client, store = self._make_redis()
        existing = {"conversation_id": CONV_ID, "user_id": USER_ID, "messages": []}
        mongo_client, _, _ = self._make_mongo(initial_context=existing)

        cm = ContextManager(redis_client, mongo_client)
        await cm.get_context(CONV_ID)  # Triggers MongoDB fallback

        redis_key = f"context:{CONV_ID}"
        assert redis_key in store, "Redis not repopulated after MongoDB fallback"

    @pytest.mark.asyncio
    async def test_message_id_is_unique_per_message(self):
        """Every message must get a distinct message_id."""
        redis_client, _ = self._make_redis()
        mongo_client, _, _ = self._make_mongo()

        cm = ContextManager(redis_client, mongo_client)
        await cm.create_context(CONV_ID, user_id=USER_ID)

        id1 = await cm.add_message(CONV_ID, "user", MSG1)
        id2 = await cm.add_message(CONV_ID, "assistant", "Sure!")
        id3 = await cm.add_message(CONV_ID, "user", MSG2)

        assert id1 != id2 != id3, "message_ids must be unique"
        assert len({id1, id2, id3}) == 3

    @pytest.mark.asyncio
    async def test_conversation_isolation_in_redis(self):
        """Two conversations must use different Redis keys."""
        redis_client, _ = self._make_redis()
        mongo_client, _, _ = self._make_mongo()

        cm = ContextManager(redis_client, mongo_client)
        await cm.create_context("conv_A", user_id="userA")
        await cm.create_context("conv_B", user_id="userB")
        await cm.add_message("conv_A", "user", "Hello A")
        await cm.add_message("conv_B", "user", "Hello B")

        ctx_a = await cm.get_context("conv_A")
        ctx_b = await cm.get_context("conv_B")

        assert ctx_a["messages"][0]["content"] == "Hello A"
        assert ctx_b["messages"][0]["content"] == "Hello B"
        assert "Hello B" not in [m["content"] for m in ctx_a["messages"]]


# ─────────────────────────────────────────────────────────────────────────────
# 9. EventEmitter unit tests — event payloads
# ─────────────────────────────────────────────────────────────────────────────

class TestEventEmitterPayloads:
    """Verify every event type carries the correct request_id and data."""

    @pytest.fixture
    def emitter_with_mock(self):
        r = AsyncMock()
        r.incr = AsyncMock(side_effect=range(1, 100))
        r.expire = AsyncMock(return_value=True)
        r.xadd = AsyncMock(return_value=b"1-0")
        return EventEmitter(r), r

    @pytest.mark.asyncio
    async def test_thinking_start_request_id(self, emitter_with_mock):
        emitter, _ = emitter_with_mock
        await emitter.emit_thinking_start(request_id=CONV_ID)
        xadd = emitter.redis.xadd.await_args
        payload = json.loads(xadd.args[1]["data"])
        assert payload["request_id"] == CONV_ID

    @pytest.mark.asyncio
    async def test_message_delta_payload(self, emitter_with_mock):
        emitter, _ = emitter_with_mock
        await emitter.emit_message_delta(
            request_id=CONV_ID, delta="Hello", accumulated_length=5
        )
        xadd = emitter.redis.xadd.await_args
        payload = json.loads(xadd.args[1]["data"])
        assert payload["request_id"] == CONV_ID
        assert payload["delta"] == "Hello"
        assert payload["accumulated_length"] == 5

    @pytest.mark.asyncio
    async def test_message_final_payload(self, emitter_with_mock):
        emitter, _ = emitter_with_mock
        await emitter.emit_message_final(
            request_id=CONV_ID,
            content="Final answer",
            role="assistant",
            metadata={"conversation_id": CONV_ID},
        )
        xadd = emitter.redis.xadd.await_args
        payload = json.loads(xadd.args[1]["data"])
        assert payload["request_id"] == CONV_ID
        assert payload["content"] == "Final answer"
        assert payload["metadata"]["conversation_id"] == CONV_ID

    @pytest.mark.asyncio
    async def test_thinking_end_duration(self, emitter_with_mock):
        emitter, _ = emitter_with_mock
        await emitter.emit_thinking_end(request_id=CONV_ID, duration_ms=1234)
        xadd = emitter.redis.xadd.await_args
        payload = json.loads(xadd.args[1]["data"])
        assert payload["duration_ms"] == 1234

    @pytest.mark.asyncio
    async def test_app_error_recoverable_field(self, emitter_with_mock):
        emitter, _ = emitter_with_mock
        await emitter.emit_app_error(
            request_id=CONV_ID,
            error_type="system_error",
            error_message="Something went wrong",
            recoverable=True,
        )
        xadd = emitter.redis.xadd.await_args
        payload = json.loads(xadd.args[1]["data"])
        assert payload["recoverable"] is True

    @pytest.mark.asyncio
    async def test_hx_design_started_payload(self, emitter_with_mock):
        emitter, _ = emitter_with_mock
        await emitter.emit_hx_design_started(
            request_id=CONV_ID,
            session_id="sess_abc",
            stream_url="/api/v1/hx/design/sess_abc/stream",
        )
        xadd = emitter.redis.xadd.await_args
        payload = json.loads(xadd.args[1]["data"])
        assert payload["session_id"] == "sess_abc"
        assert payload["stream_url"] == "/api/v1/hx/design/sess_abc/stream"
        assert payload["event_type"] == "hx_design_started"
