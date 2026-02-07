from __future__ import annotations

import hashlib
import logging
from typing import Any

from aiohttp import ClientResponseError
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
import voluptuous as vol

from .const import (
    CONF_DISCOVERY_INTERVAL,
    CONF_ENTRY_TYPE,
    CONF_EXPOSE_ALL,
    CONF_HOST as CONF_HOST_LOCAL,
    CONF_INCLUDE_NON_SAMSUNG,
    CONF_MANAGE_DIAGNOSTICS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_EXPOSE_ALL,
    DEFAULT_INCLUDE_NON_SAMSUNG,
    DEFAULT_MANAGE_DIAGNOSTICS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_CLOUD,
    ENTRY_TYPE_SOUNDBAR_LOCAL,
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

    VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Pick setup type (Cloud or Local soundbar)."""
        if user_input is not None:
            t = user_input.get(CONF_ENTRY_TYPE)
            if t == ENTRY_TYPE_SOUNDBAR_LOCAL:
                return await self.async_step_soundbar_local()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENTRY_TYPE, default=ENTRY_TYPE_CLOUD): vol.In(
                        {
                            ENTRY_TYPE_CLOUD: "SmartThings Cloud (token)",
                            ENTRY_TYPE_SOUNDBAR_LOCAL: "Soundbar Local (LAN)",
                        }
                    )
                }
            ),
        )

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN, "") or "").strip()
            if not token:
                errors["base"] = "invalid_auth"
            else:
                try:
                    await _validate_token(self.hass, token)
                    for e in self._async_current_entries():
                        if e.data.get(CONF_TOKEN) == token:
                            return self.async_abort(reason="already_configured")

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
                            CONF_ENTRY_TYPE: ENTRY_TYPE_CLOUD,
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
            step_id="cloud",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    async def async_step_soundbar_local(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_HOST, "") or "").strip()
            if not host:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"soundbar_local_{host}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Soundbar {host}",
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_SOUNDBAR_LOCAL,
                        CONF_HOST_LOCAL: host,
                        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, False)),
                    },
                )

        return self.async_show_form(
            step_id="soundbar_local",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_VERIFY_SSL, default=False): bool,
                }
            ),
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
