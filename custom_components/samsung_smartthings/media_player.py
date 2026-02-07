from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import MediaPlayerEntityFeature as F
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity


class _FeatureMask(int):
    """Int mask that also supports `feature in mask` membership checks."""

    def __contains__(self, item: object) -> bool:
        try:
            iv = int(item)
        except Exception:
            return False
        return (int(self) & iv) == iv


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    entities: list[SamsungSmartThingsMediaPlayer] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        entities.append(SamsungSmartThingsMediaPlayer(coordinator))
    async_add_entities(entities)


class SamsungSmartThingsMediaPlayer(SamsungSmartThingsEntity, MediaPlayerEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_media_player"
        self._attr_name = "Media"
        # TVs and soundbars are both exposed as media_player; pick the right device class.
        if (
            self.device.has_capability("tvChannel")
            or self.device.has_capability("samsungvd.mediaInputSource")
            or self.device.has_capability("samsungvd.remoteControl")
        ):
            self._attr_device_class = MediaPlayerDeviceClass.TV
        else:
            self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

    @property
    def supported_features(self) -> int:
        f = 0
        if self.device.has_capability("switch"):
            f |= F.TURN_ON | F.TURN_OFF
        if self.device.has_capability("audioMute"):
            f |= F.VOLUME_MUTE
        if self.device.has_capability("audioVolume"):
            f |= F.VOLUME_SET | F.VOLUME_STEP
        if self.device.has_capability("mediaPlayback"):
            f |= F.PAUSE | F.PLAY | F.STOP
        if self.device.has_capability("mediaTrackControl"):
            f |= F.NEXT_TRACK | F.PREVIOUS_TRACK
        # Source selection is handled via select entity; keep here minimal.
        return _FeatureMask(f)

    @property
    def state(self) -> str | None:
        # switch is the closest reliable state.
        sw = self.device.get_attr("switch", "switch")
        if sw in ("off", False):
            return "off"
        if sw in ("on", True):
            # use thingStatus if present for a better hint.
            ts = self.device.get_attr("samsungvd.thingStatus", "status")
            if isinstance(ts, str) and ts.lower() in ("playing", "paused"):
                return ts.lower()
            return "on"
        return None

    @property
    def is_volume_muted(self) -> bool | None:
        v = self.device.get_attr("audioMute", "mute")
        if v in (True, "muted"):
            return True
        if v in (False, "unmuted"):
            return False
        return None

    @property
    def volume_level(self) -> float | None:
        v = self.device.get_attr("audioVolume", "volume")
        try:
            vi = float(v)
        except Exception:
            return None
        # SmartThings TVs often report 0; still normalize.
        if vi < 0:
            return 0.0
        if vi > 100:
            return 1.0
        return vi / 100.0

    async def async_turn_on(self) -> None:
        await self.device.send_command("switch", "on", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        await self.device.send_command("switch", "off", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        if mute:
            await self.device.send_command("audioMute", "mute", arguments=None)
        else:
            await self.device.send_command("audioMute", "unmute", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        # ST expects 0..100 typically.
        v = max(0, min(100, int(volume * 100)))
        await self.device.send_command("audioVolume", "setVolume", arguments=[v])
        await self.coordinator.async_request_refresh()

    async def async_volume_up(self) -> None:
        await self.device.send_command("audioVolume", "volumeUp", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_volume_down(self) -> None:
        await self.device.send_command("audioVolume", "volumeDown", arguments=None)
        await self.coordinator.async_request_refresh()

    # Playback (TV may expose but might not do anything; safe to call)
    async def async_media_play(self) -> None:
        await self.device.send_command("mediaPlayback", "play", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_media_pause(self) -> None:
        await self.device.send_command("mediaPlayback", "pause", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_media_stop(self) -> None:
        await self.device.send_command("mediaPlayback", "stop", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_media_next_track(self) -> None:
        await self.device.send_command("mediaTrackControl", "nextTrack", arguments=None)
        await self.coordinator.async_request_refresh()

    async def async_media_previous_track(self) -> None:
        await self.device.send_command("mediaTrackControl", "previousTrack", arguments=None)
        await self.coordinator.async_request_refresh()
