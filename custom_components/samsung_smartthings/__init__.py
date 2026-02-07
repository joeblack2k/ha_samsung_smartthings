from __future__ import annotations

import asyncio
import hashlib
import logging

from aiohttp import ClientResponseError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_DISCOVERY_INTERVAL,
    CONF_EXPOSE_ALL,
    CONF_INCLUDE_NON_SAMSUNG,
    CONF_MANAGE_DIAGNOSTICS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_EXPOSE_ALL,
    DEFAULT_INCLUDE_NON_SAMSUNG,
    DEFAULT_MANAGE_DIAGNOSTICS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SmartThingsCoordinator
from .device import SmartThingsDevice
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _is_samsung(d: dict) -> bool:
    try:
        return str(d.get("manufacturerName") or "").lower().startswith("samsung")
    except Exception:
        return False


async def _get_hub_id(api: SmartThingsApi, token: str) -> str:
    """Return a stable hub id for device registry nesting."""
    # SmartThings has strict rate limits; avoid extra calls during setup.
    # Token-hash is stable for this config entry and avoids leaking secrets.
    return f"token_{_token_key(token)}"


async def _ensure_discovery_task(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start a background discovery loop for this hub entry."""
    if entry.pref_disable_new_entities:
        return

    token = entry.data.get(CONF_TOKEN)
    if not isinstance(token, str) or not token:
        return

    api = SmartThingsApi(hass, token)
    hub_id = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("hub_id")
    if not isinstance(hub_id, str) or not hub_id:
        hub_id = await _get_hub_id(api, token)

    opts = entry.options or {}
    discovery_interval = int(opts.get(CONF_DISCOVERY_INTERVAL, entry.data.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL)))
    include_non_samsung = bool(opts.get(CONF_INCLUDE_NON_SAMSUNG, entry.data.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG)))

    async def _loop() -> None:
        await asyncio.sleep(10)
        while True:
            try:
                devices = await api.list_devices()
                if not include_non_samsung:
                    devices = [d for d in devices if isinstance(d, dict) and _is_samsung(d)]

                current: set[str] = set()
                dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                for it in dom.get("items") or []:
                    dev = it.get("device")
                    if dev and getattr(dev, "device_id", None):
                        current.add(dev.device_id)

                latest: set[str] = set()
                for d in devices:
                    did = d.get("deviceId")
                    if isinstance(did, str) and did:
                        latest.add(did)

                if latest - current:
                    _LOGGER.info("[%s] New devices discovered for %s; reloading entry", DOMAIN, hub_id)
                    await hass.config_entries.async_reload(entry.entry_id)
            except Exception:
                # Token may be revoked; avoid log spam.
                _LOGGER.debug("[%s] discovery scan failed for entry %s", DOMAIN, entry.entry_id, exc_info=True)

            await asyncio.sleep(max(60, discovery_interval))

    # IMPORTANT: don't block HA startup. Use a background task API if available.
    if hasattr(hass, "async_create_background_task"):
        task = hass.async_create_background_task(_loop(), name=f"{DOMAIN}_discovery_{entry.entry_id}")
    else:
        task = asyncio.create_task(_loop())

    def _cancel(_event) -> None:
        task.cancel()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _cancel)
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})["_discovery_task"] = task


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Services remain: they are useful for advanced cases and debugging.
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
        # SmartThings command signature is (appId?, appName?).
        # If only app_name is provided, we must send [None, app_name] to avoid
        # SmartThings interpreting the single argument as appId.
        args = None
        if app_id and app_name:
            args = [str(app_id), str(app_name)]
        elif app_id:
            args = [str(app_id)]
        elif app_name:
            args = [None, str(app_name)]
        await dev.send_command("custom.launchapp", "launchApp", arguments=args if args else None)
        await coordinator.async_request_refresh()

    async def _set_art_mode(call) -> None:
        dev, coordinator = await _resolve_device(call)
        on = call.data.get("on", True)
        # only 'on' is currently supported; off is best-effort.
        if on in (True, "true", "on", 1, "1"):
            await dev.set_art_mode()
        else:
            await dev.exit_art_mode()
        await coordinator.async_request_refresh()

    async def _set_ambient_content(call) -> None:
        dev, coordinator = await _resolve_device(call)
        data_json = str(call.data["data_json"])
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
    if not hass.services.has_service(DOMAIN, "set_art_mode"):
        hass.services.async_register(DOMAIN, "set_art_mode", _set_art_mode)
    if not hass.services.has_service(DOMAIN, "set_ambient_content"):
        hass.services.async_register(DOMAIN, "set_ambient_content", _set_ambient_content)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    token = entry.data.get(CONF_TOKEN)
    if not isinstance(token, str) or not token:
        _LOGGER.error("[%s] Missing %s in config entry %s", DOMAIN, CONF_TOKEN, entry.entry_id)
        return False

    # Back-compat migration: old entries stored device_id/device_name/scan_interval/etc in data.
    # We keep behavior as "hub per token" regardless; device_id is ignored.
    if set(entry.data.keys()) != {CONF_TOKEN}:
        new_opts = dict(entry.options or {})
        # Preserve legacy settings when present.
        if CONF_EXPOSE_ALL not in new_opts and CONF_EXPOSE_ALL in entry.data:
            new_opts[CONF_EXPOSE_ALL] = bool(entry.data.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
        if CONF_SCAN_INTERVAL not in new_opts and CONF_SCAN_INTERVAL in entry.data:
            try:
                new_opts[CONF_SCAN_INTERVAL] = int(entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
            except Exception:
                pass
        if CONF_DISCOVERY_INTERVAL not in new_opts and CONF_DISCOVERY_INTERVAL in entry.data:
            try:
                new_opts[CONF_DISCOVERY_INTERVAL] = int(entry.data.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL))
            except Exception:
                pass
        if CONF_INCLUDE_NON_SAMSUNG not in new_opts and CONF_INCLUDE_NON_SAMSUNG in entry.data:
            new_opts[CONF_INCLUDE_NON_SAMSUNG] = bool(entry.data.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG))

        hass.config_entries.async_update_entry(entry, data={CONF_TOKEN: token}, options=new_opts)

    opts = entry.options or {}
    expose_all = bool(opts.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
    raw_scan_interval = int(opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    # SmartThings cloud rate-limits aggressively; keep a safe floor.
    scan_interval = max(30, raw_scan_interval)
    if scan_interval != raw_scan_interval:
        new_opts = dict(opts)
        new_opts[CONF_SCAN_INTERVAL] = scan_interval
        hass.config_entries.async_update_entry(entry, options=new_opts)
    include_non_samsung = bool(opts.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG))
    manage_diagnostics = bool(opts.get(CONF_MANAGE_DIAGNOSTICS, DEFAULT_MANAGE_DIAGNOSTICS))

    api = SmartThingsApi(hass, token)
    hub_id = await _get_hub_id(api, token)

    # Create a hub device to nest all SmartThings devices under it.
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_id)},
        name=f"Samsung SmartThings (Cloud) ({hub_id.split('_', 1)[-1][:8]})",
        manufacturer="Samsung",
        model="SmartThings Cloud",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    try:
        devices = await api.list_devices()
    except ClientResponseError as exc:
        if exc.status == 401:
            raise ConfigEntryAuthFailed("Invalid SmartThings token") from exc
        raise ConfigEntryNotReady(f"SmartThings API error {exc.status}") from exc
    except Exception as exc:
        raise ConfigEntryNotReady("SmartThings API not reachable") from exc
    if not include_non_samsung:
        devices = [d for d in devices if isinstance(d, dict) and _is_samsung(d)]

    # Keep deterministic order (name then deviceId), so entity_id churn is minimized.
    def _sort_key(d: dict) -> tuple[str, str]:
        label = d.get("label") or d.get("name") or ""
        did = d.get("deviceId") or ""
        return (str(label).lower(), str(did))

    devices = [d for d in devices if isinstance(d, dict) and isinstance(d.get("deviceId"), str)]
    devices.sort(key=_sort_key)

    items: list[dict] = []
    for d in devices:
        did = d.get("deviceId")
        if not isinstance(did, str) or not did:
            continue
        # Use the already-fetched device payload to avoid extra per-device API calls.
        dev = SmartThingsDevice(api, did, expose_all=expose_all, device=d)
        await dev.async_init()

        coordinator = SmartThingsCoordinator(hass, dev, hub_id=hub_id, scan_interval=scan_interval)
        # Don't block setup on initial refresh (SmartThings rate-limits hard).
        if hasattr(hass, "async_create_background_task"):
            hass.async_create_background_task(
                coordinator.async_config_entry_first_refresh(),
                name=f"{DOMAIN}_first_refresh_{did}",
            )
        else:
            hass.async_create_task(coordinator.async_config_entry_first_refresh())
        items.append({"device": dev, "coordinator": coordinator})

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"api": api, "hub_id": hub_id, "items": items}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Auto-discovery: reload entry if new devices appear.
    # Defer until HA is started so this doesn't get treated as a startup task.
    async def _start_discovery(_ev) -> None:
        await _ensure_discovery_task(hass, entry)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_discovery)

    # One-shot cleanup to reduce clutter: hide/disable diagnostic entities by default.
    if manage_diagnostics:
        hass.async_create_task(_hide_disable_diagnostics(hass, entry))

    # Reload on options changes.
    entry.async_on_unload(entry.add_update_listener(_update_listener))

    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    task = dom.get("_discovery_task")
    if task:
        try:
            task.cancel()
        except Exception:
            pass

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return True


async def _hide_disable_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hide/disable known-noisy entities for this entry (diagnostics + expose_all noise)."""
    # Only run once per HA runtime for this entry. Otherwise a config-entry reload can
    # re-disable entities the user explicitly enabled.
    dom = hass.data.setdefault(DOMAIN, {})
    done = dom.setdefault("_diagnostic_cleanup_done", set())
    if entry.entry_id in done:
        return

    # Allow platforms to create entities first.
    await asyncio.sleep(30)
    try:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(reg, entry.entry_id)
        _LOGGER.info("[%s] Diagnostics cleanup: %s entities for entry %s", DOMAIN, len(entries), entry.entry_id)

        # Heuristic based on unique_id patterns created by this integration.
        # We keep core controls visible (power switch, media player, remote, primary selects).
        noisy_tokens = (
            "_attr_",  # generic attribute sensors
            "_cmd_",  # generic no-arg command buttons
            "_switch_sb_",  # execute-based soundbar toggles
            "_select_sb_",  # execute-based soundbar selects
            "_number_",  # diagnostic numbers (raw/advanced)
        )

        updated = 0
        for e in entries:
            if e.platform != DOMAIN:
                continue
            uid = e.unique_id or ""
            # Keep the soundbar volume slider enabled/visible by default.
            if uid.endswith("_number_volume"):
                continue
            if not any(t in uid for t in noisy_tokens):
                continue
            # Don't override explicit user choices.
            if e.disabled_by in (er.RegistryEntryDisabler.USER,):
                continue
            if e.hidden_by in (er.RegistryEntryHider.USER,):
                continue
            # Apply only when still visible/enabled.
            updates = {}
            if e.hidden_by is None:
                updates["hidden_by"] = er.RegistryEntryHider.INTEGRATION
            if e.disabled_by is None:
                updates["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
            if updates:
                reg.async_update_entity(e.entity_id, **updates)
                updated += 1

        # Remove legacy "generic command" entities (they were noisy and often 4xx).
        # These used unique_ids containing "_cmd_" in earlier versions of this integration.
        for e in list(entries):
            if e.platform != DOMAIN:
                continue
            uid = e.unique_id or ""
            if "_cmd_" not in uid:
                continue
            # Don't override explicit user choices.
            if e.disabled_by in (er.RegistryEntryDisabler.USER,):
                continue
            if e.hidden_by in (er.RegistryEntryHider.USER,):
                continue
            try:
                reg.async_remove(e.entity_id)
                updated += 1
            except Exception:
                pass

        # Frame TVs often expose Ambient/Art capabilities but SmartThings may not actually
        # support the command for a given device/account. We removed the old ambient buttons
        # and replaced them with a best-effort Art Mode button. Remove the legacy entities
        # from the registry so they don't linger forever.
        dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        for it in dom.get("items") or []:
            dev = it.get("device")
            if not dev:
                continue
            for suffix in ("ambient_on", "ambient18_on"):
                uid = f"{dev.device_id}_{suffix}"
                for e in entries:
                    if e.platform != DOMAIN:
                        continue
                    if (e.unique_id or "") != uid:
                        continue
                    # Don't override explicit user choices, but removing legacy entities is safe.
                    if e.disabled_by in (er.RegistryEntryDisabler.USER,):
                        continue
                    if e.hidden_by in (er.RegistryEntryHider.USER,):
                        continue
                    try:
                        reg.async_remove(e.entity_id)
                        updated += 1
                    except Exception:
                        # Fallback: at least hide+disable.
                        updates = {}
                        if e.hidden_by is None:
                            updates["hidden_by"] = er.RegistryEntryHider.INTEGRATION
                        if e.disabled_by is None:
                            updates["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
                        if updates:
                            reg.async_update_entity(e.entity_id, **updates)
                            updated += 1
        _LOGGER.info("[%s] Diagnostics cleanup complete: updated=%s", DOMAIN, updated)
        done.add(entry.entry_id)
    except Exception:
        _LOGGER.warning("[%s] diagnostics cleanup failed for entry %s", DOMAIN, entry.entry_id, exc_info=True)
