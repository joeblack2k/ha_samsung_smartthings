from __future__ import annotations

import hashlib
import logging
from typing import Any

from aiohttp import ClientResponseError
from homeassistant import config_entries
import voluptuous as vol

from .const import (
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
)
from .smartthings_api import SmartThingsApi

_LOGGER = logging.getLogger(__name__)


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


async def _validate_token(hass, token: str) -> dict[str, Any]:
    """Validate token by listing devices (and optionally fetching /users/me)."""
    api = SmartThingsApi(hass, token)
    devices = await api.list_devices()
    return {"devices": devices}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Hub-style config flow: one entry per SmartThings account/token."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN, "") or "").strip()
            if not token:
                errors["base"] = "invalid_auth"
            else:
                try:
                    await _validate_token(self.hass, token)
                    # Prevent duplicate entries even if older installs used a different unique_id.
                    for e in self._async_current_entries():
                        if e.data.get(CONF_TOKEN) == token:
                            return self.async_abort(reason="already_configured")

                    await self.async_set_unique_id(_token_key(token))
                    self._abort_if_unique_id_configured()

                    # We seed defaults in entry.data for maximum HA compatibility.
                    # async_setup_entry will migrate these into entry.options and keep data token-only.
                    return self.async_create_entry(
                        title="Samsung SmartThings (Cloud)",
                        data={
                            CONF_TOKEN: token,
                            CONF_EXPOSE_ALL: DEFAULT_EXPOSE_ALL,
                            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                            CONF_DISCOVERY_INTERVAL: DEFAULT_DISCOVERY_INTERVAL,
                            CONF_INCLUDE_NON_SAMSUNG: DEFAULT_INCLUDE_NON_SAMSUNG,
                            CONF_MANAGE_DIAGNOSTICS: DEFAULT_MANAGE_DIAGNOSTICS,
                        },
                    )
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
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        """Support migration/import. Accepts token-only or legacy payloads."""
        token = str(user_input.get(CONF_TOKEN, "") or "").strip()
        if not token:
            return self.async_abort(reason="invalid_import")

        try:
            await _validate_token(self.hass, token)
        except Exception:
            # Import should not create broken entries.
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(_token_key(token))
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title="Samsung SmartThings (Cloud)",
            data={
                CONF_TOKEN: token,
                CONF_EXPOSE_ALL: DEFAULT_EXPOSE_ALL,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_DISCOVERY_INTERVAL: DEFAULT_DISCOVERY_INTERVAL,
                CONF_INCLUDE_NON_SAMSUNG: DEFAULT_INCLUDE_NON_SAMSUNG,
                CONF_MANAGE_DIAGNOSTICS: DEFAULT_MANAGE_DIAGNOSTICS,
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            scan_interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
            discovery_interval = int(user_input.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL))
            expose_all = bool(user_input.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
            include_non_samsung = bool(user_input.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG))
            manage_diagnostics = bool(user_input.get(CONF_MANAGE_DIAGNOSTICS, DEFAULT_MANAGE_DIAGNOSTICS))

            # Minimal validation.
            if scan_interval < 5 or scan_interval > 300:
                errors["base"] = "invalid_scan_interval"
            elif discovery_interval < 60 or discovery_interval > 24 * 3600:
                errors["base"] = "invalid_discovery_interval"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_EXPOSE_ALL: expose_all,
                        CONF_SCAN_INTERVAL: scan_interval,
                        CONF_DISCOVERY_INTERVAL: discovery_interval,
                        CONF_INCLUDE_NON_SAMSUNG: include_non_samsung,
                        CONF_MANAGE_DIAGNOSTICS: manage_diagnostics,
                    },
                )

        opts = self.config_entry.options or {}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EXPOSE_ALL, default=opts.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL)): bool,
                    vol.Required(CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): vol.All(
                        int, vol.Range(min=5, max=300)
                    ),
                    vol.Required(
                        CONF_DISCOVERY_INTERVAL,
                        default=opts.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL),
                    ): vol.All(int, vol.Range(min=60, max=24 * 3600)),
                    vol.Required(
                        CONF_INCLUDE_NON_SAMSUNG,
                        default=opts.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG),
                    ): bool,
                    vol.Required(
                        CONF_MANAGE_DIAGNOSTICS,
                        default=opts.get(CONF_MANAGE_DIAGNOSTICS, DEFAULT_MANAGE_DIAGNOSTICS),
                    ): bool,
                }
            ),
            errors=errors,
        )
