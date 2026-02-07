from __future__ import annotations

import logging

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_IDS,
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

    async def _resolve_device(call):
        # Resolve device from device_id or entity_id.
        device_id = call.data.get("device_id")
        if isinstance(device_id, str) and device_id:
            for e in hass.data.get(DOMAIN, {}).values():
                for it in e.get("items") or []:
                    dev = it.get("device")
                    if dev and dev.device_id == device_id:
                        return dev, it["coordinator"]
            raise ValueError(f"Unknown device_id: {device_id}")

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
        # If a config entry contains multiple devices, try to resolve by device_id in call first.
        items = match.get("items") or []
        if not items:
            raise ValueError(f"Config entry has no devices: {ent.config_entry_id}")

        # Try to match entity's HA device to a SmartThings device_id.
        if ent.device_id:
            from homeassistant.helpers import device_registry as dr
            dev_reg = dr.async_get(hass)
            ha_dev = dev_reg.async_get(ent.device_id)
            if ha_dev:
                for item in items:
                    st_did = item["device"].device_id
                    if (DOMAIN, st_did) in ha_dev.identifiers:
                        return item["device"], item["coordinator"]
        # Fallback to first device in this config entry.
        return items[0]["device"], items[0]["coordinator"]

    async def _raw_command(call) -> None:
        dev, coordinator = await _resolve_device(call)

        component = str(call.data.get("component", "main"))
        capability = str(call.data["capability"])
        command = str(call.data["command"])
        args_json = str(call.data.get("args_json", "") or "")
        await dev.raw_command_json(component, capability, command, args_json)
        await coordinator.async_request_refresh()

    async def _play_track(call) -> None:
        dev, coordinator = await _resolve_device(call)
        uri = str(call.data["uri"])
        level = call.data.get("level")
        args = [uri] + ([int(level)] if level is not None else [])
        await dev.send_command("audioNotification", "playTrack", arguments=args)
        await coordinator.async_request_refresh()

    async def _play_track_and_restore(call) -> None:
        dev, coordinator = await _resolve_device(call)
        uri = str(call.data["uri"])
        level = call.data.get("level")
        args = [uri] + ([int(level)] if level is not None else [])
        await dev.send_command("audioNotification", "playTrackAndRestore", arguments=args)
        await coordinator.async_request_refresh()

    async def _play_track_and_resume(call) -> None:
        dev, coordinator = await _resolve_device(call)
        uri = str(call.data["uri"])
        level = call.data.get("level")
        args = [uri] + ([int(level)] if level is not None else [])
        await dev.send_command("audioNotification", "playTrackAndResume", arguments=args)
        await coordinator.async_request_refresh()

    async def _launch_app(call) -> None:
        dev, coordinator = await _resolve_device(call)
        app_id = call.data.get("app_id")
        app_name = call.data.get("app_name")
        args = []
        if app_id:
            args.append(str(app_id))
        if app_name:
            args.append(str(app_name))
        await dev.send_command("custom.launchapp", "launchApp", arguments=args if args else None)
        await coordinator.async_request_refresh()

    async def _set_ambient_content(call) -> None:
        dev, coordinator = await _resolve_device(call)
        data_json = str(call.data["data_json"])
        # setAmbientContent expects a single object argument.
        await dev.raw_command_json("main", "samsungvd.ambientContent", "setAmbientContent", data_json)
        await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, "raw_command"):
        hass.services.async_register(DOMAIN, "raw_command", _raw_command)
    if not hass.services.has_service(DOMAIN, "play_track"):
        hass.services.async_register(DOMAIN, "play_track", _play_track)
    if not hass.services.has_service(DOMAIN, "play_track_and_restore"):
        hass.services.async_register(DOMAIN, "play_track_and_restore", _play_track_and_restore)
    if not hass.services.has_service(DOMAIN, "play_track_and_resume"):
        hass.services.async_register(DOMAIN, "play_track_and_resume", _play_track_and_resume)
    if not hass.services.has_service(DOMAIN, "launch_app"):
        hass.services.async_register(DOMAIN, "launch_app", _launch_app)
    if not hass.services.has_service(DOMAIN, "set_ambient_content"):
        hass.services.async_register(DOMAIN, "set_ambient_content", _set_ambient_content)

    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Never log secrets (token is stored in entry.data).
    if _LOGGER.isEnabledFor(logging.DEBUG):
        redacted = dict(entry.data)
        if CONF_TOKEN in redacted:
            redacted[CONF_TOKEN] = "***REDACTED***"
        _LOGGER.debug("[%s] setup entry: %s", DOMAIN, redacted)

    api = SmartThingsApi(hass, entry.data[CONF_TOKEN])

    # Migration: older versions stored multiple devices under one config entry.
    # We now create one config entry per device. If we detect the legacy format,
    # split it into import flows and remove the legacy entry.
    legacy_ids: list[str] = []
    if isinstance(entry.data.get(CONF_DEVICE_IDS), list):
        legacy_ids = [d for d in entry.data[CONF_DEVICE_IDS] if isinstance(d, str) and d]
    if legacy_ids:
        _LOGGER.warning(
            "[%s] Legacy multi-device config entry detected (%d devices). "
            "Splitting into per-device entries and removing the legacy entry: %s",
            DOMAIN,
            len(legacy_ids),
            entry.title,
        )
        token = entry.data.get(CONF_TOKEN)
        expose_all = bool(entry.data.get(CONF_EXPOSE_ALL, True))
        scan_interval = int(entry.data.get(CONF_SCAN_INTERVAL, 15))
        for did in legacy_ids:
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    data={
                        CONF_TOKEN: token,
                        CONF_DEVICE_ID: did,
                        # Name can be resolved later; keep stable now.
                        CONF_DEVICE_NAME: did,
                        CONF_EXPOSE_ALL: expose_all,
                        CONF_SCAN_INTERVAL: scan_interval,
                    },
                )
            )

        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        return True

    did = entry.data.get(CONF_DEVICE_ID)
    if not isinstance(did, str) or not did:
        _LOGGER.error("[%s] Missing %s in config entry %s", DOMAIN, CONF_DEVICE_ID, entry.entry_id)
        return False
    device_ids = [did]

    items: list[dict] = []
    for device_id in device_ids:
        dev = SmartThingsDevice(api, device_id, expose_all=entry.data.get(CONF_EXPOSE_ALL, True))
        try:
            await dev.async_init()
        except Exception as err:
            raise ConfigEntryNotReady(
                f"Device {device_id} not reachable during setup"
            ) from err
        coordinator = SmartThingsCoordinator(hass, dev, scan_interval=entry.data.get(CONF_SCAN_INTERVAL, 15))
        await coordinator.async_config_entry_first_refresh()
        items.append({"device": dev, "coordinator": coordinator})

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "items": items,
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
