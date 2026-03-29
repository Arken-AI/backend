"""
HTTP client for HX Engine microservice.
"""

import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class HXEngineClient:
    """
    Thin async HTTP client for the HX Engine microservice.

    Lifecycle: create once at startup via get_engine_client() in
    dependencies.py, reuse across requests, close on shutdown.
    """

    def __init__(self):
        self.base_url = settings.hx_engine_url
        self._client: httpx.AsyncClient | None = None

    async def connect(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
            headers={"X-Internal-Secret": settings.hx_engine_secret},
        )
        try:
            resp = await self._client.get("/health")
            resp.raise_for_status()
            logger.info("HX Engine connected: %s", self.base_url)
        except Exception as exc:
            logger.warning("HX Engine not available at %s: %s", self.base_url, exc)

    async def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def validate_requirements(self, user_id: str, **kwargs) -> dict:
        """
        POST /api/v1/hx/requirements → { valid, token?, errors?, warnings?, user_message? }

        kwargs: hot_fluid_name, cold_fluid_name, T_hot_in_C, T_cold_in_C,
                m_dot_hot_kg_s, and any optional HX fields.
        """
        if self._client is None:
            raise RuntimeError("HXEngineClient not connected — call connect() first")
        payload = {"user_id": user_id, **kwargs}
        resp = await self._client.post("/api/v1/hx/requirements", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def start_design(
        self,
        user_id: str,
        org_id: str | None = None,
        **kwargs,
    ) -> dict:
        """
        POST /api/v1/hx/design → { session_id, stream_url }

        kwargs: hot_fluid_name, cold_fluid_name, T_hot_in_C, T_cold_in_C,
                m_dot_hot_kg_s, token, and any other optional HX fields.
        The caller should pass stream_url back to the frontend so it can
        open an EventSource and receive step events.
        """
        if self._client is None:
            raise RuntimeError("HXEngineClient not connected — call connect() first")
        payload = {"user_id": user_id, **({"org_id": org_id} if org_id else {}), **kwargs}
        resp = await self._client.post("/api/v1/hx/design", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
