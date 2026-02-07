from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartThingsCoordinator = domain["coordinator"]
    dev = coordinator.device

    entities: list[NumberEntity] = []

    # Soundbar volume (absolute) as number is redundant with media_player, but useful for raw control.
    if dev.has_capability("audioVolume") and dev.runtime and dev.runtime.expose_all:
        entities.append(SamsungSmartThingsVolumeNumber(coordinator))

    async_add_entities(entities)


class SamsungSmartThingsVolumeNumber(SamsungSmartThingsEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = "slider"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_number_volume"
        self._attr_name = "Volume"

    @property
    def native_value(self) -> float | None:
        v = self.device.get_attr("audioVolume", "volume")
        try:
            return float(v)
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.device.send_command("audioVolume", "setVolume", arguments=[int(value)])
        await self.coordinator.async_request_refresh()

