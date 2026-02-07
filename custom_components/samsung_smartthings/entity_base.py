from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .device import SmartThingsDevice


class SamsungSmartThingsEntity(CoordinatorEntity[SmartThingsCoordinator]):
    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self.device: SmartThingsDevice = coordinator.device

    @property
    def device_info(self):
        rt = self.device.runtime
        if not rt:
            return None
        dev = rt.device
        return {
            "identifiers": {(DOMAIN, self.device.device_id)},
            "name": dev.get("label") or dev.get("name") or self.device.device_id,
            "manufacturer": dev.get("manufacturerName"),
            "model": self.device.get_attr("ocf", "mnmo") or dev.get("model") or dev.get("deviceTypeName"),
            "sw_version": self.device.get_attr("ocf", "mnfv"),
        }

