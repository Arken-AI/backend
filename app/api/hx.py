"""
HX Design API Endpoints (EPIC-XSTACK-2026-007-S2)

Two endpoints:
  POST /api/hx/properties/validate
      Validate a property dict without forwarding to the engine.

  POST /api/hx/design/{session_id}/respond
      Validate any user-provided properties then proxy the response
      to the HX Engine respond endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from app.dependencies import get_engine_client
from app.services.property_validator import validate_fluid_properties

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class PropertyValidateRequest(BaseModel):
    properties: dict[str, Any]
    fluid_side: Optional[str] = None  # "hot" | "cold" (informational only for validate-only)


class PropertyValidateResponse(BaseModel):
    valid: bool
    errors: list[str]
    cleaned_values: dict[str, float]


class RespondRequest(BaseModel):
    type: str  # "override" | "accept" | "skip"
    values: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helper — require a non-empty X-Username header (lightweight auth)
# ---------------------------------------------------------------------------

def _require_username(
    x_username: Optional[str] = Header(None, alias="X-Username"),
) -> str:
    """Return the username from the X-Username header or raise 401."""
    if not x_username or not x_username.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Username header is required.",
        )
    return x_username.strip()


# ---------------------------------------------------------------------------
# POST /api/hx/properties/validate
# ---------------------------------------------------------------------------

@router.post(
    "/properties/validate",
    response_model=PropertyValidateResponse,
    summary="Validate fluid properties",
    description=(
        "Validate a property dict against physical bounds. "
        "Returns errors and cleaned values without forwarding to the engine."
    ),
)
async def validate_properties(
    body: PropertyValidateRequest,
    _username: str = Depends(_require_username),
) -> PropertyValidateResponse:
    """Validate fluid properties without touching the HX Engine."""
    valid, errors, cleaned = validate_fluid_properties(body.properties)
    return PropertyValidateResponse(valid=valid, errors=errors, cleaned_values=cleaned)


# ---------------------------------------------------------------------------
# POST /api/hx/design/{session_id}/respond
# ---------------------------------------------------------------------------

@router.post(
    "/design/{session_id}/respond",
    summary="Respond to a design escalation",
    description=(
        "Validate any user-supplied fluid properties, then proxy the response "
        "to the HX Engine.  Returns HTTP 422 if property validation fails; "
        "engine errors surface as HTTP 502."
    ),
)
async def respond_to_design(
    session_id: str,
    body: RespondRequest,
    _username: str = Depends(_require_username),
    engine_client=Depends(get_engine_client),
) -> dict:
    """Validate + proxy a user escalation response to the HX Engine.

    Logic:
    1. If ``body.type == "override"`` and ``body.values`` contains a
       ``"properties"`` key: validate the properties dict.
    2. If invalid: return HTTP 422 with errors — engine is NOT called.
    3. Otherwise: forward the full payload to the engine and return its response.
    """
    # Step 1: Optional property pre-validation for "override" responses
    if (
        body.type == "override"
        and body.values is not None
        and "properties" in body.values
    ):
        props = body.values["properties"]
        valid, errors, _cleaned = validate_fluid_properties(props)
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"valid": False, "errors": errors},
            )

    # Step 2: Forward to engine
    payload = body.model_dump(exclude_none=False)
    try:
        result = await engine_client.respond_to_design(session_id, payload)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 410:
            raise HTTPException(
                status_code=410,
                detail="Response window expired — the design step has already timed out.",
            ) from exc
        logger.error(
            "Engine returned %s for respond on session %s: %s",
            exc.response.status_code, session_id, exc.response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Engine error: {exc.response.status_code}",
        ) from exc
    except Exception as exc:
        logger.error(
            "Unexpected error forwarding respond to engine for session %s: %s",
            session_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Engine is unavailable. Please try again.",
        ) from exc

    return result
