from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import MediaPlayerEntityFeature as F
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_FRAME_LOCAL,
    ENTRY_TYPE_SOUNDBAR_LOCAL,
)
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .frame_local_api import AsyncFrameLocal
from .soundbar_local_api import AsyncSoundbarLocal
from .app_catalog import YOUTUBE_APP, app_options, is_http_url, is_youtube_url, resolve_app


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
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_FRAME_LOCAL or domain.get("type") == ENTRY_TYPE_FRAME_LOCAL:
        coordinator = domain["coordinator"]
        frame: AsyncFrameLocal = domain["frame"]
        host = domain.get("host") or "frame"
        async_add_entities([FrameLocalMediaPlayer(hass, coordinator, frame, host)], True)
        return

    # Local soundbar entry: single media_player.
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SOUNDBAR_LOCAL or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL:
        coordinator = domain["coordinator"]
        soundbar: AsyncSoundbarLocal = domain["soundbar"]
        host = domain.get("host") or "soundbar"
        async_add_entities([SoundbarLocalMediaPlayer(coordinator, soundbar, host)], True)
        return

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
        if self.device.has_capability("custom.launchapp"):
            f |= F.PLAY_MEDIA | F.SELECT_SOURCE
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

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        if not self.device.has_capability("custom.launchapp"):
            raise HomeAssistantError("App launch is not supported on this TV via SmartThings")
        media_id = str(media_id or "").strip()
        media_type = str(media_type or "").strip().lower()
        if not media_id:
            raise HomeAssistantError("media_id is required")

        app = None
        if media_id.startswith("app:"):
            app = resolve_app(media_id.split(":", 1)[1])
        else:
            app = resolve_app(media_id)

        if app is not None:
            await self.device.send_command("custom.launchapp", "launchApp", arguments=[app.app_id, app.name])
            await self.coordinator.async_request_refresh()
            return

        # SmartThings cloud does not provide reliable URL deep-link support.
        # For YouTube URLs we can at least open the YouTube app.
        if is_http_url(media_id):
            if is_youtube_url(media_id):
                await self.device.send_command(
                    "custom.launchapp",
                    "launchApp",
                    arguments=[YOUTUBE_APP.app_id, YOUTUBE_APP.name],
                )
                await self.coordinator.async_request_refresh()
                return
            raise HomeAssistantError(
                "SmartThings cloud cannot open arbitrary URLs directly. "
                "Use app launch or local Frame mode for URL playback."
            )

        # Accept explicit app media type if value is an app name/id not in catalog.
        if media_type in ("app", "apps"):
            await self.device.send_command("custom.launchapp", "launchApp", arguments=[media_id])
            await self.coordinator.async_request_refresh()
            return

        raise HomeAssistantError("Unsupported media item for SmartThings TV")

    @property
    def source_list(self) -> list[str]:
        if not self.device.has_capability("custom.launchapp"):
            return []
        return app_options()

    async def async_select_source(self, source: str) -> None:
        app = resolve_app(source)
        if app is None:
            raise HomeAssistantError(f"Unknown app source: {source}")
        await self.device.send_command("custom.launchapp", "launchApp", arguments=[app.app_id, app.name])
        await self.coordinator.async_request_refresh()


