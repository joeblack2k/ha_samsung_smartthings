"""
Local (LAN) client for Samsung 2024-line Wi-Fi soundbars.

This implements the same JSON-RPC API used by the SmartThings mobile app, over
HTTPS on port 1516. The soundbar uses a self-signed certificate by default.

Attribution:
  Based on the MIT-licensed project "Samsung Soundbar Local" by ZtF:
  https://github.com/ZtF/hass-samsung-soundbar-local
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import async_timeout


class SoundbarLocalError(Exception):
    """Raised when the soundbar local API returns an error."""


class AsyncSoundbarLocal:
    """Async client for the soundbar local JSON-RPC API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        port: int = 1516,
        verify_ssl: bool = False,
        timeout: int = 8,
    ) -> None:
        self._url = f"https://{host}:{port}/"
        self._session = session
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._token: str | None = None

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = json.dumps(payload, separators=(",", ":"))
        try:
            async with async_timeout.timeout(self._timeout):
                resp = await self._session.post(
                    self._url,
                    data=raw,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    ssl=self._verify_ssl,
                )
            resp.raise_for_status()
            data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SoundbarLocalError(str(err)) from err

        if isinstance(data, dict) and "error" in data:
            raise SoundbarLocalError(str(data["error"]))
        if not isinstance(data, dict) or "result" not in data:
            raise SoundbarLocalError(f"Unexpected response: {data!r}")
        res = data["result"]
        if not isinstance(res, dict):
            raise SoundbarLocalError(f"Unexpected result: {res!r}")
        return res

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        if method != "createAccessToken":
            if not self._token:
                await self.create_token()
            params.setdefault("AccessToken", self._token)

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": 1}
        if params:
            payload["params"] = params
        return await self._post(payload)

    async def create_token(self) -> str:
        self._token = (await self._call("createAccessToken"))["AccessToken"]
        return self._token

    # Power
    async def power_on(self) -> None:
        await self._call("powerControl", power="powerOn")

    async def power_off(self) -> None:
        await self._call("powerControl", power="powerOff")

    # Volume / mute
    async def volume_up(self) -> None:
        await self._call("remoteKeyControl", remoteKey="VOL_UP")

    async def volume_down(self) -> None:
        await self._call("remoteKeyControl", remoteKey="VOL_DOWN")

    async def mute_toggle(self) -> None:
        await self._call("remoteKeyControl", remoteKey="MUTE")

    async def set_volume(self, level: int) -> None:
        if not 0 <= level <= 100:
            raise ValueError("Volume has to be in range 0-100")
        current = await self.volume()
        # These soundbars appear to only accept step changes.
        while current != level:
            if current < level:
                await self.volume_up()
                current += 1
            else:
                await self.volume_down()
                current -= 1

    # Input / sound mode
    async def select_input(self, src: str) -> None:
        await self._call("inputSelectControl", inputSource=src)

    async def set_sound_mode(self, mode: str) -> None:
        await self._call("soundModeControl", soundMode=mode)

    # Getters
    async def volume(self) -> int:
        return int((await self._call("getVolume"))["volume"])

    async def is_muted(self) -> bool:
        return bool((await self._call("getMute"))["mute"])

    async def input(self) -> str:
        return str((await self._call("inputSelectControl"))["inputSource"])

    async def sound_mode(self) -> str:
        return str((await self._call("soundModeControl"))["soundMode"])

    async def power_state(self) -> str:
        return str((await self._call("powerControl"))["power"])

    async def codec(self) -> str | None:
        v = (await self._call("getCodec")).get("codec")
        return str(v) if isinstance(v, str) else None

    async def identifier(self) -> str | None:
        v = (await self._call("getIdentifier")).get("identifier")
        return str(v) if isinstance(v, str) else None

    async def status(self) -> dict[str, Any]:
        """Return a consolidated status dict."""
        return {
            "power": await self.power_state(),
            "volume": await self.volume(),
            "mute": await self.is_muted(),
            "input": await self.input(),
            "sound_mode": await self.sound_mode(),
            "codec": await self.codec(),
            "identifier": await self.identifier(),
        }

