from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTRY_TYPE, DOMAIN, RearSpeakerMode, ENTRY_TYPE_FRAME_LOCAL, ENTRY_TYPE_SOUNDBAR_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .frame_local_api import AsyncFrameLocal, FrameLocalError, FrameLocalUnsupportedError
from .soundbar_local_api import AsyncSoundbarLocal
from .app_catalog import app_options, resolve_app


@dataclass(frozen=True, kw_only=True)
class SmartThingsSelect:
    capability: str
    attribute: str
    command: str
    arg_index: int
    name: str
    options_fn: Any
    current_fn: Any
    to_args_fn: Any


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_FRAME_LOCAL or domain.get("type") == ENTRY_TYPE_FRAME_LOCAL:
        coordinator = domain["coordinator"]
        frame: AsyncFrameLocal = domain["frame"]
        host = domain.get("host") or "frame"
        async_add_entities(
            [
                FrameLocalAppSelect(coordinator, frame, host),
                FrameLocalArtworkSelect(coordinator, frame, host),
                FrameLocalMatteSelect(coordinator, frame, host),
                FrameLocalPhotoFilterSelect(coordinator, frame, host),
            ]
        )
        return
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SOUNDBAR_LOCAL or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL:
        coordinator = domain["coordinator"]
        soundbar: AsyncSoundbarLocal = domain["soundbar"]
        host = domain.get("host") or "soundbar"
        # Build a reliable mode list once (set+read validation).
        try:
            modes = await soundbar.detect_supported_sound_modes()
            if modes:
                coordinator.data["supported_sound_modes"] = modes
        except Exception:
            pass
        async_add_entities([SoundbarLocalInputSelect(coordinator, soundbar, host), SoundbarLocalSoundModeSelect(coordinator, soundbar, host)])
        return

    is_local_soundbar_entry = (
        entry.data.get("entry_type") == ENTRY_TYPE_SOUNDBAR_LOCAL
        or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL
        or domain.get("host") is not None
    )
    entities: list[SelectEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device
        expose_all = bool(dev.runtime and dev.runtime.expose_all)

        # Picture/sound mode via SmartThings cloud is unreliable on some TVs: it may
        # report stale values and/or not apply changes. Hide these selects by default
        # and only expose them in "expose_all" (advanced) mode.
        if expose_all and dev.has_capability("custom.picturemode"):
            entities.append(SamsungSmartThingsSelect(coordinator, _picture_mode_desc()))
        if expose_all and dev.has_capability("custom.soundmode"):
            entities.append(SamsungSmartThingsSelect(coordinator, _sound_mode_desc()))
        # TV input source (Samsung map)
        if dev.has_capability("samsungvd.mediaInputSource"):
            entities.append(SamsungSmartThingsSelect(coordinator, _samsung_input_source_desc()))
        if dev.has_capability("custom.launchapp"):
            entities.append(SamsungTVAppSelect(coordinator))

        # Soundbar audio input source (cycle-based)
        # SmartThings cloud frequently fails to set inputs on many soundbars even if it can
        # list the supported sources. Only expose the dropdown for the local LAN soundbar mode
        # where it is deterministic. Cloud mode still exposes a read-only sensor + "Next input".
        if is_local_soundbar_entry and dev.has_capability("samsungvd.audioInputSource"):
            entities.append(SoundbarInputSourceSelect(coordinator))

        # Soundbar execute-based selects (only if execute is available)
        if dev.is_soundbar and dev.has_capability("execute"):
            entities.append(SoundbarSoundModeSelect(coordinator))
            entities.append(SoundbarEQPresetSelect(coordinator))
            entities.append(SoundbarRearSpeakerModeSelect(coordinator))

    async_add_entities(entities)


def _picture_mode_desc() -> SmartThingsSelect:
    def opts(d):
        lst = d.get_attr("custom.picturemode", "supportedPictureModes")
        if isinstance(lst, list) and all(isinstance(x, str) for x in lst):
            return lst
        m = d.get_attr("custom.picturemode", "supportedPictureModesMap")
        out = []
        if isinstance(m, list):
            for it in m:
                if isinstance(it, dict) and isinstance(it.get("name"), str):
                    out.append(it["name"])
        return out

    return SmartThingsSelect(
        capability="custom.picturemode",
        attribute="pictureMode",
        command="setPictureMode",
        arg_index=0,
        name="Picture Mode",
        options_fn=opts,
        current_fn=lambda d: d.get_attr("custom.picturemode", "pictureMode"),
        to_args_fn=lambda option, d: [option],
    )


def _sound_mode_desc() -> SmartThingsSelect:
    def opts(d):
        lst = d.get_attr("custom.soundmode", "supportedSoundModes")
        if isinstance(lst, list) and all(isinstance(x, str) for x in lst):
            return lst
        m = d.get_attr("custom.soundmode", "supportedSoundModesMap")
        out = []
        if isinstance(m, list):
            for it in m:
                if isinstance(it, dict) and isinstance(it.get("name"), str):
                    out.append(it["name"])
        return out

    return SmartThingsSelect(
        capability="custom.soundmode",
        attribute="soundMode",
        command="setSoundMode",
        arg_index=0,
        name="Sound Mode",
        options_fn=opts,
        current_fn=lambda d: d.get_attr("custom.soundmode", "soundMode"),
        to_args_fn=lambda option, d: [option],
    )


def _samsung_input_source_desc() -> SmartThingsSelect:
    def _map(d):
        m = d.get_attr("samsungvd.mediaInputSource", "supportedInputSourcesMap") or []
        out: list[tuple[str, str]] = []
        if isinstance(m, list):
            for it in m:
                if not isinstance(it, dict):
                    continue
                i = it.get("id")
                n = it.get("name")
                if isinstance(i, str) and i and isinstance(n, str) and n:
                    out.append((i, n))
        return out

    def opts(d):
        pairs = _map(d)
        # Prefer friendly names, but keep uniqueness by suffixing id when needed.
        names = [n for _i, n in pairs]
        dup = {n for n in names if names.count(n) > 1}
        out = []
        for i, n in pairs:
            out.append(f"{n} ({i})" if n in dup else n)
        return out

    def cur(d):
        cur_id = d.get_attr("samsungvd.mediaInputSource", "inputSource")
        if not isinstance(cur_id, str):
            return None
        pairs = _map(d)
        names = [n for _i, n in pairs]
        dup = {n for n in names if names.count(n) > 1}
        for i, n in pairs:
            if i == cur_id:
                return f"{n} ({i})" if n in dup else n
        return None

    def to_args(option: str, d):
        # option is either name or "name (id)"
        pairs = _map(d)
        for i, n in pairs:
            if option == n or option == f"{n} ({i})":
                return [i]
        return [option]

    return SmartThingsSelect(
        capability="samsungvd.mediaInputSource",
        attribute="inputSource",
        command="setInputSource",
        arg_index=0,
        name="Input Source",
        options_fn=opts,
        current_fn=cur,
        to_args_fn=to_args,
    )


class SamsungSmartThingsSelect(SamsungSmartThingsEntity, SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator, desc: SmartThingsSelect) -> None:
        super().__init__(coordinator)
        self.desc = desc
        self._attr_unique_id = f"{self.device.device_id}_select_{desc.capability}_{desc.attribute}"
        self._attr_name = desc.name

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        # TV picture/sound/input source settings return 409 when the TV is off.
        if self.desc.capability in ("custom.picturemode", "custom.soundmode", "samsungvd.mediaInputSource"):
            sw = self.device.get_attr("switch", "switch")
            return sw in ("on", True)
        return True

    @property
    def options(self) -> list[str]:
        opts = self.desc.options_fn(self.device)
        return [x for x in opts if isinstance(x, str)]

    @property
    def current_option(self) -> str | None:
        v = self.desc.current_fn(self.device)
        if not isinstance(v, str):
            return None
        # HA logs warnings if current_option is not in options.
        if v not in self.options:
            return None
        return v

    async def async_select_option(self, option: str) -> None:
        if self.desc.capability in ("custom.picturemode", "custom.soundmode", "samsungvd.mediaInputSource"):
            sw = self.device.get_attr("switch", "switch")
            if sw not in ("on", True):
                raise HomeAssistantError(f"{self.desc.name} is only available when the TV is on")
        args = self.desc.to_args_fn(option, self.device)
        try:
            await self.device.send_command(self.desc.capability, self.desc.command, arguments=args)
        except ClientResponseError as exc:
            if exc.status in (409, 422):
                raise HomeAssistantError(f"SmartThings rejected {self.desc.name} for this device state") from exc
            raise
        await self.coordinator.async_request_refresh()


class SamsungTVAppSelect(SamsungSmartThingsEntity, SelectEntity):
    """App launcher select for Samsung TVs via SmartThings cloud."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_select_tv_app"
        self._attr_name = "App"
        self._attr_options = app_options()

    @property
    def available(self) -> bool:
        return super().available and self.device.has_capability("custom.launchapp")

    @property
    def current_option(self) -> str | None:
        return None

    async def async_select_option(self, option: str) -> None:
        app = resolve_app(option)
        if app is None:
            raise HomeAssistantError(f"Unknown app option: {option}")
        try:
            await self.device.send_command(
                "custom.launchapp",
                "launchApp",
                arguments=[app.app_id, app.name],
            )
        except ClientResponseError as exc:
            if exc.status in (409, 422):
                raise HomeAssistantError("SmartThings rejected app launch for this device state") from exc
            raise
        await self.coordinator.async_request_refresh()


# ---- Soundbar-specific select entities ----


class SoundbarInputSourceSelect(SamsungSmartThingsEntity, SelectEntity):
    """Input source for soundbars using samsungvd.audioInputSource (cycle-based)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_select_audio_input_source"
        self._attr_name = "Audio Input Source"

    @property
    def options(self) -> list[str]:
        sources = self.device.get_attr("samsungvd.audioInputSource", "supportedInputSources")
        if isinstance(sources, list):
            return [s for s in sources if isinstance(s, str)]
        return []

    @property
    def current_option(self) -> str | None:
        v = self.device.get_attr("samsungvd.audioInputSource", "inputSource")
        if isinstance(v, str) and v in self.options:
            return v
        return None

    async def async_select_option(self, option: str) -> None:
        await self.device.select_audio_input_source(option)
        await self.coordinator.async_request_refresh()


class SoundbarSoundModeSelect(SamsungSmartThingsEntity, SelectEntity):
    """Execute-based sound mode for soundbars."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_select_sb_soundmode"
        self._attr_name = "Soundbar Sound Mode"

    @property
    def available(self) -> bool:
        return super().available and self.device._sb_execute_supported is not False and len(self.options) > 0

    @property
    def options(self) -> list[str]:
        opts = [s for s in self.device._sb_soundmodes if isinstance(s, str)]
        if opts:
            return opts
        current = self.device._sb_soundmode
        return [current] if isinstance(current, str) and current else []

    @property
    def current_option(self) -> str | None:
        v = self.device._sb_soundmode
        if isinstance(v, str) and v in self.options:
            return v
        return None

    async def async_select_option(self, option: str) -> None:
        try:
            await self.device.set_soundbar_soundmode(option)
        except ClientResponseError as exc:
            if exc.status in (409, 422):
                raise HomeAssistantError("SmartThings rejected sound mode for this device state") from exc
            raise
        await self.coordinator.async_request_refresh()


class SoundbarEQPresetSelect(SamsungSmartThingsEntity, SelectEntity):
    """Execute-based EQ preset for soundbars."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_select_sb_eq_preset"
        self._attr_name = "Equalizer Preset"

    @property
    def available(self) -> bool:
        return super().available and self.device._sb_execute_supported is not False and len(self.options) > 0

    @property
    def options(self) -> list[str]:
        return [s for s in self.device._sb_eq_presets if isinstance(s, str)]

    @property
    def current_option(self) -> str | None:
        v = self.device._sb_eq_preset
        if isinstance(v, str) and v in self.options:
            return v
        return None

    async def async_select_option(self, option: str) -> None:
        await self.device.set_eq_preset(option)
        await self.coordinator.async_request_refresh()


class SoundbarRearSpeakerModeSelect(SamsungSmartThingsEntity, SelectEntity):
    """Rear speaker mode (Front/Rear) for soundbars."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_select_sb_rear_speaker_mode"
        self._attr_name = "Rear Speaker Mode"

    @property
    def available(self) -> bool:
        return super().available and self.device._sb_execute_supported is True

    @property
    def options(self) -> list[str]:
        return [m.value for m in RearSpeakerMode]

    @property
    def current_option(self) -> str | None:
        # Not polled via execute — no read-back. Return None.
        return None

    async def async_select_option(self, option: str) -> None:
        mode = RearSpeakerMode(option)
        await self.device.set_rear_speaker_mode(mode)
        await self.coordinator.async_request_refresh()


class _SoundbarLocalSelect(SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str, key: str, name: str) -> None:
        self._coordinator = coordinator
        self._soundbar = soundbar
        self._host = host
        self._attr_unique_id = f"soundbar_local_{host}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"soundbar_local_{host}")},
            manufacturer="Samsung",
            model="Soundbar (Local)",
            name=f"Soundbar {host}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success


class SoundbarLocalInputSelect(_SoundbarLocalSelect):
    _SOURCES = ["HDMI_IN1", "HDMI_IN2", "E_ARC", "ARC", "D_IN", "BT", "WIFI_IDLE"]

    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "select_input", "Input Source")

    @property
    def options(self) -> list[str]:
        return list(self._SOURCES)

    @property
    def current_option(self) -> str | None:
        v = self._coordinator.data.get("input")
        return str(v) if isinstance(v, str) else None

    async def async_select_option(self, option: str) -> None:
        await self._soundbar.select_input(option)
        await self._coordinator.async_request_refresh()


class SoundbarLocalSoundModeSelect(_SoundbarLocalSelect):
    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "select_sound_mode", "Sound Mode")

    @property
    def options(self) -> list[str]:
        modes = self._coordinator.data.get("supported_sound_modes")
        if isinstance(modes, list):
            valid = [m for m in modes if isinstance(m, str) and m]
            if valid:
                return valid
        current = self.current_option
        return [current] if isinstance(current, str) and current else []

    @property
    def current_option(self) -> str | None:
        v = self._coordinator.data.get("sound_mode")
        return str(v) if isinstance(v, str) else None

    async def async_select_option(self, option: str) -> None:
        await self._soundbar.set_sound_mode(option)
        await self._coordinator.async_request_refresh()


class _FrameLocalSelect(SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str, key: str, name: str) -> None:
        self._coordinator = coordinator
        self._frame = frame
        self._host = host
        self._attr_unique_id = f"frame_local_{host}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"frame_local_{host}")},
            manufacturer="Samsung",
            model="The Frame (Local)",
            name=f"Frame TV {host}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._coordinator.data.get("online", False))


class FrameLocalAppSelect(_FrameLocalSelect):
    """Launch apps directly on Frame TV local websocket API."""

    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "app", "App")
        self._dynamic: list[str] = []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.hass is not None:
            self.hass.async_create_task(self._async_refresh_apps())

    async def _async_refresh_apps(self) -> None:
        try:
            apps = await self._frame.list_apps()
        except Exception:
            return
        out: list[str] = []
        for item in apps:
            if not isinstance(item, dict):
                continue
            app_id = item.get("appId") or item.get("app_id")
            name = item.get("name")
            if isinstance(app_id, str) and app_id and isinstance(name, str) and name:
                label = f"{name} ({app_id})"
                if label not in out:
                    out.append(label)
        if out:
            self._dynamic = out
            self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        # Dynamic installed-app list first, curated fallback second.
        if self._dynamic:
            return self._dynamic
        return app_options()

    @property
    def current_option(self) -> str | None:
        return None

    async def async_select_option(self, option: str) -> None:
        app = resolve_app(option)
        if app is None and option.endswith(")") and " (" in option:
            # Accept dynamic app labels even if not in curated list.
            app_id = option.rsplit("(", 1)[-1].rstrip(")").strip()
            if app_id:
                await self._frame.run_app(app_id)
                await self._coordinator.async_request_refresh()
                return
            raise HomeAssistantError(f"Unknown app option: {option}")
        if app is None:
            raise HomeAssistantError(f"Unknown app option: {option}")
        await self._frame.run_app(app.app_id)
        await self._coordinator.async_request_refresh()


class FrameLocalArtworkSelect(_FrameLocalSelect):
    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "artwork", "Artwork")

    @property
    def options(self) -> list[str]:
        data = self._coordinator.data.get("artwork_ids")
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
        return []

    @property
    def current_option(self) -> str | None:
        value = self._coordinator.data.get("current_artwork_id")
        if not isinstance(value, str) or not value:
            return None
        # Return current id even if options list is empty/incomplete on this firmware.
        return value

    async def async_select_option(self, option: str) -> None:
        try:
            await self._frame.select_artwork(option, True)
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to select artwork on Frame TV: {err}") from err
        await self._coordinator.async_request_refresh()


class FrameLocalMatteSelect(_FrameLocalSelect):
    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "matte", "Matte")
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def options(self) -> list[str]:
        data = self._coordinator.data.get("matte_options")
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
        return []

    @property
    def current_option(self) -> str | None:
        value = self._coordinator.data.get("current_matte")
        return value if isinstance(value, str) and value in self.options else None

    async def async_select_option(self, option: str) -> None:
        current_art = self._coordinator.data.get("current_artwork_id")
        if not isinstance(current_art, str) or not current_art:
            raise HomeAssistantError("No current artwork selected on TV")
        try:
            await self._frame.change_matte(current_art, option)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Matte change is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to change matte: {err}") from err
        await self._coordinator.async_request_refresh()


class FrameLocalPhotoFilterSelect(_FrameLocalSelect):
    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "photo_filter", "Photo Filter")
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def options(self) -> list[str]:
        data = self._coordinator.data.get("photo_filter_options")
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
        return []

    @property
    def current_option(self) -> str | None:
        value = self._coordinator.data.get("current_filter")
        return value if isinstance(value, str) and value in self.options else None

    async def async_select_option(self, option: str) -> None:
        current_art = self._coordinator.data.get("current_artwork_id")
        if not isinstance(current_art, str) or not current_art:
            raise HomeAssistantError("No current artwork selected on TV")
        try:
            await self._frame.set_photo_filter(current_art, option)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Photo filter is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set photo filter: {err}") from err
        await self._coordinator.async_request_refresh()