class SoundbarLocalMediaPlayer(MediaPlayerEntity):
    """2024-line Samsung soundbar over LAN (HTTPS JSON-RPC on port 1516)."""

    _attr_supported_features = (
        F.TURN_ON
        | F.TURN_OFF
        | F.VOLUME_STEP
        | F.VOLUME_SET
        | F.VOLUME_MUTE
        | F.SELECT_SOURCE
        | F.SELECT_SOUND_MODE
    )

    _SOURCES = [
        "HDMI_IN1",
        "HDMI_IN2",
        "E_ARC",
        "ARC",
        "D_IN",
        "BT",
        "WIFI_IDLE",
    ]

    def __init__(self, coordinator, soundbar: AsyncSoundbarLocal, host: str) -> None:
        self._coordinator = coordinator
        self._soundbar = soundbar
        self._host = host
        self._attr_unique_id = f"soundbar_local_{host}"
        self._attr_name = f"Soundbar {host}"
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER
        self._attr_source_list = list(self._SOURCES)
        self._attr_sound_mode_list = []
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"soundbar_local_{host}")},
            manufacturer="Samsung",
            model="Soundbar (Local)",
            name=self._attr_name,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_update))

    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def state(self) -> str | None:
        power = self._coordinator.data.get("power")
        return STATE_ON if power == "powerOn" else STATE_OFF

    @property
    def volume_level(self) -> float | None:
        try:
            v = float(self._coordinator.data.get("volume", 0) or 0)
        except Exception:
            return None
        return max(0.0, min(1.0, v / 100.0))

    @property
    def is_volume_muted(self) -> bool | None:
        return bool(self._coordinator.data.get("mute", False))

    @property
    def source(self) -> str | None:
        v = self._coordinator.data.get("input")
        return str(v) if isinstance(v, str) else None

    @property
    def sound_mode(self) -> str | None:
        v = self._coordinator.data.get("sound_mode")
        return str(v) if isinstance(v, str) else None

    @property
    def sound_mode_list(self) -> list[str] | None:
        modes = self._coordinator.data.get("supported_sound_modes")
        if isinstance(modes, list):
            valid = [m for m in modes if isinstance(m, str) and m]
            if valid:
                return valid
        current = self.sound_mode
        return [current] if isinstance(current, str) and current else []

    async def async_turn_on(self) -> None:
        await self._soundbar.power_on()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        await self._soundbar.power_off()
        await self._coordinator.async_request_refresh()

    async def async_volume_up(self) -> None:
        await self._soundbar.volume_up()
        await self._coordinator.async_request_refresh()

    async def async_volume_down(self) -> None:
        await self._soundbar.volume_down()
        await self._coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        await self._soundbar.set_volume(int(max(0.0, min(1.0, volume)) * 100))
        await self._coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        if bool(mute) != bool(self.is_volume_muted):
            await self._soundbar.mute_toggle()
            await self._coordinator.async_request_refresh()

    async def async_select_source(self, source: str) -> None:
        await self._soundbar.select_input(source)
        await self._coordinator.async_request_refresh()

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._soundbar.set_sound_mode(sound_mode)
        await self._coordinator.async_request_refresh()


_FRAME_MEDIA_ROOT = "frame_art:root"
_FRAME_MEDIA_LOCAL = "frame_art:local"
_FRAME_MEDIA_INTERNET = "frame_art:internet"
_FRAME_MEDIA_LOCAL_FILE = "frame_art:local_file:"
_FRAME_MEDIA_INTERNET_COLLECTION = "frame_art:internet_collection:"
_FRAME_MEDIA_INTERNET_ITEM = "frame_art:internet_item:"

_INTERNET_COLLECTIONS: dict[str, dict[str, str]] = {
    "museums": {"title": "Museums", "query": "museum,art"},
    "nature": {"title": "Nature", "query": "nature,landscape"},
    "architecture": {"title": "Architecture", "query": "architecture,minimal"},
}
_INTERNET_ITEMS_PER_COLLECTION = 24


