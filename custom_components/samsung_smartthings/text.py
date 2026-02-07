from __future__ import annotations

from homeassistant.components.text import TextEntity
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
    entities: list[TextEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device
        if dev.has_capability("tvChannel"):
            entities.append(SamsungSmartThingsTvChannelText(coordinator))

    async_add_entities(entities)


class SamsungSmartThingsTvChannelText(SamsungSmartThingsEntity, TextEntity):
    _attr_has_entity_name = True
    _attr_native_min = 0
    _attr_native_max = 32
    _attr_pattern = ".*"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_text_tv_channel"
        self._attr_name = "TV Channel"

    @property
    def native_value(self) -> str | None:
        v = self.device.get_attr("tvChannel", "tvChannel")
        return v if isinstance(v, str) else None

    async def async_set_value(self, value: str) -> None:
        # SmartThings tvChannel.setTvChannel expects a string in most definitions.
        await self.device.send_command("tvChannel", "setTvChannel", arguments=[value])
        await self.coordinator.async_request_refresh()
