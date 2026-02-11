from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Iterable

from aiohttp import ClientResponseError
from homeassistant.exceptions import HomeAssistantError

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

    def __init__(
        self,
        api: SmartThingsApi,
        device_id: str,
        *,
        expose_all: bool,
        cloud_soundmodes: list[str] | None = None,
        device: dict[str, Any] | None = None,
    ) -> None:
        self.api = api
        self.device_id = device_id
        self.expose_all = expose_all
        self.runtime: DeviceRuntime | None = None
        self._device_prefetch = device if isinstance(device, dict) else None
        self._cloud_soundmodes = [s for s in (cloud_soundmodes or []) if isinstance(s, str) and s.strip()]

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
        self._sb_soundmode_validation_done: bool = False
        self._sb_last_soundmode_validation: float = 0.0

        # Throttle execute polling to avoid spamming the API.
        self._sb_last_execute_poll: float = 0.0

    async def async_init(self) -> None:
        # Prefer already-fetched device payload (from /devices list) to avoid extra calls.
        device = self._device_prefetch or await self.api.get_device(self.device_id)

        # Don't fetch status during config entry setup; the coordinator will do it and
        # handle SmartThings rate limits/backoff. Start with empty status.
        status: dict[str, Any] = {}

        # Capability definitions are global and costly to fetch (and SmartThings rate-limits).
        # We keep them empty by default and rely on status payload + curated mappings.
        cap_defs: dict[str, dict[str, Any]] = {}

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

    def is_frame_tv(self) -> bool:
        """Best-effort identification of The Frame TVs."""
        cat = self.get_attr("samsungvd.deviceCategory", "category")
        if isinstance(cat, str) and cat.lower() == "frametv":
            return True
        # fallback: model number prefix
        mn = self.get_attr("ocf", "mnmo")
        if isinstance(mn, str) and mn.upper().startswith("QE") and "LS03" in mn.upper():
            return True
        return False

    async def set_art_mode(self) -> None:
        """Best-effort Art/Ambient mode enable.

        SmartThings is inconsistent across models/accounts. Try:
        1) samsungvd.ambient / ambient18 setAmbientOn
        2) custom.launchapp launchApp(None, "Ambient Mode") and other common names
        """
        # Try the dedicated ambient capability first.
        for cap in ("samsungvd.ambient", "samsungvd.ambient18"):
            if not self.has_capability(cap):
                continue
            try:
                await self.send_command(cap, "setAmbientOn", arguments=None)
                return
            except ClientResponseError as exc:
                # Many Frame TVs return 422 NOT_FOUND even though the capability exists.
                if exc.status in (400, 404, 422):
                    pass
                else:
                    raise

        # Fallback: launch app by name (requires placeholder for optional appId).
        if self.has_capability("custom.launchapp"):
            for name in ("Ambient Mode", "Art Mode", "Art", "Ambient"):
                try:
                    await self.send_command("custom.launchapp", "launchApp", arguments=[None, name])
                    return
                except ClientResponseError as exc:
                    if exc.status in (400, 404, 422):
                        continue
                    raise

        raise RuntimeError("Art mode not supported by SmartThings for this device/account")

    async def exit_art_mode(self) -> None:
        """Best-effort to exit Art Mode (not reliably supported)."""
        # If remoteControl exists, HOME usually exits art mode.
        if self.has_capability("samsungvd.remoteControl"):
            await self.send_command("samsungvd.remoteControl", "send", arguments=["HOME", "PRESS_AND_RELEASED"])
            return
        # Otherwise no safe generic method.
        raise RuntimeError("Exit art mode is not supported")

    async def execute_query(self, href: str) -> dict[str, Any]:
        """Send an execute query and read the payload from execute.data.value."""
        await self.send_command("execute", "execute", arguments=[href])
        await asyncio.sleep(0.5)
        try:
            status = await self.api.get_status(self.device_id)
            self.update_runtime_status(status)
        except ClientResponseError as exc:
            # If rate-limited, keep last-known state and try again on next poll.
            if exc.status == 429:
                return {}
            raise

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
            # Treat execute as supported as soon as we can successfully call it, even if
            # SmartThings doesn't provide read-back state in the status payload.
            # Many soundbars return empty payloads while still accepting execute commands.
            if self._sb_execute_supported is None:
                self._sb_execute_supported = True

            # Sound mode
            payload = await self.execute_query(EXECUTE_SOUNDMODE)
            if payload:
                self._sb_soundmodes = list(payload.get("x.com.samsung.networkaudio.supportedSoundmode") or [])
                self._sb_soundmode = payload.get("x.com.samsung.networkaudio.soundmode")
            # Some models return an empty/null execute payload while commands still work.
            # Ensure fallback options are validated/exposed in that case as well.
            if not self._sb_soundmodes:
                await self._ensure_validated_soundmode_options()

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

        except ClientResponseError as exc:
            # Don't permanently disable execute features due to transient rate-limits.
            if exc.status == 429:
                return
            _LOGGER.debug("Execute-based features failed for %s (status=%s)", self.device_id, exc.status)
            self._sb_execute_supported = False
        except Exception:
            _LOGGER.debug("Execute-based features not available for %s", self.device_id)
            self._sb_execute_supported = False

    def _model_code(self) -> str:
        model = self.get_attr("ocf", "mnmo")
        if not isinstance(model, str) or not model:
            rt = self.runtime
            if rt and isinstance(rt.device, dict):
                ocf = rt.device.get("ocf")
                if isinstance(ocf, dict) and isinstance(ocf.get("modelNumber"), str):
                    model = ocf["modelNumber"]
        return model.upper() if isinstance(model, str) else ""

    def _fallback_soundmode_candidates(self) -> list[str]:
        """Model-aware fallback candidates used when ST omits supportedSoundmode."""
        if self._cloud_soundmodes:
            return list(self._cloud_soundmodes)
        model = self._model_code()
        base = ["STANDARD", "SURROUND", "GAME", "ADAPTIVE"]
        if model.startswith("HW-Q"):
            modes = base + ["DTS_VIRTUAL_X", "MUSIC", "CLEARVOICE", "MOVIE"]
        elif model.startswith("HW-S"):
            modes = base + ["MUSIC", "CLEARVOICE"]
        elif model.startswith("HW-"):
            modes = base + ["MUSIC"]
        else:
            modes = base
        # Add lowercase variants because some firmware only accepts lowercase mode values.
        out: list[str] = []
        for mode in modes:
            if mode not in out:
                out.append(mode)
            lower = mode.lower()
            if lower not in out:
                out.append(lower)
        return out

    async def _ensure_validated_soundmode_options(self) -> None:
        """Validate fallback sound modes by command+readback.

        We only run this when SmartThings did not provide supported modes.
        """
        now = asyncio.get_running_loop().time()
        if self._sb_soundmode_validation_done:
            return
        if now - self._sb_last_soundmode_validation < 21600:
            return
        self._sb_last_soundmode_validation = now

        # Avoid mode flapping while media is active; validate when idle/on.
        sw = self.get_attr("switch", "switch")
        if sw not in ("on", True):
            return
        thing_status = self.get_attr("samsungvd.thingStatus", "status")
        if isinstance(thing_status, str) and thing_status.lower() not in ("idle", "stopped", "ready"):
            return

        original = self._sb_soundmode if isinstance(self._sb_soundmode, str) else None
        validated: list[str] = []
        had_readback = False
        for mode in self._fallback_soundmode_candidates():
            try:
                await self.set_soundbar_soundmode(mode)
                await asyncio.sleep(0.6)
                payload = await self.execute_query(EXECUTE_SOUNDMODE)
                current = payload.get("x.com.samsung.networkaudio.soundmode") if isinstance(payload, dict) else None
                if isinstance(current, str):
                    had_readback = True
                    self._sb_soundmode = current
                if current == mode:
                    validated.append(mode)
            except ClientResponseError as exc:
                if exc.status in (400, 409, 422):
                    continue
                raise
            except Exception:
                continue

        if original:
            try:
                await self.set_soundbar_soundmode(original)
                self._sb_soundmode = original
            except Exception:
                pass
            if original not in validated:
                validated.insert(0, original)

        # Deduplicate, keep order.
        dedup: list[str] = []
        for mode in validated:
            if mode not in dedup:
                dedup.append(mode)
        if dedup:
            self._sb_soundmodes = dedup
        elif not had_readback:
            # SmartThings sometimes returns null execute data while still accepting soundmode
            # commands. In that case expose fallback candidates so users can try working values.
            fallback = []
            for mode in self._fallback_soundmode_candidates():
                if mode not in fallback:
                    fallback.append(mode)
            self._sb_soundmodes = fallback
        self._sb_soundmode_validation_done = True

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
            await self.send_command("samsungvd.audioInputSource", "setNextInputSource", arguments=None)
            # Give the device a moment to process. We avoid polling status for each step
            # to reduce SmartThings API rate-limit pressure.
            await asyncio.sleep(0.6)

        # Verify we actually reached the target (SmartThings sometimes accepts the command
        # but does not change state, e.g. when eARC / D.IN is locked by the TV).
        status = await self.api.get_status(self.device_id)
        self.update_runtime_status(status)
        after = self.get_attr("samsungvd.audioInputSource", "inputSource")
        if after != source:
            hint = ""
            if after == "D.IN" or source == "D.IN":
                hint = " (D.IN/eARC can be locked and not switchable via SmartThings)"
            raise HomeAssistantError(f"SmartThings did not change input source (current={after}, target={source}){hint}")
