from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_ID,
    CONF_EXPOSE_ALL,
    CONF_TOKEN,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SmartThingsCoordinator
from .device import SmartThingsDevice
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Never log secrets (token is stored in entry.data).
    if _LOGGER.isEnabledFor(logging.DEBUG):
        redacted = dict(entry.data)
        if CONF_TOKEN in redacted:
            redacted[CONF_TOKEN] = "***REDACTED***"
        _LOGGER.debug("[%s] setup entry: %s", DOMAIN, redacted)

    api = SmartThingsApi(hass, entry.data[CONF_TOKEN])
    dev = SmartThingsDevice(api, entry.data[CONF_DEVICE_ID], expose_all=entry.data.get(CONF_EXPOSE_ALL, True))
    await dev.async_init()

    coordinator = SmartThingsCoordinator(hass, dev)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "device": dev,
        "coordinator": coordinator,
    }

    async def _raw_command(call) -> None:
        component = str(call.data.get("component", "main"))
        capability = str(call.data["capability"])
        command = str(call.data["command"])
        args_json = str(call.data.get("args_json", "") or "")
        await dev.raw_command_json(component, capability, command, args_json)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        "raw_command",
        _raw_command,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.data.pop(DOMAIN, None)
    return unload_ok
