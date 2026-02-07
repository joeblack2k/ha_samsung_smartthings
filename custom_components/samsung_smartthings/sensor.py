from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity


@dataclass(frozen=True, kw_only=True)
class SmartThingsAttr:
    capability: str
    attribute: str
    unit: str | None


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartThingsCoordinator = domain["coordinator"]
    dev = coordinator.device

    entities: list[SamsungSmartThingsAttrSensor] = []

    # Expose *all* attributes as sensors when enabled.
    if dev.runtime and dev.runtime.expose_all:
        seen: set[tuple[str, str]] = set()
        for cap, attr, _val, unit in dev.flatten_attributes():
            key = (cap, attr)
            if key in seen:
                continue
            seen.add(key)
            entities.append(
                SamsungSmartThingsAttrSensor(
                    coordinator,
                    SmartThingsAttr(capability=cap, attribute=attr, unit=unit),
                )
            )

    # A few useful "always-on" device info sensors (even if expose_all is off).
    entities.append(SamsungSmartThingsSimpleSensor(coordinator, "ocf_mnmo", "OCF Model", lambda d: d.get_attr("ocf", "mnmo")))
    entities.append(SamsungSmartThingsSimpleSensor(coordinator, "ocf_mnfv", "Firmware", lambda d: d.get_attr("ocf", "mnfv")))
    entities.append(SamsungSmartThingsSimpleSensor(coordinator, "thing_status", "Thing Status", lambda d: d.get_attr("samsungvd.thingStatus", "status")))

    async_add_entities(entities)


class SamsungSmartThingsAttrSensor(SamsungSmartThingsEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator, desc: SmartThingsAttr) -> None:
        super().__init__(coordinator)
        self.desc = desc
        self._attr_unique_id = f"{self.device.device_id}_attr_{desc.capability}_{desc.attribute}"
        self._attr_name = f"{desc.capability}.{desc.attribute}"
        self._attr_native_unit_of_measurement = desc.unit

    @property
    def native_value(self) -> Any:
        return self.device.get_attr(self.desc.capability, self.desc.attribute)


class SamsungSmartThingsSimpleSensor(SamsungSmartThingsEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator, key: str, name: str, fn) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{self.device.device_id}_{key}"
        self._attr_name = name
        self._fn = fn

    @property
    def native_value(self) -> Any:
        return self._fn(self.device)

