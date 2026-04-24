"""Async HTTP client for the Battery Badger REST API."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class BatteryBadgerApiError(Exception):
    """Base class for API errors the coordinator needs to reason about."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BatteryBadgerAuthError(BatteryBadgerApiError):
    """401/403 — the token is invalid or was revoked."""


class BatteryBadgerConflictError(BatteryBadgerApiError):
    """409 — e.g. duplicate reading or no-readings-yet on action-schedule."""


class BatteryBadgerClient:
    """Thin async wrapper around the Battery Badger API.

    One client per config entry. Token auth only — the server issues these
    and the user pastes them into the config flow; we don't do the JWT
    refresh dance.
    """

    def __init__(self, session: aiohttp.ClientSession, server_url: str, api_token: str):
        self._session = session
        self._server_url = server_url.rstrip("/")
        self._token = api_token

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, json: dict | None = None) -> Any:
        url = f"{self._server_url}{path}"
        try:
            async with self._session.request(
                method, url, headers=self.headers, json=json, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                body: Any
                if resp.content_type and "json" in resp.content_type:
                    body = await resp.json(content_type=None)
                else:
                    body = await resp.text()
                if resp.status == 401 or resp.status == 403:
                    raise BatteryBadgerAuthError(str(body), resp.status)
                if resp.status == 409:
                    raise BatteryBadgerConflictError(str(body), resp.status)
                if resp.status >= 400:
                    raise BatteryBadgerApiError(f"{resp.status} {body}", resp.status)
                return body
        except aiohttp.ClientError as exc:
            raise BatteryBadgerApiError(f"transport error: {exc}") from exc

    async def whoami(self) -> dict:
        """Validate the token; returns the user object on success."""
        return await self._request("GET", "/api/v1/auth/me/")

    async def list_installations(self) -> list[dict]:
        data = await self._request("GET", "/api/v1/installations/")
        # DRF list can be paginated or flat depending on the viewset config
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data or []

    async def post_reading(
        self,
        installation_id: int,
        taken_at: datetime,
        usage_wh: int,
        solar_wh: int,
        battery_soc_percent: float,
    ) -> dict:
        payload = {
            "taken_at": taken_at.isoformat().replace("+00:00", "Z"),
            "usage_wh": usage_wh,
            "solar_wh": solar_wh,
            "battery_soc_percent": battery_soc_percent,
        }
        return await self._request(
            "POST", f"/api/v1/installations/{installation_id}/readings/", json=payload
        )

    async def get_action_schedule(self, installation_id: int) -> list[dict]:
        return await self._request(
            "POST", f"/api/v1/installations/{installation_id}/action-schedule/"
        )
