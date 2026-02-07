from __future__ import annotations

import datetime as dt
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .device import SmartThingsDevice

_LOGGER = logging.getLogger(__name__)


class SmartThingsCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, device: SmartThingsDevice, *, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device.device_id}",
            update_interval=dt.timedelta(seconds=max(5, int(scan_interval))),
        )
        self.device = device

    async def _async_update_data(self) -> dict:
        try:
            dev = await self.device.api.get_device(self.device.device_id)
            status = await self.device.api.get_status(self.device.device_id)
            self.device.update_runtime(dev, status)
            return {"device": dev, "status": status}
        except Exception as err:
            raise UpdateFailed(str(err)) from err
