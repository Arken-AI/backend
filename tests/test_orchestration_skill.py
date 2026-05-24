from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import orchestration_service
from app.services.orchestration_service import (
    ORCHESTRATION_SKILL_FILE,
    SKILLS_DIR,
    OrchestrationService,
    _load_skill,
)


@pytest.fixture
def service() -> OrchestrationService:
    context_manager = AsyncMock()
    return OrchestrationService(
        context_manager=context_manager,
        event_emitter=AsyncMock(),
        llm_provider=AsyncMock(),
        engine_client=AsyncMock(),
        tool_registry=MagicMock(),
        redis_client=None,
    )


def test_orchestration_skill_exists_and_is_non_empty() -> None:
    skill_path = SKILLS_DIR / ORCHESTRATION_SKILL_FILE

    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8").strip()


def test_load_skill_reads_orchestration_skill() -> None:
    content = _load_skill(ORCHESTRATION_SKILL_FILE)

    assert "You are ARKEN AI" in content
    assert "hx_validate_requirements" in content
    assert "hx_design" in content


@pytest.mark.asyncio
async def test_build_system_prompt_includes_skill_content(service: OrchestrationService) -> None:
    service.context_manager.get_context = AsyncMock(return_value=None)

    prompt = await service._build_system_prompt("conv_1")

    assert prompt == _load_skill(ORCHESTRATION_SKILL_FILE)


@pytest.mark.asyncio
async def test_build_system_prompt_appends_conversation_context_after_skill(
    service: OrchestrationService,
) -> None:
    service.context_manager.get_context = AsyncMock(return_value={
        "hx_session_id": "sess_1",
        "hx_steps": [
            {
                "step_id": 4,
                "step_name": "TEMA Geometry",
                "ai_decision": "WARN",
                "status": "completed",
                "outputs": {"tema_type": "BEM", "N_tubes": 120},
                "warnings": [],
            }
        ],
    })

    prompt = await service._build_system_prompt("conv_1")
    skill = _load_skill(ORCHESTRATION_SKILL_FILE)

    assert prompt.startswith(skill)
    assert "## Most recent design results" in prompt
    assert prompt.index("## Most recent design results") > len(skill)
    assert "Session: sess_1" in prompt
    assert "TEMA Geometry" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_raises_when_skill_missing(
    service: OrchestrationService,
    tmp_path,
) -> None:
    service.context_manager.get_context = AsyncMock(return_value=None)

    with patch.object(orchestration_service, "SKILLS_DIR", tmp_path):
        with pytest.raises(FileNotFoundError):
            await service._build_system_prompt("conv_1")