from __future__ import annotations

import json
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_BASE


class SmartThingsApi:
    """Minimal SmartThings REST client using Home Assistant's shared aiohttp session."""

    def __init__(self, hass: HomeAssistant, token: str) -> None:
        self._hass = hass
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    async def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> Any:
        session = async_get_clientsession(self._hass)
        url = path if path.startswith("http://") or path.startswith("https://") else f"{API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        async with session.request(method, url, headers=headers, json=json_body) as resp:
            resp.raise_for_status()
            # SmartThings sometimes returns empty body for 202.
            text = await resp.text()
            if not text:
                return None
            try:
                return json.loads(text)
            except Exception:
                return text

    async def list_devices(self) -> list[dict[str, Any]]:
        # Best effort pagination. In practice most accounts are small enough.
        items: list[dict[str, Any]] = []
        path = "/devices"
        while path:
            payload = await self._request("GET", path)
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                for it in payload["items"]:
                    if isinstance(it, dict):
                        items.append(it)
            next_href = None
            if isinstance(payload, dict):
                links = payload.get("_links")
                if isinstance(links, dict):
                    nxt = links.get("next")
                    if isinstance(nxt, dict):
                        next_href = nxt.get("href")
            if next_href and isinstance(next_href, str):
                # next_href is a full URL; convert to path.
                if next_href.startswith(API_BASE):
                    path = next_href[len(API_BASE) :]
                else:
                    # Fallback: treat as absolute and pass as path-less request
                    path = next_href
            else:
                path = ""
        return items

    async def get_device(self, device_id: str) -> dict[str, Any]:
        payload = await self._request("GET", f"/devices/{device_id}")
        if not isinstance(payload, dict):
            raise ClientResponseError(None, (), status=500, message="Invalid device payload")  # type: ignore[arg-type]
        return payload

    async def get_status(self, device_id: str) -> dict[str, Any]:
        payload = await self._request("GET", f"/devices/{device_id}/status")
        if not isinstance(payload, dict):
            raise ClientResponseError(None, (), status=500, message="Invalid status payload")  # type: ignore[arg-type]
        return payload

    async def get_capability_def(self, cap_id: str, version: int) -> dict[str, Any]:
        payload = await self._request("GET", f"/capabilities/{cap_id}/{version}")
        if not isinstance(payload, dict):
            raise ClientResponseError(None, (), status=500, message="Invalid capability def payload")  # type: ignore[arg-type]
        return payload

    async def send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> Any:
        return await self._request(
            "POST",
            f"/devices/{device_id}/commands",
            json_body={"commands": commands},
        )
