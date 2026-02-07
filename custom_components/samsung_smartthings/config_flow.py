from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientResponseError
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ADD_ALL,
    CONF_EXPOSE_ALL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_EXPOSE_ALL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)


async def _validate_token(hass, token: str) -> list[dict[str, Any]]:
    api = SmartThingsApi(hass, token)
    devices = await api.list_devices()
    # Filter to Samsung by default, but keep all in case user needs it.
    return [d for d in devices if isinstance(d, dict)]

def _is_samsung(d: dict[str, Any]) -> bool:
    return (d.get("manufacturerName") or "").lower().startswith("samsung")

def _device_name(d: dict[str, Any]) -> str:
    label = d.get("label") or d.get("name")
    if isinstance(label, str) and label.strip():
        return label.strip()
    did = d.get("deviceId")
    return did if isinstance(did, str) else "SmartThings Device"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            self._token = user_input.get(CONF_TOKEN, "").strip()
            self._expose_all = bool(user_input.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
            self._scan_interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
            self._add_all = bool(user_input.get(CONF_ADD_ALL, False))
            try:
                self._devices = await _validate_token(self.hass, self._token)
                if self._add_all:
                    # Add all Samsung devices as separate config entries.
                    devices: list[dict[str, Any]] = [
                        d for d in self._devices if isinstance(d, dict) and _is_samsung(d)
                    ]

                    # Filter out already-configured device_ids.
                    existing: set[str] = set()
                    for e in self._async_current_entries():
                        did = e.data.get(CONF_DEVICE_ID)
                        if isinstance(did, str):
                            existing.add(did)

                    todo: list[tuple[str, str]] = []
                    for d in devices:
                        did = d.get("deviceId")
                        if not isinstance(did, str) or not did or did in existing:
                            continue
                        todo.append((did, _device_name(d)))

                    if not todo:
                        return self.async_abort(reason="already_configured")

                    # Keep deterministic order: by device name then device_id.
                    todo.sort(key=lambda t: (t[1].lower(), t[0]))

                    first_id, first_name = todo[0]

                    # Spawn import flows for the remaining devices.
                    for did, name in todo[1:]:
                        self.hass.async_create_task(
                            self.hass.config_entries.flow.async_init(
                                DOMAIN,
                                context={"source": config_entries.SOURCE_IMPORT},
                                data={
                                    CONF_TOKEN: self._token,
                                    CONF_DEVICE_ID: did,
                                    CONF_DEVICE_NAME: name or did,
                                    CONF_EXPOSE_ALL: self._expose_all,
                                    CONF_SCAN_INTERVAL: self._scan_interval,
                                },
                            )
                        )

                    await self.async_set_unique_id(first_id)
                    self._abort_if_unique_id_configured()

                    data = {
                        CONF_TOKEN: self._token,
                        CONF_DEVICE_ID: first_id,
                        CONF_DEVICE_NAME: first_name or first_id,
                        CONF_EXPOSE_ALL: self._expose_all,
                        CONF_SCAN_INTERVAL: self._scan_interval,
                    }
                    return self.async_create_entry(title=data[CONF_DEVICE_NAME], data=data)

                return await self.async_step_device()
            except ClientResponseError as exc:
                if exc.status == 401:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Config flow token validation failed")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): str,
                    vol.Required(CONF_EXPOSE_ALL, default=DEFAULT_EXPOSE_ALL): bool,
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(int, vol.Range(min=5, max=300)),
                    vol.Required(CONF_ADD_ALL, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        devices = getattr(self, "_devices", [])

        # Prefer Samsung devices in the picker to reduce clutter.
        samsung = [d for d in devices if (d.get("manufacturerName") or "").lower().startswith("samsung")]
        others = [d for d in devices if d not in samsung]
        ordered = samsung + others

        # Use a selector so the UI shows human labels while storing the deviceId.
        options: list[dict[str, str]] = []
        for d in ordered:
            did = d.get("deviceId")
            if not isinstance(did, str) or not did:
                continue
            label = _device_name(d)
            dtype = d.get("deviceTypeName") or ""
            opt = f"{label} [{dtype}] ({did[:8]})"
            options.append({"label": opt, "value": did})

        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE_ID)
            if isinstance(device_id, str) and device_id:
                # Prevent duplicates for same device_id.
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()

                # Fill device name from list.
                name = None
                for d in ordered:
                    if d.get("deviceId") == device_id:
                        name = d.get("label") or d.get("name")
                        break
                data = {
                    CONF_TOKEN: getattr(self, "_token", ""),
                    CONF_DEVICE_ID: device_id,
                    CONF_DEVICE_NAME: name or device_id,
                    CONF_EXPOSE_ALL: getattr(self, "_expose_all", DEFAULT_EXPOSE_ALL),
                    CONF_SCAN_INTERVAL: getattr(self, "_scan_interval", DEFAULT_SCAN_INTERVAL),
                }
                title = data[CONF_DEVICE_NAME]
                return self.async_create_entry(title=title, data=data)

            errors["base"] = "invalid_device"

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        """Create a config entry from an import request (used by add-all and migrations)."""
        token = str(user_input.get(CONF_TOKEN, "") or "").strip()
        device_id = str(user_input.get(CONF_DEVICE_ID, "") or "").strip()
        device_name = str(user_input.get(CONF_DEVICE_NAME, "") or "").strip() or device_id
        expose_all = bool(user_input.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
        scan_interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

        if not token or not device_id:
            return self.async_abort(reason="invalid_import")

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=device_name,
            data={
                CONF_TOKEN: token,
                CONF_DEVICE_ID: device_id,
                CONF_DEVICE_NAME: device_name,
                CONF_EXPOSE_ALL: expose_all,
                CONF_SCAN_INTERVAL: scan_interval,
            },
        )