class FrameLocalMediaPlayer(MediaPlayerEntity):
    """Frame TV media player with artwork browsing + setting."""

    _attr_has_entity_name = True
    _attr_device_class = MediaPlayerDeviceClass.TV

    def __init__(self, hass: HomeAssistant, coordinator, frame: AsyncFrameLocal, host: str) -> None:
        self.hass = hass
        self._coordinator = coordinator
        self._frame = frame
        self._host = host
        self._attr_unique_id = f"frame_local_{host}_media_player"
        self._attr_name = "Art Browser"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"frame_local_{host}")},
            manufacturer="Samsung",
            model="The Frame (Local)",
            name=f"Frame TV {host}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_update))

    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and bool(self._coordinator.data.get("online", False))

    @property
    def supported_features(self) -> int:
        return F.PLAY_MEDIA | F.BROWSE_MEDIA | F.SELECT_SOURCE

    @property
    def state(self) -> str | None:
        art_mode = self._coordinator.data.get("art_mode")
        if art_mode == "on":
            return STATE_ON
        return STATE_OFF

    @property
    def media_title(self) -> str | None:
        current = self._coordinator.data.get("current_artwork_id")
        return str(current) if isinstance(current, str) and current else None

    @property
    def source_list(self) -> list[str]:
        data = self._coordinator.data.get("installed_apps")
        if isinstance(data, list):
            out: list[str] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                app_id = item.get("appId") or item.get("app_id")
                name = item.get("name")
                if isinstance(app_id, str) and app_id and isinstance(name, str) and name:
                    label = f"{name} ({app_id})"
                    if label not in out:
                        out.append(label)
            if out:
                return out
        return app_options()

    def _frame_art_dir(self) -> Path:
        return Path(self.hass.config.path("FrameTV"))

    def _iter_local_images(self) -> list[Path]:
        base = self._frame_art_dir()
        if not base.exists():
            return []
        out: list[Path] = []
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            out.append(p)
        out.sort()
        return out

    @staticmethod
    def _internet_item_url(collection_slug: str, idx: int) -> str:
        # Deterministic preview/download URLs without API keys.
        return f"https://picsum.photos/seed/{collection_slug}-{idx}/1920/1080"

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        mcid = media_content_id or _FRAME_MEDIA_ROOT
        if mcid == _FRAME_MEDIA_ROOT:
            return BrowseMedia(
                title="Frame TV Art",
                media_class=MediaClass.DIRECTORY,
                media_content_type="library",
                media_content_id=_FRAME_MEDIA_ROOT,
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMedia(
                        title="Local (FrameTV folder)",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="library",
                        media_content_id=_FRAME_MEDIA_LOCAL,
                        can_play=False,
                        can_expand=True,
                    ),
                    BrowseMedia(
                        title="Internet collections",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="library",
                        media_content_id=_FRAME_MEDIA_INTERNET,
                        can_play=False,
                        can_expand=True,
                    ),
                ],
            )

        if mcid == _FRAME_MEDIA_LOCAL:
            base = self._frame_art_dir()
            children: list[BrowseMedia] = []
            for p in self._iter_local_images():
                rel = p.relative_to(base).as_posix()
                children.append(
                    BrowseMedia(
                        title=rel,
                        media_class=MediaClass.IMAGE,
                        media_content_type="image",
                        media_content_id=f"{_FRAME_MEDIA_LOCAL_FILE}{rel}",
                        can_play=True,
                        can_expand=False,
                    )
                )
            return BrowseMedia(
                title="Local (FrameTV)",
                media_class=MediaClass.DIRECTORY,
                media_content_type="library",
                media_content_id=_FRAME_MEDIA_LOCAL,
                can_play=False,
                can_expand=True,
                children=children,
            )

        if mcid == _FRAME_MEDIA_INTERNET:
            children = [
                BrowseMedia(
                    title=cfg["title"],
                    media_class=MediaClass.DIRECTORY,
                    media_content_type="library",
                    media_content_id=f"{_FRAME_MEDIA_INTERNET_COLLECTION}{slug}",
                    can_play=False,
                    can_expand=True,
                )
                for slug, cfg in _INTERNET_COLLECTIONS.items()
            ]
            return BrowseMedia(
                title="Internet collections",
                media_class=MediaClass.DIRECTORY,
                media_content_type="library",
                media_content_id=_FRAME_MEDIA_INTERNET,
                can_play=False,
                can_expand=True,
                children=children,
            )

        if mcid.startswith(_FRAME_MEDIA_INTERNET_COLLECTION):
            slug = mcid[len(_FRAME_MEDIA_INTERNET_COLLECTION) :]
            cfg = _INTERNET_COLLECTIONS.get(slug)
            if cfg is None:
                raise ValueError("Unknown internet collection")
            children: list[BrowseMedia] = []
            for idx in range(1, _INTERNET_ITEMS_PER_COLLECTION + 1):
                children.append(
                    BrowseMedia(
                        title=f"{cfg['title']} #{idx}",
                        media_class=MediaClass.IMAGE,
                        media_content_type="image",
                        media_content_id=f"{_FRAME_MEDIA_INTERNET_ITEM}{slug}:{idx}",
                        can_play=True,
                        can_expand=False,
                        thumbnail=self._internet_item_url(slug, idx),
                    )
                )
            return BrowseMedia(
                title=cfg["title"],
                media_class=MediaClass.DIRECTORY,
                media_content_type="library",
                media_content_id=mcid,
                can_play=False,
                can_expand=True,
                children=children,
            )

        raise ValueError("Unsupported media path")

    async def _set_local_artwork(self, rel_path: str) -> None:
        base = self._frame_art_dir().resolve()
        path = (base / rel_path).resolve()
        if not str(path).startswith(str(base)):
            raise HomeAssistantError("Invalid local media path")
        if not path.is_file():
            raise HomeAssistantError(f"Local media not found: {rel_path}")
        content_id = await self._frame.upload_artwork(str(path))
        await self._frame.select_artwork(content_id, True)
        await self._coordinator.async_request_refresh()

    async def _set_internet_artwork(self, slug: str, idx: int) -> None:
        if slug not in _INTERNET_COLLECTIONS:
            raise HomeAssistantError("Unknown internet collection")
        if idx < 1:
            raise HomeAssistantError("Invalid internet item index")
        url = self._internet_item_url(slug, idx)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HomeAssistantError("Invalid URL scheme")
        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=20) as resp:
                resp.raise_for_status()
                data = await resp.read()
        except Exception as err:
            raise HomeAssistantError(
                "Internet collection unavailable (network/DNS issue). Try Local source or retry later."
            ) from err
        if not data:
            raise HomeAssistantError("Internet image download returned empty body")
        with tempfile.NamedTemporaryFile(prefix="frame_art_", suffix=".jpg", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            content_id = await self._frame.upload_artwork(tmp_path)
            await self._frame.select_artwork(content_id, True)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        await self._coordinator.async_request_refresh()

    async def async_play_media(
        self,
        media_type: str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        media_id = str(media_id or "").strip()
        media_type = str(media_type or "").strip().lower()

        app = None
        if media_id.startswith("app:"):
            app = resolve_app(media_id.split(":", 1)[1])
        else:
            app = resolve_app(media_id)
        if app is not None:
            await self._frame.run_app(app.app_id)
            await self._coordinator.async_request_refresh()
            return

        if is_http_url(media_id):
            if is_youtube_url(media_id):
                await self._frame.run_app(YOUTUBE_APP.app_id, "DEEP_LINK", media_id)
            else:
                await self._frame.open_url(media_id)
            await self._coordinator.async_request_refresh()
            return

        if media_id.startswith(_FRAME_MEDIA_LOCAL_FILE):
            rel = media_id[len(_FRAME_MEDIA_LOCAL_FILE) :]
            await self._set_local_artwork(rel)
            return

        if media_id.startswith(_FRAME_MEDIA_INTERNET_ITEM):
            token = media_id[len(_FRAME_MEDIA_INTERNET_ITEM) :]
            slug, _, idx_raw = token.partition(":")
            idx = int(idx_raw)
            await self._set_internet_artwork(slug, idx)
            return

        if media_type in ("app", "apps"):
            # Accept raw app id from automation.
            await self._frame.run_app(media_id)
            await self._coordinator.async_request_refresh()
            return

        if media_type == "url":
            await self._frame.open_url(media_id)
            await self._coordinator.async_request_refresh()
            return

        raise HomeAssistantError("Unsupported media item")

    async def async_select_source(self, source: str) -> None:
        app = resolve_app(source)
        if app is None:
            raise HomeAssistantError(f"Unknown app source: {source}")
        await self._frame.run_app(app.app_id)
        await self._coordinator.async_request_refresh()
