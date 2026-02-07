from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
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
