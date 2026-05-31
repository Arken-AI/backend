"""
HX proxy router.

Thin pass-through layer that forwards user responses for ESCALATED HX
design steps from the browser to the HX Engine's `POST
/api/v1/hx/design/{session_id}/respond` endpoint. The proxy exists so we
have a single place to authenticate the caller (via the `X-Username`
header set by AuthContext on the frontend) and, in a follow-up epic
(EPIC-XSTACK-2026-007-S2), to pre-validate user-supplied property values
before they reach the engine.

Today the route is intentionally minimal: it requires `X-Username`,
relays the engine's status code + JSON body verbatim for 200/404/410/422,
and normalizes network / 5xx failures into a 502.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.engine_client import HXEngineClient
from app.dependencies import get_engine_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hx"])


class UserResponse(BaseModel):
    """
    Mirror of the HX Engine's UserResponse schema (see
    hx_design_engine/hx_engine/app/routers/design.py).

    Kept intentionally minimal — if the engine ever extends the `type`
    enum, this model must be updated in lock-step.
    """

    type: Literal["accept", "override", "skip"]
    values: dict[str, Any] | None = None


def _prevalidate_user_response(payload: UserResponse) -> None:
    """
    No-op pre-validation hook.

    EPIC-XSTACK-2026-007-S2 will implement acceptable-range checks on
    user-supplied property values here so invalid input never reaches
    the engine. Until then, this function intentionally does nothing
    and exists only to give S2 a single, obvious extension point.
    """
    return None


@router.post("/design/{session_id}/respond")
async def respond_to_escalation(
    session_id: str,
    response: UserResponse,
    x_username: str | None = Header(None, alias="X-Username"),
    engine: HXEngineClient = Depends(get_engine_client),
) -> JSONResponse:
    """
    Forward a user's answer for an ESCALATED step to the HX Engine.

    Requires `X-Username`. Relays the engine's status + body verbatim for
    200 (success), 404 (session not found), 410 (response window expired),
    and 422 (schema validation). Engine unreachable / 5xx → 502.
    """
    if not x_username:
        raise HTTPException(
            status_code=401, detail="X-Username header required"
        )

    _prevalidate_user_response(response)

    try:
        status_code, body = await engine.respond_to_escalation(
            session_id, response.model_dump()
        )
    except (httpx.HTTPError, httpx.RequestError) as exc:
        logger.warning(
            "HX engine unavailable for respond_to_escalation session_id=%s: %s",
            session_id,
            exc,
        )
        return JSONResponse(
            status_code=502, content={"detail": "HX engine unavailable"}
        )

    return JSONResponse(status_code=status_code, content=body)
