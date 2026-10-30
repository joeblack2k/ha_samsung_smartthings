"""Home Assistant lifecycle and native entities for advanced local settings."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .soundbar_ocf_api import LocalControlError, load_credentials
from .soundbar_ocf_settings import LABELS, NUMBERS, SELECTS, SWITCHES, SettingsClient

ENTRY_TYPE = 'soundbar_ocf'
CONF_CREDENTIAL_FILE = 'credential_file'
PRIVATE_DIRECTORY = f'{DOMAIN}_ocf'
PLATFORMS = ['switch', 'select', 'number']
_LOGGER = logging.getLogger(__name__)


async def read_configuration(hass, filename):
    """Run file I/O off the event loop; never include file contents in errors."""
    return await hass.async_add_executor_job(
        load_credentials, hass.config.path('.storage', PRIVATE_DIRECTORY), filename
    )


class SoundbarOcfCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, credentials):
        super().__init__(hass, _LOGGER, config_entry=entry, name='Samsung soundbar local OCF',
                         update_interval=timedelta(seconds=60))
        self.client = SettingsClient(credentials)
        self.device_id = str(credentials.device_id)
        self.entry = entry
        self.command_lock = asyncio.Lock()

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(self.client.read)
        except Exception:
            # Do not propagate library exceptions which may contain peer details.
            raise UpdateFailed('Local soundbar settings unavailable; no cloud fallback') from None

    async def apply(self, control, value):
        async with self.command_lock:
            try:
                result = await self.hass.async_add_executor_job(self.client.apply, control, value)
            except Exception:
                # Refresh independently; an ambiguous write is never resent.
                await self.async_request_refresh()
                raise HomeAssistantError('Local setting was not confirmed; no command resent') from None
            self.async_set_updated_data(result['values'])


async def async_setup_entry(hass, entry):
    from homeassistant.exceptions import ConfigEntryError
    try:
        credentials = await read_configuration(hass, entry.data[CONF_CREDENTIAL_FILE])
    except (KeyError, LocalControlError):
        raise ConfigEntryError('Local credential file unavailable or invalid') from None
    if entry.unique_id != f'soundbar_ocf_{credentials.device_id}':
        raise ConfigEntryError('Local credential file identifies a different soundbar')
    coordinator = SoundbarOcfCoordinator(hass, entry, credentials)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        'type': ENTRY_TYPE, 'coordinator': coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


class SoundbarOcfEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, control):
        super().__init__(coordinator)
        self.control = control
        self._attr_unique_id = f'soundbar_ocf_{coordinator.device_id}_{control}'
        self._attr_name = LABELS[control]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f'soundbar_ocf_{coordinator.device_id}')},
            manufacturer='Samsung', name='Soundbar local settings',
        )

    @property
    def available(self):
        return super().available and self.control in (self.coordinator.data or {})

    @property
    def extra_state_attributes(self):
        return {
            'transport': 'local_ocf', 'device_confirmed': self.available,
            **({'changes_sound_processing_mode': True} if self.control == 'equalizer_preset' else {}),
            **({'applies_outside_standard_mode': True} if self.control in ('bass', 'treble') else {}),
            **({'also_affects_woofer': True} if self.control == 'bass' else {}),
        }


class SoundbarOcfSwitch(SoundbarOcfEntity, SwitchEntity):
    @property
    def is_on(self):
        value = (self.coordinator.data or {}).get(self.control)
        return None if value is None else value == 'on'

    async def async_turn_on(self, **kwargs):
        await self.coordinator.apply(self.control, 'on')

    async def async_turn_off(self, **kwargs):
        await self.coordinator.apply(self.control, 'off')


class SoundbarOcfSelect(SoundbarOcfEntity, SelectEntity):
    @property
    def options(self):
        return ['Front', 'Rear'] if self.control == 'rear_speaker_mode' else (
            self.coordinator.data or {}
        ).get('equalizer_options', [])

    @property
    def current_option(self):
        return (self.coordinator.data or {}).get(self.control)

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError('Option not advertised by this soundbar')
        await self.coordinator.apply(self.control, option)


class SoundbarOcfNumber(SoundbarOcfEntity, NumberEntity):
    _attr_native_step = 1
    _attr_native_max_value = 6
    _attr_mode = 'slider'

    def __init__(self, coordinator, control):
        super().__init__(coordinator, control)
        self._attr_native_min_value = -12 if control == 'woofer_level' else -6

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.control)

    async def async_set_native_value(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
            self._attr_native_min_value <= value <= 6 and int(value) == value
        ):
            raise HomeAssistantError('Local soundbar level must be a whole step within range')
        await self.coordinator.apply(self.control, int(value))


def entities_for(coordinator, platform):
    controls, entity_class = {
        'switch': (SWITCHES, SoundbarOcfSwitch),
        'select': (SELECTS, SoundbarOcfSelect),
        'number': (NUMBERS, SoundbarOcfNumber),
    }[platform]
    # Create only mapped settings. Missing/unsupported values stay unavailable,
    # so a later successful poll can recover without an integration reload.
    return [entity_class(coordinator, control) for control in controls]
