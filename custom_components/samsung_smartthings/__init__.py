from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_ID,
    CONF_EXPOSE_ALL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SmartThingsCoordinator
from .device import SmartThingsDevice
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def _raw_command(call) -> None:
        # Resolve device from device_id or entity_id.
        device_id = call.data.get("device_id")
        if isinstance(device_id, str) and device_id:
            match = None
            for e in hass.data.get(DOMAIN, {}).values():
                if e.get("device").device_id == device_id:
                    match = e
                    break
            if not match:
                raise ValueError(f"Unknown device_id: {device_id}")
            dev = match["device"]
            coordinator = match["coordinator"]
        else:
            entity_ids = []
            if isinstance(call.data.get("entity_id"), str):
                entity_ids = [call.data["entity_id"]]
            elif isinstance(call.data.get("entity_id"), list):
                entity_ids = [x for x in call.data["entity_id"] if isinstance(x, str)]
            elif call.target and call.target.entity_ids:
                entity_ids = list(call.target.entity_ids)
            if not entity_ids:
                raise ValueError("Provide device_id or entity_id")

            from homeassistant.helpers import entity_registry as er

            reg = er.async_get(hass)
            ent = reg.async_get(entity_ids[0])
            if not ent or not ent.config_entry_id:
                raise ValueError(f"Entity not found or not linked to config entry: {entity_ids[0]}")
            match = hass.data.get(DOMAIN, {}).get(ent.config_entry_id)
            if not match:
                raise ValueError(f"Config entry not loaded: {ent.config_entry_id}")
            dev = match["device"]
            coordinator = match["coordinator"]

        component = str(call.data.get("component", "main"))
        capability = str(call.data["capability"])
        command = str(call.data["command"])
        args_json = str(call.data.get("args_json", "") or "")
        await dev.raw_command_json(component, capability, command, args_json)
        await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, "raw_command"):
        hass.services.async_register(DOMAIN, "raw_command", _raw_command)

    return True

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

    coordinator = SmartThingsCoordinator(hass, dev, scan_interval=entry.data.get(CONF_SCAN_INTERVAL, 15))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "device": dev,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.data.pop(DOMAIN, None)
    return unload_ok
