from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTRY_TYPE, DOMAIN, SpeakerIdentifier, ENTRY_TYPE_FRAME_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .frame_local_api import AsyncFrameLocal, FrameLocalError, FrameLocalUnsupportedError


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
                FrameLocalBrightnessNumber(coordinator, frame, host),
                FrameLocalSlideshowMinutesNumber(coordinator, frame, host),
                FrameLocalMotionSensitivityNumber(coordinator, frame, host),
            ]
        )
        return
    entities: list[NumberEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device

        # Soundbar volume (absolute). This is redundant with media_player, but useful for
        # users who want an explicit slider control.
        if dev.is_soundbar and dev.has_capability("audioVolume"):
            entities.append(SamsungSmartThingsVolumeNumber(coordinator))

        # Soundbar: samsungvd.soundFrom mode (integer)
        if dev.has_capability("samsungvd.soundFrom") and dev.runtime and dev.runtime.expose_all:
            entities.append(SamsungSmartThingsSoundFromModeNumber(coordinator))

        # Soundbar execute-based numbers
        if dev.is_soundbar and dev.has_capability("execute"):
            entities.append(SoundbarWooferLevelNumber(coordinator))
            for spk in SpeakerIdentifier:
                entities.append(SoundbarSpeakerLevelNumber(coordinator, spk))

    async_add_entities(entities)


class SamsungSmartThingsVolumeNumber(SamsungSmartThingsEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = "slider"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_number_volume"
        self._attr_name = "Volume"

    @property
    def native_value(self) -> float | None:
        v = self.device.get_attr("audioVolume", "volume")
        try:
            return float(v)
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.device.send_command("audioVolume", "setVolume", arguments=[int(value)])
        await self.coordinator.async_request_refresh()


class SamsungSmartThingsSoundFromModeNumber(SamsungSmartThingsEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_mode = "slider"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_number_sound_from_mode"
        self._attr_name = "Sound From Mode"

    @property
    def native_value(self) -> float | None:
        v = self.device.get_attr("samsungvd.soundFrom", "mode")
        try:
            return float(v)
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.device.send_command("samsungvd.soundFrom", "setSoundFrom", arguments=[int(value)])
        await self.coordinator.async_request_refresh()


# ---- Soundbar execute-based numbers ----


class SoundbarWooferLevelNumber(SamsungSmartThingsEntity, NumberEntity):
    """Woofer level for soundbars (execute-based)."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_native_min_value = -12
    _attr_native_max_value = 6
    _attr_native_step = 1
    _attr_mode = "slider"
    _attr_icon = "mdi:speaker"

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_number_sb_woofer"
        self._attr_name = "Woofer Level"

    @property
    def available(self) -> bool:
        return super().available and self.device._sb_execute_supported is True

    @property
    def native_value(self) -> float | None:
        v = self.device._sb_woofer_level
        if v is None:
            return None
        return float(v)

    async def async_set_native_value(self, value: float) -> None:
        await self.device.set_woofer_level(int(value))
        await self.coordinator.async_request_refresh()


_SPEAKER_NAMES = {
    SpeakerIdentifier.CENTER: "Center",
    SpeakerIdentifier.SIDE: "Side",
    SpeakerIdentifier.WIDE: "Wide",
    SpeakerIdentifier.FRONT_TOP: "Front Top",
    SpeakerIdentifier.REAR: "Rear",
    SpeakerIdentifier.REAR_TOP: "Rear Top",
}


class SoundbarSpeakerLevelNumber(SamsungSmartThingsEntity, NumberEntity):
    """Per-speaker channel volume for soundbars (execute-based, write-only)."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_native_min_value = -12
    _attr_native_max_value = 6
    _attr_native_step = 1
    _attr_mode = "slider"
    _attr_icon = "mdi:speaker"

    def __init__(self, coordinator: SmartThingsCoordinator, speaker: SpeakerIdentifier) -> None:
        super().__init__(coordinator)
        self._speaker = speaker
        label = _SPEAKER_NAMES.get(speaker, speaker.name)
        self._attr_unique_id = f"{self.device.device_id}_number_sb_spk_{speaker.name.lower()}"
        self._attr_name = f"Speaker {label}"

    @property
    def available(self) -> bool:
        return super().available and self.device._sb_execute_supported is True

    @property
    def native_value(self) -> float | None:
        # Channel volumes have no read-back via execute status.
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self.device.set_speaker_level(self._speaker, int(value))
        await self.coordinator.async_request_refresh()


class _FrameLocalNumber(NumberEntity):
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


class FrameLocalBrightnessNumber(_FrameLocalNumber):
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_mode = "slider"

    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "brightness", "Art Brightness")

    @property
    def native_value(self) -> float | None:
        value = self._coordinator.data.get("brightness")
        try:
            return float(value)
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._frame.set_brightness(value)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Brightness control is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set art brightness: {err}") from err
        await self._coordinator.async_request_refresh()


class FrameLocalSlideshowMinutesNumber(_FrameLocalNumber):
    _attr_native_min_value = 0
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_mode = "box"

    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "slideshow_minutes", "Slideshow Minutes")
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        status = self._coordinator.data.get("slideshow")
        if isinstance(status, dict):
            value = status.get("value")
            if isinstance(value, str) and value.isdigit():
                return float(value)
            if value in ("off", "OFF"):
                return 0.0
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        supported = self._coordinator.data.get("supports_slideshow")
        return supported is not False

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._frame.set_slideshow_status(int(value), True, None)
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Slideshow control is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set slideshow minutes: {err}") from err
        await self._coordinator.async_request_refresh()


class FrameLocalMotionSensitivityNumber(_FrameLocalNumber):
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_mode = "slider"

    def __init__(self, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        super().__init__(coordinator, frame, host, "motion_sensitivity", "Motion Sensitivity")
        self._attr_entity_registry_enabled_default = False
        self._attr_entity_registry_visible_default = False
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        payload = self._coordinator.data.get("current_artwork_payload")
        if isinstance(payload, dict):
            value = payload.get("motion_sensitivity")
            if isinstance(value, str) and value.isdigit():
                return float(value)
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        supported = self._coordinator.data.get("supports_motion_sensitivity")
        return supported is not False

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._frame.set_motion_sensitivity(str(int(value)))
        except FrameLocalUnsupportedError as err:
            raise HomeAssistantError("Motion sensitivity is not supported on this Frame TV.") from err
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set motion sensitivity: {err}") from err
        await self._coordinator.async_request_refresh()
