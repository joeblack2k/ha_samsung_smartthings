from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from aiohttp import ClientResponseError

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_FRAME_LOCAL, ENTRY_TYPE_SOUNDBAR_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .frame_local_api import AsyncFrameLocal, FrameLocalError, FrameLocalUnsupportedError
from .soundbar_local_api import AsyncSoundbarLocal


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_FRAME_LOCAL or domain.get("type") == ENTRY_TYPE_FRAME_LOCAL:
        coordinator = domain["coordinator"]
        frame: AsyncFrameLocal = domain["frame"]
        host = domain.get("host") or "frame"
        async_add_entities(
            [
                FrameLocalArtModeSwitch(coordinator, frame, host),
                FrameLocalBrightnessSensorSwitch(coordinator, frame, host),
            ]
        )
        return
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SOUNDBAR_LOCAL or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL:
        coordinator = domain["coordinator"]
        soundbar: AsyncSoundbarLocal = domain["soundbar"]
        host = domain.get("host") or "soundbar"
        async_add_entities(
            [
                SoundbarLocalPowerSwitch(coordinator, soundbar, host),
                SoundbarLocalMuteSwitch(coordinator, soundbar, host),
                SoundbarLocalNightModeSwitch(coordinator, soundbar, host),
            ]
        )
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"soundbar_local_{host}")},
            manufacturer="Samsung",
            model="Soundbar (Local)",
            name=f"Soundbar {host}",
        )

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


class SoundbarLocalNightModeSwitch(_SoundbarLocalSwitch):
    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        super().__init__(coordinator, soundbar, host, "switch_night_mode", "Night Mode")
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:weather-night"

    @property
    def is_on(self) -> bool | None:
        v = self._coordinator.data.get("night_mode")
        if isinstance(v, bool):
            return v
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self._soundbar.set_night_mode(True)
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._soundbar.set_night_mode(False)
        await self._coordinator.async_request_refresh()


class _FrameLocalSwitch(SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str, key: str, name: str) -> None:
        self._coordinator = coordinator
        self._frame = frame
        self._host = host
        self._attr_unique_id = f"frame_local_{host}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"frame_local_{host}")},
            manufacturer="Samsung",
            model="The Frame (Local)",
            name=f"Frame TV {host}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._coordinator.data.get("online", False))


class FrameLocalArtModeSwitch(_FrameLocalSwitch):
    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "art_mode", "Art Mode")
        self._attr_icon = "mdi:image-frame"

    @property
    def is_on(self) -> bool | None:
        v = self._coordinator.data.get("art_mode")
        if isinstance(v, str):
            return v.lower() == "on"
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        supported = self._coordinator.data.get("supports_art_mode")
        return supported is not False

    async def async_turn_on(self, **kwargs) -> None:
        try:
            await self._frame.set_art_mode(True)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Art mode control is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to turn art mode on: {err}") from err
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self._frame.set_art_mode(False)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Art mode control is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to turn art mode off: {err}") from err
        await self._coordinator.async_request_refresh()


class FrameLocalBrightnessSensorSwitch(_FrameLocalSwitch):
    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "brightness_sensor", "Brightness Sensor")
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:brightness-6"

    @property
    def is_on(self) -> bool | None:
        payload = self._coordinator.data.get("current_artwork_payload")
        if isinstance(payload, dict):
            raw = payload.get("brightness_sensor_setting")
            if isinstance(raw, str):
                if raw.lower() == "on":
                    return True
                if raw.lower() == "off":
                    return False
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        supported = self._coordinator.data.get("supports_brightness_sensor")
        return supported is not False

    async def async_turn_on(self, **kwargs) -> None:
        try:
            await self._frame.set_brightness_sensor(True)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Brightness sensor toggle is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to enable brightness sensor: {err}") from err
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self._frame.set_brightness_sensor(False)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Brightness sensor toggle is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to disable brightness sensor: {err}") from err
        await self._coordinator.async_request_refresh()
