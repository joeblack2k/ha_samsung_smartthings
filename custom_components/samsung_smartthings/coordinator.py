from __future__ import annotations

import datetime as dt
import logging

from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .device import SmartThingsDevice
from .smartthings_api import retry_after_seconds

_LOGGER = logging.getLogger(__name__)


class SmartThingsCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self,
        hass: HomeAssistant,
        device: SmartThingsDevice,
        *,
        hub_id: str,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device.device_id}",
            update_interval=dt.timedelta(seconds=max(5, int(scan_interval))),
        )
        self.device = device
        self.hub_id = hub_id

    async def _async_update_data(self) -> dict:
        try:
            status = await self.device.api.get_status(self.device.device_id)
            self.device.update_runtime_status(status)

            # Poll execute-based features for soundbars.
            if self.device.is_soundbar and self.device._sb_execute_supported is not False:
                try:
                    await self.device.update_execute_features()
                except Exception:
                    _LOGGER.debug("Execute features poll failed for %s", self.device.device_id)

            return {"status": status}
        except ClientResponseError as exc:
            # SmartThings rate-limits hard. Don't mark the device unavailable when we hit 429;
            # keep the last known state and back off.
            if exc.status == 429:
                ra = retry_after_seconds(exc) or 10.0
                # Increase interval for the next poll; keep a floor to avoid spamming.
                self.update_interval = dt.timedelta(seconds=max(float(ra), float(self.update_interval.total_seconds())))
                _LOGGER.debug("429 rate limited for %s; backing off %.1fs", self.device.device_id, ra)
                if self.data:
                    # Return a new object so listeners still get notified.
                    return dict(self.data)
                # No previous data: surface as failed so HA retries setup later.
                raise UpdateFailed(str(exc)) from exc
            raise UpdateFailed(str(exc)) from exc
        except Exception as err:
            raise UpdateFailed(str(err)) from err
