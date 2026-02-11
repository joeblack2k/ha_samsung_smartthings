from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_SOUNDBAR_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .soundbar_local_api import AsyncSoundbarLocal

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SOUNDBAR_LOCAL or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL:
        coordinator = domain["coordinator"]
        soundbar: AsyncSoundbarLocal = domain["soundbar"]
        host = domain.get("host") or "soundbar"
        async_add_entities([SoundbarLocalSubPlusButton(coordinator, soundbar, host), SoundbarLocalSubMinusButton(coordinator, soundbar, host)])
        return

    entities: list[ButtonEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device

        # Frame TV: best-effort Art Mode button (tries multiple SmartThings methods).
        if dev.is_frame_tv() and (dev.has_capability("samsungvd.ambient") or dev.has_capability("custom.launchapp")):
            entities.append(SamsungSmartThingsArtModeButton(coordinator))

        # Soundbar: next input source
        if dev.has_capability("samsungvd.audioInputSource"):
            entities.append(SamsungSmartThingsNextInputButton(coordinator))

    async_add_entities(entities)


class SamsungSmartThingsArtModeButton(SamsungSmartThingsEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_art_mode"
        self._attr_name = "Art Mode"

    async def async_press(self) -> None:
        try:
            await self.device.set_art_mode()
        except Exception:
            _LOGGER.exception("Art Mode failed: device=%s", self.device.device_id)
        finally:
            await self.coordinator.async_request_refresh()


class SamsungSmartThingsNextInputButton(SamsungSmartThingsEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_next_input"
        self._attr_name = "Next Input Source"

    async def async_press(self) -> None:
        try:
            await self.device.send_command("samsungvd.audioInputSource", "setNextInputSource", arguments=None)
        except Exception:
            _LOGGER.exception("Next Input Source failed: device=%s", self.device.device_id)
        finally:
            await self.coordinator.async_request_refresh()


class _SoundbarLocalButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str, key: str, name: str) -> None:
        self._coordinator = coordinator
        self._soundbar = soundbar
        self._host = host
        self._attr_unique_id = f"soundbar_local_{host}_{key}"
        self._attr_name = name
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
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


class SoundbarLocalSubPlusButton(_SoundbarLocalButton):
    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "button_sub_plus", "Subwoofer +")
        self._attr_icon = "mdi:volume-plus"

    async def async_press(self) -> None:
        await self._soundbar.sub_plus()
        await self._coordinator.async_request_refresh()


class SoundbarLocalSubMinusButton(_SoundbarLocalButton):
    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "button_sub_minus", "Subwoofer -")
        self._attr_icon = "mdi:volume-minus"

    async def async_press(self) -> None:
        await self._soundbar.sub_minus()
        await self._coordinator.async_request_refresh()
