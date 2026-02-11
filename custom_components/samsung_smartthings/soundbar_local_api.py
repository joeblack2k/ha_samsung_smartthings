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
        self._supported_sound_modes: list[str] | None = None
        self._last_sound_mode_probe: float = 0.0
        self._night_mode: bool | None = None

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

    async def _post_any(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST payload and return raw JSON object (for non-JSON-RPC variants)."""
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

        if isinstance(data, dict):
            return data
        raise SoundbarLocalError(f"Unexpected response: {data!r}")

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

    async def sub_plus(self) -> None:
        await self._call("remoteKeyControl", remoteKey="WOOFER_PLUS")

    async def sub_minus(self) -> None:
        await self._call("remoteKeyControl", remoteKey="WOOFER_MINUS")

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

    async def set_advanced_sound_settings(self, settings: dict[str, Any]) -> None:
        """Best-effort advanced settings call exposed by some firmware."""
        await self._call("setAdvancedSoundSettings", **settings)

    async def set_night_mode(self, enabled: bool) -> None:
        """Set night mode via available local methods.

        Firmware varies by model; we try both known patterns.
        """
        target = "on" if enabled else "off"
        # Pattern 1: explicit local method.
        try:
            await self.set_advanced_sound_settings({"nightMode": target})
            self._night_mode = enabled
            return
        except Exception:
            pass

        # Pattern 2: app-style event payload.
        payload = {
            "method": "ms.channel.emit",
            "params": {
                "event": "ed.installedApp.event",
                "to": "host",
                "data": {
                    "component": "audio",
                    "capability": "custom1",
                    "command": "setNightMode",
                    "arguments": [target],
                },
            },
        }
        await self._post_any(payload)
        self._night_mode = enabled

    @staticmethod
    def default_sound_mode_candidates() -> list[str]:
        """Conservative candidate list for modern Samsung soundbars."""
        return [
            "STANDARD",
            "SURROUND",
            "GAME",
            "ADAPTIVE",
            "ADAPTIVE SOUND",
            "DTS_VIRTUAL_X",
            "MUSIC",
            "CLEARVOICE",
            "MOVIE",
        ]

    async def detect_supported_sound_modes(self, *, force: bool = False) -> list[str]:
        """Discover supported sound modes by set+read validation.

        This avoids showing modes that are not actually accepted by the device.
        """
        now = asyncio.get_running_loop().time()
        if not force and self._supported_sound_modes is not None:
            return list(self._supported_sound_modes)
        if not force and now - self._last_sound_mode_probe < 900:
            return list(self._supported_sound_modes or [])
        self._last_sound_mode_probe = now

        try:
            if await self.power_state() != "powerOn":
                return list(self._supported_sound_modes or [])
        except Exception:
            return list(self._supported_sound_modes or [])

        current = None
        try:
            current = await self.sound_mode()
        except Exception:
            current = None

        validated: list[str] = []
        for mode in self.default_sound_mode_candidates():
            try:
                await self.set_sound_mode(mode)
                await asyncio.sleep(0.35)
                observed = await self.sound_mode()
                if observed == mode:
                    validated.append(mode)
            except Exception:
                continue

        if current:
            try:
                await self.set_sound_mode(current)
            except Exception:
                pass
            if current not in validated:
                validated.insert(0, current)

        # Keep order, remove duplicates.
        dedup: list[str] = []
        for mode in validated:
            if mode not in dedup:
                dedup.append(mode)
        self._supported_sound_modes = dedup
        return list(self._supported_sound_modes)

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
        power = await self.power_state()
        data = {
            "power": power,
            "volume": await self.volume(),
            "mute": await self.is_muted(),
            "input": await self.input(),
            "sound_mode": await self.sound_mode(),
            "codec": await self.codec(),
            "identifier": await self.identifier(),
        }
        # If the device is on, try to build/refresh the validated mode list (throttled).
        if power == "powerOn":
            try:
                modes = await self.detect_supported_sound_modes()
                if modes:
                    data["supported_sound_modes"] = modes
            except Exception:
                pass
        if self._supported_sound_modes:
            data["supported_sound_modes"] = list(self._supported_sound_modes)
        if self._night_mode is not None:
            data["night_mode"] = self._night_mode
        return data
