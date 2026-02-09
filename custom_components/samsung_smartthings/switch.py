from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from aiohttp import ClientResponseError

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_SOUNDBAR_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .soundbar_local_api import AsyncSoundbarLocal


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SOUNDBAR_LOCAL or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL:
        coordinator = domain["coordinator"]
        soundbar: AsyncSoundbarLocal = domain["soundbar"]
        host = domain.get("host") or "soundbar"
        async_add_entities([SoundbarLocalPowerSwitch(coordinator, soundbar, host), SoundbarLocalMuteSwitch(coordinator, soundbar, host)])
        return

    entities: list[SwitchEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device
        if dev.has_capability("switch"):
            entities.append(SamsungSmartThingsPowerSwitch(coordinator))

        # Soundbar execute-based switches
        if dev.is_soundbar and dev.has_capability("execute"):
            entities.append(SoundbarNightModeSwitch(coordinator))
            entities.append(SoundbarBassModeSwitch(coordinator))
            entities.append(SoundbarVoiceAmplifierSwitch(coordinator))
            entities.append(SoundbarAVASwitch(coordinator))
            entities.append(SoundbarSpaceFitSoundSwitch(coordinator))

    async_add_entities(entities)


class SamsungSmartThingsPowerSwitch(SamsungSmartThingsEntity, SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch"
        self._attr_name = "Power"

    @property
    def is_on(self) -> bool | None:
        v = self.device.get_attr("switch", "switch")
        if v in ("on", True):
            return True
        if v in ("off", False):
            return False
        return None

    async def async_turn_on(self, **kwargs) -> None:
        try:
            await self.device.send_command("switch", "on", arguments=None)
        except ClientResponseError:
            # Some TVs cannot be powered on via the SmartThings cloud switch command.
            # Best-effort fallback: send a POWER key via remoteControl (toggle).
            if self.device.has_capability("samsungvd.remoteControl"):
                await self.device.send_command(
                    "samsungvd.remoteControl",
                    "send",
                    arguments=["POWER", "PRESS_AND_RELEASED"],
                )
            else:
                raise
        finally:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self.device.send_command("switch", "off", arguments=None)
        except ClientResponseError:
            if self.device.has_capability("samsungvd.remoteControl"):
                await self.device.send_command(
                    "samsungvd.remoteControl",
                    "send",
                    arguments=["POWER", "PRESS_AND_RELEASED"],
                )
            else:
                raise
        finally:
            await self.coordinator.async_request_refresh()


# ---- Soundbar execute-based switches ----


class _SoundbarExecuteSwitch(SamsungSmartThingsEntity, SwitchEntity):
    """Base class for soundbar execute-based on/off toggles."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False

    _state_attr: str  # override in subclass
    _set_method: str  # method name on device

    @property
    def available(self) -> bool:
        # Execute read-back is often missing in SmartThings cloud even when the command
        # itself works. Only hide the entity when we've explicitly detected execute is
        # unsupported (e.g. non-429 error).
        return super().available and self.device._sb_execute_supported is not False

    @property
    def is_on(self) -> bool | None:
        v = getattr(self.device, self._state_attr, None)
        if v is None:
            return None
        return v == 1

    async def async_turn_on(self, **kwargs) -> None:
        await getattr(self.device, self._set_method)(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await getattr(self.device, self._set_method)(False)
        await self.coordinator.async_request_refresh()


class SoundbarNightModeSwitch(_SoundbarExecuteSwitch):
    _state_attr = "_sb_night_mode"
    _set_method = "set_night_mode"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch_sb_night_mode"
        self._attr_name = "Night Mode"
        self._attr_icon = "mdi:weather-night"


class SoundbarBassModeSwitch(_SoundbarExecuteSwitch):
    _state_attr = "_sb_bass_mode"
    _set_method = "set_bass_mode"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch_sb_bass_mode"
        self._attr_name = "Bass Boost"
        self._attr_icon = "mdi:speaker"


class SoundbarVoiceAmplifierSwitch(_SoundbarExecuteSwitch):
    _state_attr = "_sb_voice_amplifier"
    _set_method = "set_voice_amplifier"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch_sb_voice_amplifier"
        self._attr_name = "Voice Amplifier"
        self._attr_icon = "mdi:account-voice"


class SoundbarAVASwitch(_SoundbarExecuteSwitch):
    _set_method = "set_active_voice_amplifier"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch_sb_ava"
        self._attr_name = "Active Voice Amplifier"
        self._attr_icon = "mdi:account-voice"

    @property
    def is_on(self) -> bool | None:
        # AVA has no read-back from execute status; return None.
        return None


class SoundbarSpaceFitSoundSwitch(_SoundbarExecuteSwitch):
    _set_method = "set_space_fit_sound"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_switch_sb_space_fit"
        self._attr_name = "SpaceFit Sound"
        self._attr_icon = "mdi:surround-sound"

    @property
    def is_on(self) -> bool | None:
        # No read-back from execute status; return None.
        return None


class _SoundbarLocalSwitch(SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str, key: str, name: str) -> None:
        self._coordinator = coordinator
        self._soundbar = soundbar
        self._host = host
        self._attr_unique_id = f"soundbar_local_{host}_{key}"
        self._attr_name = name

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success


class SoundbarLocalPowerSwitch(_SoundbarLocalSwitch):
    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "switch_power", "Power")

    @property
    def is_on(self) -> bool | None:
        return self._coordinator.data.get("power") == "powerOn"

    async def async_turn_on(self, **kwargs) -> None:
        await self._soundbar.power_on()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._soundbar.power_off()
        await self._coordinator.async_request_refresh()


class SoundbarLocalMuteSwitch(_SoundbarLocalSwitch):
    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "switch_mute", "Mute")

    @property
    def is_on(self) -> bool | None:
        v = self._coordinator.data.get("mute")
        if isinstance(v, bool):
            return v
        return None

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on is not True:
            await self._soundbar.mute_toggle()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is not False:
            await self._soundbar.mute_toggle()
        await self._coordinator.async_request_refresh()
