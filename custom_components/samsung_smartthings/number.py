from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SpeakerIdentifier
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device

        # Soundbar volume (absolute) as number is redundant with media_player, but useful for raw control.
        if dev.has_capability("audioVolume") and dev.runtime and dev.runtime.expose_all:
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
    _attr_entity_registry_enabled_default = False
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
