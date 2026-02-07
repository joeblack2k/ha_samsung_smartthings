from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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

    entities: list[SwitchEntity] = []
    if dev.has_capability("switch"):
        entities.append(SamsungSmartThingsPowerSwitch(coordinator))

    async_add_entities(entities)


class SamsungSmartThingsPowerSwitch(SamsungSmartThingsEntity, SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch"
        self._attr_name = "Power"

    @property
    def is_on(self) -> bool | None:
        v = self.device.get_attr("switch", "switch")
        if v in ("on", True):
            return True
        if v in ("off", False):
            return False
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.device.send_command("switch", "on", arguments=[])
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.device.send_command("switch", "off", arguments=[])
        await self.coordinator.async_request_refresh()

