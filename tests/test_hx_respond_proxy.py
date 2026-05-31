"""
Unit tests for the HX proxy router — backend/app/api/hx.py.

Tests cover the auth gate (401 when X-Username is missing), verbatim
relay of the engine's 200/404/410/422 responses, and normalization of
engine network failures into a 502.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_engine_client


SESSION_ID = "sess_test_123"


@pytest.fixture
def fake_engine():
    engine = AsyncMock()
    app.dependency_overrides[get_engine_client] = lambda: engine
    try:
        yield engine
    finally:
        app.dependency_overrides.pop(get_engine_client, None)


@pytest.fixture
def client(fake_engine):
    with TestClient(app) as c:
        yield c


def _post(client: TestClient, body: dict | None = None, username: str | None = "alice"):
    headers = {}
    if username is not None:
        headers["X-Username"] = username
    return client.post(
        f"/api/hx/design/{SESSION_ID}/respond",
        json=body if body is not None else {
            "type": "override",
            "values": {"user_input": "A", "option_index": 0},
        },
        headers=headers,
    )


class TestRespondProxy:
    def test_missing_x_username_returns_401(self, client, fake_engine):
        resp = _post(client, username=None)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "X-Username header required"
        fake_engine.respond_to_escalation.assert_not_called()

    def test_success_relays_engine_200_body(self, client, fake_engine):
        fake_engine.respond_to_escalation.return_value = (200, {"status": "received"})
        resp = _post(client)
        assert resp.status_code == 200
        assert resp.json() == {"status": "received"}
        fake_engine.respond_to_escalation.assert_awaited_once_with(
            SESSION_ID,
            {"type": "override", "values": {"user_input": "A", "option_index": 0}},
        )

    def test_404_relayed_verbatim(self, client, fake_engine):
        body = {"detail": "Session not found"}
        fake_engine.respond_to_escalation.return_value = (404, body)
        resp = _post(client)
        assert resp.status_code == 404
        assert resp.json() == body

    def test_410_relayed_verbatim(self, client, fake_engine):
        body = {"detail": "Response window has expired. The pipeline already timed out or completed."}
        fake_engine.respond_to_escalation.return_value = (410, body)
        resp = _post(client)
        assert resp.status_code == 410
        assert resp.json() == body

    def test_422_relayed_verbatim(self, client, fake_engine):
        body = {"detail": [{"loc": ["body", "type"], "msg": "Field required"}]}
        fake_engine.respond_to_escalation.return_value = (422, body)
        resp = _post(client)
        assert resp.status_code == 422
        assert resp.json() == body

    def test_engine_unreachable_returns_502(self, client, fake_engine):
        fake_engine.respond_to_escalation.side_effect = httpx.ConnectError(
            "engine down"
        )
        resp = _post(client)
        assert resp.status_code == 502
        assert resp.json() == {"detail": "HX engine unavailable"}

    def test_engine_5xx_returns_502(self, client, fake_engine):
        request = httpx.Request("POST", "http://hx-engine/x")
        response = httpx.Response(500, request=request)
        fake_engine.respond_to_escalation.side_effect = httpx.HTTPStatusError(
            "500", request=request, response=response
        )
        resp = _post(client)
        assert resp.status_code == 502
        assert resp.json() == {"detail": "HX engine unavailable"}

    def test_invalid_response_type_rejected_by_pydantic(self, client, fake_engine):
        resp = _post(client, body={"type": "not-a-valid-type", "values": {}})
        assert resp.status_code == 422
        fake_engine.respond_to_escalation.assert_not_called()
