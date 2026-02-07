from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientResponseError
import voluptuous as vol
from homeassistant import config_entries

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_EXPOSE_ALL,
    CONF_TOKEN,
    DEFAULT_EXPOSE_ALL,
    DOMAIN,
)
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)


async def _validate_token(hass, token: str) -> list[dict[str, Any]]:
    api = SmartThingsApi(hass, token)
    devices = await api.list_devices()
    # Filter to Samsung by default, but keep all in case user needs it.
    return [d for d in devices if isinstance(d, dict)]


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            self._token = user_input.get(CONF_TOKEN, "").strip()
            self._expose_all = bool(user_input.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
            try:
                self._devices = await _validate_token(self.hass, self._token)
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

        # Create a mapping label -> deviceId (include type for uniqueness).
        options: dict[str, str] = {}
        for d in ordered:
            did = d.get("deviceId")
            if not isinstance(did, str) or not did:
                continue
            label = d.get("label") or d.get("name") or did
            dtype = d.get("deviceTypeName") or ""
            opt = f"{label} [{dtype}] ({did[:8]})"
            options[opt] = did

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
                }
                title = data[CONF_DEVICE_NAME]
                return self.async_create_entry(title=title, data=data)

            errors["base"] = "invalid_device"

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): vol.In(options),
                }
            ),
            errors=errors,
        )

