from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.orchestration_service import (
    CONTEXT_COMPACTION_TRIGGER_MESSAGES,
    MAX_RECENT_MESSAGES,
    OrchestrationService,
)


def _message(index: int, role: str = "user") -> dict:
    return {
        "message_id": f"msg_{index:03d}",
        "role": role,
        "content": f"message {index}",
        "timestamp": f"2026-05-25T00:{index:02d}:00",
    }


@pytest.fixture
def service() -> OrchestrationService:
    return OrchestrationService(
        context_manager=AsyncMock(),
        event_emitter=AsyncMock(),
        llm_provider=AsyncMock(),
        engine_client=AsyncMock(),
        tool_registry=AsyncMock(),
        redis_client=None,
    )


@pytest.mark.asyncio
async def test_long_history_gets_compacted_before_llm(service: OrchestrationService) -> None:
    raw_messages = [_message(i, "user" if i % 2 == 0 else "assistant") for i in range(25)]
    raw_messages.append(_message(25, "user"))
    service.context_manager.get_messages = AsyncMock(return_value=raw_messages)
    service.context_manager.get_context = AsyncMock(return_value={})

    messages = await service._build_llm_messages("conv_1", "current turn")

    assert messages[0]["role"] == "user"
    assert "[Compacted prior conversation context]" in messages[0]["content"]
    assert len(messages) == MAX_RECENT_MESSAGES + 2
    assert messages[-1] == {"role": "user", "content": "current turn"}


@pytest.mark.asyncio
async def test_short_history_is_not_compacted(service: OrchestrationService) -> None:
    raw_messages = [_message(i, "user" if i % 2 == 0 else "assistant") for i in range(6)]
    raw_messages.append(_message(6, "user"))
    service.context_manager.get_messages = AsyncMock(return_value=raw_messages)
    service.context_manager.get_context = AsyncMock(return_value={})

    messages = await service._build_llm_messages("conv_1", "current turn")

    assert "[Compacted prior conversation context]" not in messages[0]["content"]
    assert service.context_manager.get_context.await_count == 0


@pytest.mark.asyncio
async def test_compaction_preserves_confirmed_user_decisions_and_latest_step_status(
    service: OrchestrationService,
) -> None:
    raw_messages = [
        _message(i, "user" if i % 2 == 0 else "assistant")
        for i in range(CONTEXT_COMPACTION_TRIGGER_MESSAGES + 3)
    ]
    raw_messages.append(_message(99, "user"))
    service.context_manager.get_messages = AsyncMock(return_value=raw_messages)
    service.context_manager.get_context = AsyncMock(return_value={
        "escalation_history": {
            "11": [{"attempt": 2, "user_chose": "Use larger tube length"}],
        },
        "hx_steps": [
            {"step_id": 10, "step_name": "Pressure Drops", "status": "completed"},
            {
                "step_id": 11,
                "step_name": "Area Overdesign",
                "status": "waiting_for_user",
                "ai_decision": "ESCALATE",
                "warnings": ["Exchanger is undersized"],
            },
        ],
    })

    messages = await service._build_llm_messages("conv_1", "current turn")
    summary = messages[0]["content"]

    assert "confirmed_user_decisions" in summary
    assert "Use larger tube length" in summary
    assert "open_engineering_risks" in summary
    assert "Exchanger is undersized" in summary
    assert "latest_step_status: Step 11 - Area Overdesign" in summary


@pytest.mark.asyncio
async def test_compaction_does_not_delete_raw_history(service: OrchestrationService) -> None:
    raw_messages = [_message(i, "user" if i % 2 == 0 else "assistant") for i in range(25)]
    raw_messages.append(_message(25, "user"))
    service.context_manager.get_messages = AsyncMock(return_value=raw_messages)
    service.context_manager.get_context = AsyncMock(return_value={})

    await service._build_llm_messages("conv_1", "current turn")

    assert len(raw_messages) == 26
    service.context_manager.update_context.assert_not_called()
    service.context_manager.delete_messages_from_tail.assert_not_called()