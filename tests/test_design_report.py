"""
Tests for FEATURE-002: Design report generation.

Tests _generate_design_report, _build_fallback_report, _build_step_digest,
the expanded _persist_hx_steps (report generation + persistence), and
the enriched _build_system_prompt (step digest injection).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.services.orchestration_service import (
    OrchestrationService,
    SYSTEM_PROMPT,
    REPORT_LLM_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_STEP_RECORDS = [
    {
        "step_id": 1,
        "step_name": "Fluid Properties",
        "ai_decision": "PROCEED",
        "duration_s": 0.5,
        "outputs": {"Q_W": 1032000, "LMTD_K": 45.2},
        "warnings": [],
        "ai_review": {"reasoning": "All fluid properties look correct."},
    },
    {
        "step_id": 4,
        "step_name": "TEMA Geometry Selection",
        "ai_decision": "WARN",
        "duration_s": 14.6,
        "outputs": {"tema_type": "AEU", "tema_class": "R"},
        "warnings": [
            "Lubricating oil on tube side in U-tube bundle: mechanical cleaning impossible."
        ],
        "ai_review": {
            "reasoning": "AEU selected but fouling concern on tube side.",
            "observation": "Consider AES if oil fouling worsens.",
            "corrections": [],
        },
    },
    {
        "step_id": 6,
        "step_name": "Initial U + Size Estimate",
        "ai_decision": "CORRECT",
        "duration_s": 2.1,
        "outputs": {"U_W_m2K": 300, "A_m2": 81.0, "N_tubes": 278, "tube_length_m": 4.88},
        "warnings": ["Zero overdesign margin."],
        "ai_review": {
            "reasoning": "U estimate adjusted for viscosity.",
            "observation": "Viscosity uncertainty reduces confidence.",
            "corrections": [
                {
                    "field": "U_W_m2K",
                    "old_value": 350,
                    "new_value": 300,
                    "reason": "Lube oil viscosity correction",
                }
            ],
        },
    },
]


@pytest.fixture
def mock_context_manager():
    cm = AsyncMock()
    cm.get_context = AsyncMock(return_value={"conversation_id": "conv_1"})
    cm.create_context = AsyncMock(return_value={"conversation_id": "conv_1"})
    cm.add_message = AsyncMock(return_value="msg_id_1")
    cm.get_messages = AsyncMock(return_value=[])
    cm.update_context = AsyncMock()
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
def mock_llm_provider():
    return AsyncMock()


@pytest.fixture
def mock_engine_client():
    ec = AsyncMock()
    ec.base_url = "http://hx-engine:8100"
    ec.get_design_status = AsyncMock(return_value={
        "is_complete": True,
        "step_records": SAMPLE_STEP_RECORDS,
    })
    return ec


def make_service(context_manager, event_emitter, llm_provider, engine_client=None):
    return OrchestrationService(
        context_manager=context_manager,
        event_emitter=event_emitter,
        llm_provider=llm_provider,
        engine_client=engine_client,
        redis_client=None,
    )


# ---------------------------------------------------------------------------
# Test: _generate_design_report
# ---------------------------------------------------------------------------

class TestGenerateDesignReport:
    async def test_happy_path(self, mock_context_manager, mock_event_emitter, mock_llm_provider):
        """LLM returns a valid report → use it as-is."""
        expected_report = "### Design Complete ✓\n\nYour HX has been sized."
        mock_response = MagicMock()
        mock_response.text = expected_report
        mock_llm_provider.create_message = AsyncMock(return_value=mock_response)

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        report = await service._generate_design_report(SAMPLE_STEP_RECORDS)

        assert report == expected_report
        mock_llm_provider.create_message.assert_called_once()

    async def test_llm_exception_returns_fallback(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """LLM raises exception → fall back to deterministic template."""
        mock_llm_provider.create_message = AsyncMock(
            side_effect=Exception("API key invalid")
        )

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        report = await service._generate_design_report(SAMPLE_STEP_RECORDS)

        # Should contain key results from the template fallback
        assert "Design Complete" in report
        assert "AEU" in report

    async def test_llm_timeout_returns_fallback(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """LLM hangs beyond REPORT_LLM_TIMEOUT → fallback."""
        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(REPORT_LLM_TIMEOUT + 5)  # will be cancelled
            return MagicMock(text="Too late")

        mock_llm_provider.create_message = slow_llm

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        report = await service._generate_design_report(SAMPLE_STEP_RECORDS)

        assert "Design Complete" in report  # fallback template
        assert "AEU" in report

    async def test_llm_empty_response_returns_fallback(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """LLM returns empty string → fallback."""
        mock_response = MagicMock()
        mock_response.text = "   "
        mock_llm_provider.create_message = AsyncMock(return_value=mock_response)

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        report = await service._generate_design_report(SAMPLE_STEP_RECORDS)

        assert "Design Complete" in report  # fallback template


# ---------------------------------------------------------------------------
# Test: _build_fallback_report
# ---------------------------------------------------------------------------

class TestBuildFallbackReport:
    def test_with_warnings(self):
        """Fallback report includes warnings from step records."""
        report = OrchestrationService._build_fallback_report(SAMPLE_STEP_RECORDS)

        assert "Design Complete" in report
        assert "AEU" in report
        assert "⚠" in report
        assert "cleaning" in report.lower() or "overdesign" in report.lower()

    def test_no_warnings(self):
        """Steps with no warnings produce a clean report."""
        clean_records = [
            {
                "step_id": 1,
                "step_name": "Fluid Properties",
                "ai_decision": "PROCEED",
                "duration_s": 0.5,
                "outputs": {"Q_W": 500000, "tema_type": "AES", "A_m2": 40.0},
                "warnings": [],
                "ai_review": {},
            }
        ]
        report = OrchestrationService._build_fallback_report(clean_records)

        assert "Design Complete" in report
        assert "AES" in report
        # No warning section
        assert "need attention" not in report

    def test_empty_step_records(self):
        """Empty step records still produce a valid report."""
        report = OrchestrationService._build_fallback_report([])

        assert "Design Complete" in report
        assert "N/A" in report  # tema_type default

    def test_deduplicated_warnings(self):
        """Duplicate warnings from multiple steps are deduplicated."""
        records = [
            {
                "step_id": 1,
                "outputs": {},
                "warnings": ["Zero overdesign margin."],
                "ai_review": {"observation": "Zero overdesign margin."},
            },
            {
                "step_id": 2,
                "outputs": {},
                "warnings": ["Zero overdesign margin."],
                "ai_review": {},
            },
        ]
        report = OrchestrationService._build_fallback_report(records)

        # Should appear only once (deduplicated)
        assert report.count("Zero overdesign margin.") == 1


# ---------------------------------------------------------------------------
# Test: _build_step_digest
# ---------------------------------------------------------------------------

class TestBuildStepDigest:
    def test_filters_proceed_steps(self):
        """PROCEED steps are excluded from the digest to save tokens."""
        digest = OrchestrationService._build_step_digest(SAMPLE_STEP_RECORDS)

        # Step 1 (PROCEED) should NOT appear
        assert "Fluid Properties" not in digest
        # Step 4 (WARN) and Step 6 (CORRECT) SHOULD appear
        assert "TEMA Geometry" in digest
        assert "Initial U" in digest

    def test_empty_steps(self):
        """Empty step list produces a sensible message."""
        digest = OrchestrationService._build_step_digest([])

        assert "All steps passed" in digest

    def test_all_proceed_steps(self):
        """When every step is PROCEED, include a summary of final outputs."""
        all_proceed = [
            {
                "step_id": 1,
                "step_name": "Fluid Properties",
                "ai_decision": "PROCEED",
                "outputs": {"Q_W": 1032000, "A_m2": 81.0, "tema_type": "AEU"},
                "warnings": [],
                "ai_review": {},
            },
        ]
        digest = OrchestrationService._build_step_digest(all_proceed)

        assert "All steps passed" in digest
        # Should include key final outputs
        assert "Q_W" in digest or "A_m2" in digest


# ---------------------------------------------------------------------------
# Test: _persist_hx_steps (with report generation)
# ---------------------------------------------------------------------------

class TestPersistHxStepsWithReport:
    async def test_generates_and_saves_report(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider, mock_engine_client
    ):
        """Full happy path: poll → persist steps → generate report → save report + message."""
        mock_response = MagicMock()
        mock_response.text = "### Design Complete ✓\n\nGreat design!"
        mock_llm_provider.create_message = AsyncMock(return_value=mock_response)

        service = make_service(
            mock_context_manager, mock_event_emitter, mock_llm_provider, mock_engine_client
        )

        await service._persist_hx_steps(
            conversation_id="conv_1",
            session_id="sess_1",
            poll_interval_s=0.01,  # fast for testing
        )

        # Steps persisted
        calls = mock_context_manager.update_context.call_args_list
        assert len(calls) >= 2
        # First call: hx_steps
        first_update = calls[0][0][1]
        assert "hx_steps" in first_update
        # Second call: hx_design_report
        second_update = calls[1][0][1]
        assert "hx_design_report" in second_update
        assert "Design Complete" in second_update["hx_design_report"]

        # Report saved as assistant message
        mock_context_manager.add_message.assert_called_once()
        add_msg_kwargs = mock_context_manager.add_message.call_args
        assert add_msg_kwargs[1]["role"] == "assistant"
        assert "design_report" in str(add_msg_kwargs[1]["metadata"])

    async def test_timeout_saves_error_report(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """When pipeline never completes, save a timeout error report."""
        engine_client = AsyncMock()
        engine_client.get_design_status = AsyncMock(
            return_value={"is_complete": False, "step_records": []}
        )

        service = make_service(
            mock_context_manager, mock_event_emitter, mock_llm_provider, engine_client
        )

        await service._persist_hx_steps(
            conversation_id="conv_1",
            session_id="sess_1",
            poll_interval_s=0.01,
            max_polls=2,  # times out after 2 polls
        )

        # Should save timeout report
        mock_context_manager.update_context.assert_called()
        last_update = mock_context_manager.update_context.call_args[0][1]
        assert "hx_design_report" in last_update
        assert "did not complete" in last_update["hx_design_report"]

        # Report saved as assistant message
        mock_context_manager.add_message.assert_called_once()
        assert "is_timeout" in str(mock_context_manager.add_message.call_args)


# ---------------------------------------------------------------------------
# Test: _build_system_prompt (step digest injection)
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    async def test_injects_step_digest_when_steps_present(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """When hx_steps exist in context, system prompt includes step data."""
        mock_context_manager.get_context = AsyncMock(return_value={
            "conversation_id": "conv_1",
            "hx_session_id": "sess_1",
            "hx_steps": SAMPLE_STEP_RECORDS,
        })

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        prompt = await service._build_system_prompt("conv_1")

        # Should contain the base prompt
        assert "ARKEN AI" in prompt
        # Should contain the step digest section
        assert "Most recent design results" in prompt
        assert "sess_1" in prompt
        # Should include non-PROCEED steps
        assert "TEMA Geometry" in prompt

    async def test_no_step_data_returns_base_prompt(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """When no hx_steps in context, return the base SYSTEM_PROMPT unchanged."""
        mock_context_manager.get_context = AsyncMock(return_value={
            "conversation_id": "conv_1",
        })

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        prompt = await service._build_system_prompt("conv_1")

        assert prompt == SYSTEM_PROMPT

    async def test_no_context_returns_base_prompt(
        self, mock_context_manager, mock_event_emitter, mock_llm_provider
    ):
        """When context is None, return the base SYSTEM_PROMPT."""
        mock_context_manager.get_context = AsyncMock(return_value=None)

        service = make_service(mock_context_manager, mock_event_emitter, mock_llm_provider)
        prompt = await service._build_system_prompt("conv_1")

        assert prompt == SYSTEM_PROMPT
