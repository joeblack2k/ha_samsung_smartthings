from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
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
    entities: list[SelectEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device

        # Picture mode
        if dev.has_capability("custom.picturemode"):
            entities.append(SamsungSmartThingsSelect(coordinator, _picture_mode_desc()))
        # Sound mode
        if dev.has_capability("custom.soundmode"):
            entities.append(SamsungSmartThingsSelect(coordinator, _sound_mode_desc()))
        # TV input source (Samsung map)
        if dev.has_capability("samsungvd.mediaInputSource"):
            entities.append(SamsungSmartThingsSelect(coordinator, _samsung_input_source_desc()))

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
        for i, n in _map(d):
            if i == cur_id:
                return n
        return cur_id

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
        return v if isinstance(v, str) else None

    async def async_select_option(self, option: str) -> None:
        args = self.desc.to_args_fn(option, self.device)
        await self.device.send_command(self.desc.capability, self.desc.command, arguments=args)
        await self.coordinator.async_request_refresh()
