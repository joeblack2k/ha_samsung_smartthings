from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Iterable

from aiohttp import ClientResponseError

from .const import (
    EXECUTE_ADVANCED_AUDIO,
    EXECUTE_AVA,
    EXECUTE_CHANNEL_VOLUME,
    EXECUTE_EQ,
    EXECUTE_SOUNDMODE,
    EXECUTE_SPACE_FIT,
    EXECUTE_SURROUND_SPEAKER,
    EXECUTE_WOOFER,
    RearSpeakerMode,
    SpeakerIdentifier,
)
from .models import DeviceRuntime
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)


def _cap_key(cap_id: str, version: int) -> str:
    return f"{cap_id}/{version}"


class SmartThingsDevice:
    """Helper around SmartThings device + status payloads."""

    def __init__(self, api: SmartThingsApi, device_id: str, *, expose_all: bool) -> None:
        self.api = api
        self.device_id = device_id
        self.expose_all = expose_all
        self.runtime: DeviceRuntime | None = None

        # Serialize outgoing commands per device to reduce 409 conflicts.
        self._cmd_lock = asyncio.Lock()

        # Soundbar execute-based state (per-instance).
        self._sb_soundmodes: list[str] = []
        self._sb_soundmode: str | None = None
        self._sb_woofer_level: int | None = None
        self._sb_woofer_connection: str | None = None
        self._sb_eq_preset: str | None = None
        self._sb_eq_presets: list[str] = []
        self._sb_night_mode: int | None = None
        self._sb_bass_mode: int | None = None
        self._sb_voice_amplifier: int | None = None
        self._sb_execute_supported: bool | None = None

        # Throttle execute polling to avoid spamming the API.
        self._sb_last_execute_poll: float = 0.0

    async def async_init(self) -> None:
        device = await self.api.get_device(self.device_id)
        status = await self.api.get_status(self.device_id)

        cap_defs: dict[str, dict[str, Any]] = {}
        for cap_id, ver in self.iter_capabilities(device):
            try:
                cap_defs[_cap_key(cap_id, ver)] = await self.api.get_capability_def(cap_id, ver)
            except Exception:
                # Don't fail setup if one cap def fetch fails.
                cap_defs[_cap_key(cap_id, ver)] = {}

        self.runtime = DeviceRuntime(
            device_id=self.device_id,
            device=device,
            status=status,
            capability_defs=cap_defs,
            expose_all=self.expose_all,
        )

    def update_runtime_status(self, status: dict[str, Any]) -> None:
        if self.runtime is None:
            raise RuntimeError("update_runtime_status called before async_init")
        self.runtime.status = status

    @staticmethod
    def iter_capabilities(device: dict[str, Any]) -> Iterable[tuple[str, int]]:
        seen: set[tuple[str, int]] = set()
        for comp in device.get("components", []) or []:
            for cap in comp.get("capabilities", []) or []:
                cap_id = cap.get("id")
                ver = cap.get("version")
                if isinstance(cap_id, str) and cap_id and isinstance(ver, int):
                    t = (cap_id, ver)
                    if t not in seen:
                        seen.add(t)
                        yield t

    # -------- status helpers --------

    def _components(self) -> dict[str, Any]:
        if not self.runtime:
            return {}
        comps = self.runtime.status.get("components") if isinstance(self.runtime.status, dict) else None
        if not isinstance(comps, dict):
            return {}
        return comps

    def _component(self, component: str) -> dict[str, Any]:
        comp = self._components().get(component)
        return comp if isinstance(comp, dict) else {}

    def has_capability(self, cap_id: str) -> bool:
        if not self.runtime:
            return False
        dev = self.runtime.device
        for comp in dev.get("components", []) or []:
            for cap in comp.get("capabilities", []) or []:
                if cap.get("id") == cap_id:
                    return True
        return False

    def get_attr(self, cap_id: str, attr: str, *, component: str = "main") -> Any:
        cap = self._component(component).get(cap_id)
        if not isinstance(cap, dict):
            return None
        node = cap.get(attr)
        if not isinstance(node, dict):
            return None
        return node.get("value")

    def get_attr_unit(self, cap_id: str, attr: str, *, component: str = "main") -> str | None:
        cap = self._component(component).get(cap_id)
        if not isinstance(cap, dict):
            return None
        node = cap.get(attr)
        if not isinstance(node, dict):
            return None
        u = node.get("unit")
        return u if isinstance(u, str) else None

    def flatten_attributes(self) -> list[tuple[str, str, str, Any, str | None]]:
        """Return [(component, capability, attribute, value, unit)] for all components."""
        out: list[tuple[str, str, str, Any, str | None]] = []
        for comp_id, comp in self._components().items():
            if not isinstance(comp_id, str) or not isinstance(comp, dict):
                continue
            for cap_id, cap in comp.items():
                if not isinstance(cap_id, str) or not isinstance(cap, dict):
                    continue
                for attr, node in cap.items():
                    if not isinstance(attr, str) or not isinstance(node, dict):
                        continue
                    if "value" not in node:
                        continue
                    out.append((comp_id, cap_id, attr, node.get("value"), node.get("unit")))
        return out

    def get_capability_def(self, cap_id: str) -> dict[str, Any] | None:
        """Return the first matching capability definition for this device."""
        rt = self.runtime
        if not rt:
            return None
        for _k, capdef in (rt.capability_defs or {}).items():
            if isinstance(capdef, dict) and capdef.get("id") == cap_id:
                return capdef
        return None

    def get_command_def(self, cap_id: str, command: str) -> dict[str, Any] | None:
        capdef = self.get_capability_def(cap_id)
        if not isinstance(capdef, dict):
            return None
        cmds = capdef.get("commands")
        if not isinstance(cmds, dict):
            return None
        cmd = cmds.get(command)
        return cmd if isinstance(cmd, dict) else None

    # -------- command helpers --------

    async def send_command(
        self,
        capability: str,
        command: str,
        *,
        arguments: list[Any] | None = None,
        component: str = "main",
    ) -> None:
        cmd: dict[str, Any] = {
            "component": component,
            "capability": capability,
            "command": command,
        }
        if arguments is not None:
            cmd["arguments"] = arguments

        async with self._cmd_lock:
            # SmartThings conflicts are common; retry a few times.
            for attempt, delay in enumerate((0.0, 0.6, 1.6, 3.0), start=1):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    await self.api.send_commands(self.device_id, [cmd])
                    return
                except ClientResponseError as exc:
                    if exc.status in (409, 429, 503) and attempt < 4:
                        continue
                    raise

    async def raw_command_json(self, component: str, capability: str, command: str, args_json: str) -> None:
        args = None
        s = (args_json or "").strip()
        if s:
            parsed = json.loads(s)
            if parsed is None:
                args = None
            elif isinstance(parsed, list):
                args = parsed
            else:
                args = [parsed]
        await self.send_command(capability, command, arguments=args, component=component)

    # -------- execute-based (OCF soundbar) helpers --------

    @property
    def is_soundbar(self) -> bool:
        """True if device is a Samsung OCF soundbar (has audioInputSource but not tvChannel)."""
        return self.has_capability("samsungvd.audioInputSource") and not self.has_capability("tvChannel")

    async def execute_query(self, href: str) -> dict[str, Any]:
        """Send an execute query and read the payload from execute.data.value."""
        await self.send_command("execute", "execute", arguments=[href])
        await asyncio.sleep(0.5)
        status = await self.api.get_status(self.device_id)
        self.update_runtime_status(status)

        main = {}
        if isinstance(status, dict):
            comps = status.get("components")
            if isinstance(comps, dict):
                m = comps.get("main")
                if isinstance(m, dict):
                    main = m
        data = main.get("execute", {}).get("data", {}).get("value")
        if isinstance(data, dict):
            payload = data.get("payload")
            if isinstance(payload, dict):
                return payload
        return {}

    async def execute_set(self, href: str, prop: str, value: Any) -> None:
        """Set a value via execute capability."""
        await self.send_command("execute", "execute", arguments=[href, {prop: value}])

    async def update_execute_features(self) -> None:
        """Poll execute-based soundbar features. Called by coordinator."""
        if not self.has_capability("execute"):
            self._sb_execute_supported = False
            return
        if self._sb_execute_supported is False:
            return

        # Throttle heavy execute polling.
        now = asyncio.get_running_loop().time()
        if now - self._sb_last_execute_poll < 300.0:
            return
        self._sb_last_execute_poll = now

        try:
            # Sound mode
            payload = await self.execute_query(EXECUTE_SOUNDMODE)
            if payload:
                self._sb_execute_supported = True
                self._sb_soundmodes = list(payload.get("x.com.samsung.networkaudio.supportedSoundmode") or [])
                self._sb_soundmode = payload.get("x.com.samsung.networkaudio.soundmode")
            else:
                # First empty response — mark as unsupported to stop polling
                if self._sb_execute_supported is None:
                    self._sb_execute_supported = False
                    return

            # Woofer
            payload = await self.execute_query(EXECUTE_WOOFER)
            if payload:
                self._sb_woofer_level = payload.get("x.com.samsung.networkaudio.woofer")
                self._sb_woofer_connection = payload.get("x.com.samsung.networkaudio.connection")

            # EQ
            payload = await self.execute_query(EXECUTE_EQ)
            if payload:
                self._sb_eq_preset = payload.get("x.com.samsung.networkaudio.EQname")
                self._sb_eq_presets = list(payload.get("x.com.samsung.networkaudio.supportedList") or [])

            # Advanced audio
            payload = await self.execute_query(EXECUTE_ADVANCED_AUDIO)
            if payload:
                self._sb_night_mode = payload.get("x.com.samsung.networkaudio.nightmode")
                self._sb_bass_mode = payload.get("x.com.samsung.networkaudio.bassboost")
                self._sb_voice_amplifier = payload.get("x.com.samsung.networkaudio.voiceamplifier")

        except Exception:
            _LOGGER.debug("Execute-based features not available for %s", self.device_id)
            self._sb_execute_supported = False

    # -- Soundbar execute-based setters --

    async def set_soundbar_soundmode(self, mode: str) -> None:
        await self.execute_set(EXECUTE_SOUNDMODE, "x.com.samsung.networkaudio.soundmode", mode)

    async def set_woofer_level(self, level: int) -> None:
        await self.execute_set(EXECUTE_WOOFER, "x.com.samsung.networkaudio.woofer", level)

    async def set_eq_preset(self, preset: str) -> None:
        await self.execute_set(EXECUTE_EQ, "x.com.samsung.networkaudio.EQname", preset)

    async def set_night_mode(self, on: bool) -> None:
        await self.execute_set(EXECUTE_ADVANCED_AUDIO, "x.com.samsung.networkaudio.nightmode", 1 if on else 0)

    async def set_bass_mode(self, on: bool) -> None:
        await self.execute_set(EXECUTE_ADVANCED_AUDIO, "x.com.samsung.networkaudio.bassboost", 1 if on else 0)

    async def set_voice_amplifier(self, on: bool) -> None:
        await self.execute_set(EXECUTE_ADVANCED_AUDIO, "x.com.samsung.networkaudio.voiceamplifier", 1 if on else 0)

    async def set_active_voice_amplifier(self, on: bool) -> None:
        await self.execute_set(EXECUTE_AVA, "x.com.samsung.networkaudio.activeVoiceAmplifier", 1 if on else 0)

    async def set_space_fit_sound(self, on: bool) -> None:
        await self.execute_set(EXECUTE_SPACE_FIT, "x.com.samsung.networkaudio.spacefitSound", 1 if on else 0)

    async def set_speaker_level(self, speaker: SpeakerIdentifier, level: int) -> None:
        await self.execute_set(
            EXECUTE_CHANNEL_VOLUME,
            "x.com.samsung.networkaudio.channelVolume",
            [{"name": speaker.value, "value": level}],
        )

    async def set_rear_speaker_mode(self, mode: RearSpeakerMode) -> None:
        await self.execute_set(
            EXECUTE_SURROUND_SPEAKER,
            "x.com.samsung.networkaudio.currentRearPosition",
            mode.value,
        )

    async def select_audio_input_source(self, source: str) -> None:
        """Cycle through input sources via setNextInputSource."""
        sources = self.get_attr("samsungvd.audioInputSource", "supportedInputSources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("No supported input sources")
        current = self.get_attr("samsungvd.audioInputSource", "inputSource")
        if current == source:
            return
        if source not in sources:
            raise ValueError(f"Unsupported source: {source}")

        cur_idx = sources.index(current) if current in sources else 0
        tgt_idx = sources.index(source)
        steps = (tgt_idx - cur_idx) % len(sources)
        for _ in range(steps):
            await self.send_command("samsungvd.audioInputSource", "setNextInputSource", arguments=[])
            await asyncio.sleep(0.6)
            status = await self.api.get_status(self.device_id)
            self.update_runtime_status(status)
