"""
Tests for OrchestrationService tool dispatch path (Path 2 — browser HX design).

All external calls (Claude API, HX Engine, Redis, MongoDB) are mocked.
Tests verify the validate→design loop, hx_design_started emission,
design_started flag (tools dropped after design), and engine-down error path.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime

from app.services.orchestration_service import OrchestrationService, MAX_TOOL_TURNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_use_block(name: str, tool_id: str, input_dict: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = input_dict
    return block


def _make_text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_final_message(stop_reason: str, content: list, input_tokens=10, output_tokens=20):
    msg = MagicMock()
    msg.stop_reason = stop_reason
    msg.content = content
    msg.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return msg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALIDATE_PARAMS = {
    "hot_fluid_name": "steam",
    "cold_fluid_name": "water",
    "T_hot_in_C": 180.0,
    "T_cold_in_C": 25.0,
    "m_dot_hot_kg_s": 10.0,
    "T_cold_out_C": 45.0,
}


@pytest.fixture
def mock_context_manager():
    cm = AsyncMock()
    cm.get_context = AsyncMock(return_value={"conversation_id": "conv_1"})
    cm.create_context = AsyncMock(return_value={"conversation_id": "conv_1"})
    cm.add_message = AsyncMock(return_value="msg_id_1")
    cm.store_message_attachments = AsyncMock()
    cm.get_messages = AsyncMock(return_value=[])
    cm.update_message = AsyncMock()
    return cm


@pytest.fixture
def mock_event_emitter():
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
def mock_engine_client():
    ec = AsyncMock()
    ec.base_url = "http://hx-engine:8100"
    ec.validate_requirements = AsyncMock(return_value={
        "valid": True,
        "token": "tok_abc",
        "user_message": "All valid.",
        "warnings": [],
    })
    ec.start_design = AsyncMock(return_value={
        "session_id": "sess_xyz",
        "stream_url": "/api/v1/hx/design/sess_xyz/stream",
    })
    return ec


@pytest.fixture
def mock_tool_registry():
    tr = MagicMock()
    tr.get_tools_for_claude = MagicMock(return_value=[
        {"name": "hx_validate_requirements", "description": "validate", "input_schema": {}},
        {"name": "hx_design", "description": "design", "input_schema": {}},
    ])
    tr.get_tool_endpoint = MagicMock(return_value="/api/v1/hx/requirements")
    return tr


@pytest.fixture
def mock_llm_provider():
    return AsyncMock()


def _make_stream(text_chunks: list[str], final_message):
    """Build an async context manager that yields text chunks and final message."""
    stream = AsyncMock()

    async def text_stream_gen():
        for chunk in text_chunks:
            yield chunk

    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=False)
    stream.text_stream = text_stream_gen()
    stream.get_final_message = AsyncMock(return_value=final_message)
    return stream


def make_service(context_manager, event_emitter, llm_provider, engine_client, tool_registry):
    return OrchestrationService(
        context_manager=context_manager,
        event_emitter=event_emitter,
        llm_provider=llm_provider,
        engine_client=engine_client,
        tool_registry=tool_registry,
        redis_client=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidateDesignLoop:
    async def test_validate_then_design_two_turns(
        self, mock_context_manager, mock_event_emitter, mock_engine_client,
        mock_tool_registry, mock_llm_provider
    ):
        """
        Full happy path:
          Turn 1: Claude calls hx_validate_requirements
          Turn 2: Claude calls hx_design
          Turn 3: Claude produces final text (no tools)
        """
        validate_tool_block = _make_tool_use_block(
            "hx_validate_requirements", "tc_1", VALIDATE_PARAMS
        )
        design_tool_block = _make_tool_use_block(
            "hx_design", "tc_2", {**VALIDATE_PARAMS, "token": "tok_abc"}
        )

        # Turn 1: tool_use (validate)
        msg_turn1 = _make_final_message("tool_use", [validate_tool_block])
        # Turn 2: tool_use (design)
        msg_turn2 = _make_final_message("tool_use", [design_tool_block])
        # Turn 3: end_turn (text response)
        msg_turn3 = _make_final_message("end_turn", [_make_text_block("Design started!")])

        streams = [
            _make_stream([], msg_turn1),
            _make_stream([], msg_turn2),
            _make_stream(["Design started!"], msg_turn3),
        ]
        mock_llm_provider.create_message_stream = MagicMock(side_effect=streams)

        service = make_service(mock_context_manager, mock_event_emitter,
                                mock_llm_provider, mock_engine_client, mock_tool_registry)

        result = await service.process_message("conv_1", "Design a steam/water HX")
        assert result["status"] == "success"
        mock_engine_client.validate_requirements.assert_called_once()
        mock_engine_client.start_design.assert_called_once()
        mock_event_emitter.emit_hx_design_started.assert_called_once()

    async def test_hx_design_started_emitted_with_relative_stream_url(
        self, mock_context_manager, mock_event_emitter, mock_engine_client,
        mock_tool_registry, mock_llm_provider
    ):
        """stream_url in the emitted event must be the relative path, not the Docker hostname."""
        design_tool_block = _make_tool_use_block("hx_design", "tc_1", VALIDATE_PARAMS)
        msg_turn1 = _make_final_message("tool_use", [design_tool_block])
        msg_turn2 = _make_final_message("end_turn", [_make_text_block("Done")])

        mock_llm_provider.create_message_stream = MagicMock(side_effect=[
            _make_stream([], msg_turn1),
            _make_stream(["Done"], msg_turn2),
        ])

        service = make_service(mock_context_manager, mock_event_emitter,
                                mock_llm_provider, mock_engine_client, mock_tool_registry)
        await service.process_message("conv_1", "Design HX")

        call_kwargs = mock_event_emitter.emit_hx_design_started.call_args[1]
        # Must NOT contain Docker-internal hostname
        assert "hx-engine" not in call_kwargs["stream_url"]
        # Must be the relative path returned by the engine
        assert call_kwargs["stream_url"] == "/api/v1/hx/design/sess_xyz/stream"
        assert call_kwargs["session_id"] == "sess_xyz"

    async def test_tools_dropped_after_design_started(
        self, mock_context_manager, mock_event_emitter, mock_engine_client,
        mock_tool_registry, mock_llm_provider
    ):
        """After hx_design succeeds, Claude is called WITHOUT tools on the next turn."""
        design_tool_block = _make_tool_use_block("hx_design", "tc_1", VALIDATE_PARAMS)
        msg_turn1 = _make_final_message("tool_use", [design_tool_block])
        msg_turn2 = _make_final_message("end_turn", [_make_text_block("Design started!")])

        streams = [
            _make_stream([], msg_turn1),
            _make_stream(["Design started!"], msg_turn2),
        ]
        call_log: list = []

        def capture_stream(*args, **kwargs):
            call_log.append(kwargs.get("tools"))
            return streams.pop(0)

        mock_llm_provider.create_message_stream = MagicMock(side_effect=capture_stream)

        service = make_service(mock_context_manager, mock_event_emitter,
                                mock_llm_provider, mock_engine_client, mock_tool_registry)
        await service.process_message("conv_1", "Design HX")

        # Turn 1 had tools (before design)
        assert call_log[0] is not None and len(call_log[0]) > 0
        # Turn 2 had no tools (after design started)
        assert call_log[1] is None

    async def test_engine_down_emits_app_error(
        self, mock_context_manager, mock_event_emitter, mock_engine_client,
        mock_tool_registry, mock_llm_provider
    ):
        """When HX Engine is unreachable, emit app_error and stop loop gracefully."""
        import httpx
        mock_engine_client.validate_requirements = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        validate_tool_block = _make_tool_use_block(
            "hx_validate_requirements", "tc_1", VALIDATE_PARAMS
        )
        msg_turn1 = _make_final_message("tool_use", [validate_tool_block])
        msg_turn2 = _make_final_message("end_turn", [_make_text_block("Sorry, engine is down.")])

        mock_llm_provider.create_message_stream = MagicMock(side_effect=[
            _make_stream([], msg_turn1),
            _make_stream(["Sorry, engine is down."], msg_turn2),
        ])

        service = make_service(mock_context_manager, mock_event_emitter,
                                mock_llm_provider, mock_engine_client, mock_tool_registry)
        result = await service.process_message("conv_1", "Design HX")

        # Process should complete (not raise), design_started never fires
        mock_event_emitter.emit_app_error.assert_called_once()
        mock_event_emitter.emit_hx_design_started.assert_not_called()

    async def test_max_turns_guard(
        self, mock_context_manager, mock_event_emitter, mock_engine_client,
        mock_tool_registry, mock_llm_provider
    ):
        """Loop exits after MAX_TOOL_TURNS even if Claude keeps calling tools."""
        validate_tool_block = _make_tool_use_block(
            "hx_validate_requirements", "tc_1", VALIDATE_PARAMS
        )
        # Always returns tool_use → loop should exit at MAX_TOOL_TURNS
        infinite_tool_msg = _make_final_message("tool_use", [validate_tool_block])
        final_text_msg = _make_final_message("end_turn", [_make_text_block("Done")])

        # MAX_TOOL_TURNS tool-use turns + 1 final text turn
        side_effects = [
            _make_stream([], infinite_tool_msg) for _ in range(MAX_TOOL_TURNS)
        ] + [_make_stream(["Done"], final_text_msg)]

        mock_llm_provider.create_message_stream = MagicMock(side_effect=side_effects)

        service = make_service(mock_context_manager, mock_event_emitter,
                                mock_llm_provider, mock_engine_client, mock_tool_registry)
        result = await service.process_message("conv_1", "Design HX")

        assert result["status"] == "success"
        assert mock_engine_client.validate_requirements.call_count == MAX_TOOL_TURNS


class TestFormatValidateResult:
    def test_valid_result_contains_token(self):
        data = {"valid": True, "token": "tok_abc", "user_message": "All valid.", "warnings": []}
        result = OrchestrationService._format_validate_result(data)
        assert "tok_abc" in result
        assert "PROCEED" in result

    def test_invalid_result_contains_errors(self):
        data = {
            "valid": False,
            "errors": [
                {"field": "T_hot_in_C", "message": "Must be > T_cold_in_C",
                 "suggestion": "Increase T_hot_in_C", "valid_range": "> 25 °C"}
            ]
        }
        result = OrchestrationService._format_validate_result(data)
        assert "T_hot_in_C" in result
        assert "Must be > T_cold_in_C" in result
        assert "Increase T_hot_in_C" in result

    def test_valid_result_with_warnings(self):
        data = {
            "valid": True, "token": "tok", "user_message": "Valid.",
            "warnings": ["Pressure near limit."]
        }
        result = OrchestrationService._format_validate_result(data)
        assert "Pressure near limit." in result
