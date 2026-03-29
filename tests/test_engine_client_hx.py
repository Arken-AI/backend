"""
Unit tests for HXEngineClient — validate_requirements and start_design.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.engine_client import HXEngineClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """HXEngineClient with a mocked httpx.AsyncClient."""
    ec = HXEngineClient.__new__(HXEngineClient)
    ec.base_url = "http://hx-engine:8100"
    ec._client = AsyncMock()
    return ec


def _make_response(status_code: int, json_body: dict) -> httpx.Response:
    # httpx.Response needs a request object to call raise_for_status()
    request = httpx.Request("POST", "http://hx-engine:8100/api/v1/hx/test")
    return httpx.Response(status_code, json=json_body, request=request)


# ---------------------------------------------------------------------------
# validate_requirements
# ---------------------------------------------------------------------------

class TestValidateRequirements:
    async def test_posts_correct_payload(self, client):
        client._client.post = AsyncMock(return_value=_make_response(200, {"valid": True, "token": "tok"}))
        await client.validate_requirements(
            user_id="test",
            hot_fluid_name="steam",
            cold_fluid_name="water",
            T_hot_in_C=180.0,
            T_cold_in_C=25.0,
            m_dot_hot_kg_s=10.0,
        )
        call_kwargs = client._client.post.call_args
        assert call_kwargs[0][0] == "/api/v1/hx/requirements"
        payload = call_kwargs[1]["json"]
        assert payload["user_id"] == "test"
        assert payload["hot_fluid_name"] == "steam"
        assert payload["T_hot_in_C"] == 180.0

    async def test_returns_json_response(self, client):
        expected = {"valid": True, "token": "tok_abc", "warnings": []}
        client._client.post = AsyncMock(return_value=_make_response(200, expected))
        result = await client.validate_requirements(user_id="test", hot_fluid_name="steam",
                                                     cold_fluid_name="water", T_hot_in_C=180.0,
                                                     T_cold_in_C=25.0, m_dot_hot_kg_s=10.0)
        assert result == expected

    async def test_raises_on_http_error(self, client):
        resp = _make_response(422, {"detail": "validation error"})
        client._client.post = AsyncMock(return_value=resp)
        with pytest.raises(httpx.HTTPStatusError):
            await client.validate_requirements(user_id="test", hot_fluid_name="steam",
                                                cold_fluid_name="water", T_hot_in_C=180.0,
                                                T_cold_in_C=25.0, m_dot_hot_kg_s=10.0)

    async def test_not_connected_raises_runtime_error(self):
        ec = HXEngineClient.__new__(HXEngineClient)
        ec.base_url = "http://hx-engine:8100"
        ec._client = None
        with pytest.raises(RuntimeError, match="not connected"):
            await ec.validate_requirements(user_id="test", hot_fluid_name="steam",
                                            cold_fluid_name="water", T_hot_in_C=180.0,
                                            T_cold_in_C=25.0, m_dot_hot_kg_s=10.0)


# ---------------------------------------------------------------------------
# start_design
# ---------------------------------------------------------------------------

class TestStartDesign:
    async def test_posts_correct_payload(self, client):
        client._client.post = AsyncMock(return_value=_make_response(200, {
            "session_id": "sess_abc", "stream_url": "/api/v1/hx/design/sess_abc/stream"
        }))
        await client.start_design(
            user_id="test",
            hot_fluid_name="steam",
            cold_fluid_name="water",
            T_hot_in_C=180.0,
            T_cold_in_C=25.0,
            m_dot_hot_kg_s=10.0,
            token="tok_abc",
        )
        call_kwargs = client._client.post.call_args
        assert call_kwargs[0][0] == "/api/v1/hx/design"
        payload = call_kwargs[1]["json"]
        assert payload["user_id"] == "test"
        assert payload["token"] == "tok_abc"
        # org_id omitted → not in payload
        assert "org_id" not in payload

    async def test_includes_org_id_when_provided(self, client):
        client._client.post = AsyncMock(return_value=_make_response(200, {
            "session_id": "sess_xyz", "stream_url": "/api/v1/hx/design/sess_xyz/stream"
        }))
        await client.start_design(user_id="test", org_id="org_1", hot_fluid_name="steam",
                                   cold_fluid_name="water", T_hot_in_C=180.0,
                                   T_cold_in_C=25.0, m_dot_hot_kg_s=10.0)
        payload = client._client.post.call_args[1]["json"]
        assert payload["org_id"] == "org_1"

    async def test_returns_session_id_and_stream_url(self, client):
        expected = {"session_id": "sess_xyz", "stream_url": "/api/v1/hx/design/sess_xyz/stream"}
        client._client.post = AsyncMock(return_value=_make_response(200, expected))
        result = await client.start_design(user_id="test", hot_fluid_name="steam",
                                            cold_fluid_name="water", T_hot_in_C=180.0,
                                            T_cold_in_C=25.0, m_dot_hot_kg_s=10.0)
        assert result["session_id"] == "sess_xyz"
        assert result["stream_url"] == "/api/v1/hx/design/sess_xyz/stream"
