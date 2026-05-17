"""
Tests for backend/app/api/hx.py — respond proxy + property validate endpoints.
(EPIC-XSTACK-2026-007-S2)

Scenarios:
  1. Valid override response → engine called, 200 returned
  2. Invalid viscosity → 422 returned, engine NOT called
  3. "accept" type (no properties key) → no validation, engine called
  4. Engine returns 410 → backend returns 410
  5. Engine returns 5xx → backend returns 502
  6. No X-Username header → 401
  7. POST /properties/validate → valid props → 200 {valid: true}
  8. POST /properties/validate → out-of-range cp → 200 {valid: false}
  9. POST /properties/validate → no X-Username → 401
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import Request as HttpxRequest, Response as HttpxResponse, HTTPStatusError
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_engine_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_engine_client(return_value=None, raise_exc=None):
    """Create a mock engine_client with respond_to_design configured."""
    ec = AsyncMock()
    if raise_exc is not None:
        ec.respond_to_design = AsyncMock(side_effect=raise_exc)
    else:
        ec.respond_to_design = AsyncMock(return_value=return_value or {"status": "ok"})
    return ec


@pytest.fixture
def mock_engine_ok():
    return _make_engine_client({"status": "ok", "session_id": "abc123"})


@pytest.fixture
def client_ok(mock_engine_ok):
    async def _override():
        return mock_engine_ok

    app.dependency_overrides[get_engine_client] = _override
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, mock_engine_ok
    app.dependency_overrides.clear()


def _httpx_status_error(status_code: int) -> HTTPStatusError:
    req = HttpxRequest("POST", "http://engine/test")
    resp = HttpxResponse(status_code=status_code, request=req)
    return HTTPStatusError(f"{status_code}", request=req, response=resp)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

AUTH_HEADERS = {"X-Username": "test_engineer"}

VALID_OVERRIDE_PAYLOAD = {
    "type": "override",
    "values": {
        "option_index": 1,
        "fluid_side": "hot",
        "properties": {
            "density_kg_m3": 855.0,
            "viscosity_Pa_s": 0.0022,
        },
    },
}

ACCEPT_PAYLOAD = {
    "type": "accept",
    "values": {"option_index": 0, "fluid_side": "hot"},
}


# ---------------------------------------------------------------------------
# Test 1: Valid override → engine called, 200
# ---------------------------------------------------------------------------

class TestRespondProxy:

    def test_valid_override_calls_engine(self, client_ok):
        client, mock_ec = client_ok
        resp = client.post(
            "/api/hx/design/session-xyz/respond",
            json=VALID_OVERRIDE_PAYLOAD,
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        mock_ec.respond_to_design.assert_awaited_once()
        args = mock_ec.respond_to_design.call_args
        assert args[0][0] == "session-xyz"

    def test_invalid_viscosity_returns_422_engine_not_called(self):
        bad_payload = {
            "type": "override",
            "values": {
                "option_index": 1,
                "fluid_side": "hot",
                "properties": {"viscosity_Pa_s": 999.0},  # way above 1.0 Pa·s
            },
        }
        mock_ec = _make_engine_client()
        async def _override():
            return mock_ec
        app.dependency_overrides[get_engine_client] = _override
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/hx/design/session-xyz/respond",
                json=bad_payload,
                headers=AUTH_HEADERS,
            )
        app.dependency_overrides.clear()

        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["valid"] is False
        assert any("viscosity_Pa_s" in e for e in body["detail"]["errors"])
        mock_ec.respond_to_design.assert_not_awaited()

    def test_accept_type_bypasses_validation_calls_engine(self, client_ok):
        client, mock_ec = client_ok
        resp = client.post(
            "/api/hx/design/session-abc/respond",
            json=ACCEPT_PAYLOAD,
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        mock_ec.respond_to_design.assert_awaited_once()

    def test_engine_410_surfaces_as_410(self):
        mock_ec = _make_engine_client(raise_exc=_httpx_status_error(410))
        async def _override():
            return mock_ec
        app.dependency_overrides[get_engine_client] = _override
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/hx/design/gone-session/respond",
                json=ACCEPT_PAYLOAD,
                headers=AUTH_HEADERS,
            )
        app.dependency_overrides.clear()

        assert resp.status_code == 410

    def test_engine_500_surfaces_as_502(self):
        mock_ec = _make_engine_client(raise_exc=_httpx_status_error(500))
        async def _override():
            return mock_ec
        app.dependency_overrides[get_engine_client] = _override
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/hx/design/bad-session/respond",
                json=ACCEPT_PAYLOAD,
                headers=AUTH_HEADERS,
            )
        app.dependency_overrides.clear()

        assert resp.status_code == 502

    def test_no_auth_header_returns_401(self, client_ok):
        client, _ = client_ok
        resp = client.post(
            "/api/hx/design/session-xyz/respond",
            json=ACCEPT_PAYLOAD,
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /properties/validate
# ---------------------------------------------------------------------------

class TestPropertiesValidateEndpoint:

    def test_valid_properties_returns_valid_true(self, client_ok):
        client, _ = client_ok
        resp = client.post(
            "/api/hx/properties/validate",
            json={
                "properties": {
                    "density_kg_m3": 860.0,
                    "viscosity_Pa_s": 0.003,
                    "cp_J_kgK": 2100.0,
                    "k_W_mK": 0.135,
                }
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["errors"] == []
        assert body["cleaned_values"]["density_kg_m3"] == pytest.approx(860.0)

    def test_out_of_range_cp_returns_valid_false(self, client_ok):
        client, _ = client_ok
        resp = client.post(
            "/api/hx/properties/validate",
            json={"properties": {"cp_J_kgK": 999_999_999.0}},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert any("cp_J_kgK" in e for e in body["errors"])

    def test_no_auth_header_returns_401(self, client_ok):
        client, _ = client_ok
        resp = client.post(
            "/api/hx/properties/validate",
            json={"properties": {"density_kg_m3": 850.0}},
        )
        assert resp.status_code == 401
