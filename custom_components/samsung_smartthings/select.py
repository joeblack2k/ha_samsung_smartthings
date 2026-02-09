from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, RearSpeakerMode, ENTRY_TYPE_SOUNDBAR_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity


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
        args = self.desc.to_args_fn(option, self.device)
        await self.device.send_command(self.desc.capability, self.desc.command, arguments=args)
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
        return super().available and self.device._sb_execute_supported is True

    @property
    def options(self) -> list[str]:
        return [s for s in self.device._sb_soundmodes if isinstance(s, str)]

    @property
    def current_option(self) -> str | None:
        v = self.device._sb_soundmode
        if isinstance(v, str) and v in self.options:
            return v
        return None

    async def async_select_option(self, option: str) -> None:
        await self.device.set_soundbar_soundmode(option)
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
        return super().available and self.device._sb_execute_supported is True

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
