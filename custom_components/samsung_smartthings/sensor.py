from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_SOUNDBAR_LOCAL
from .const import ENTRY_TYPE_FRAME_LOCAL
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .frame_local_api import AsyncFrameLocal
from .naming import attribute_label, capability_label, humanize_token


@dataclass(frozen=True, kw_only=True)
class SmartThingsAttr:
    component: str
    capability: str
    attribute: str
    unit: str | None


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_FRAME_LOCAL or domain.get("type") == ENTRY_TYPE_FRAME_LOCAL:
        coordinator = domain["coordinator"]
        frame: AsyncFrameLocal = domain["frame"]
        host = domain.get("host") or "frame"
        entities.extend(
            [
                FrameLocalSimpleSensor(coordinator, frame, host, "api_version", "Art API Version", "api_version"),
                FrameLocalSimpleSensor(coordinator, frame, host, "current_artwork", "Current Artwork", "current_artwork_id"),
                FrameLocalSimpleSensor(coordinator, frame, host, "art_count", "Artwork Count", "art_count"),
                FrameLocalSimpleSensor(
                    coordinator,
                    frame,
                    host,
                    "last_errors",
                    "Last Errors",
                    "last_errors",
                    entity_category=EntityCategory.DIAGNOSTIC,
                    enabled_default=False,
                    visible_default=False,
                ),
            ]
        )
        async_add_entities(entities, True)
        return

    # Local soundbar entry: small set of useful read-only sensors.
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SOUNDBAR_LOCAL or domain.get("type") == ENTRY_TYPE_SOUNDBAR_LOCAL:
        coordinator = domain["coordinator"]
        host = domain.get("host") or "soundbar"
        entities.append(SoundbarLocalSimpleSensor(coordinator, host, "codec", "Audio Codec", "codec"))
        entities.append(SoundbarLocalSimpleSensor(coordinator, host, "identifier", "Identifier", "identifier", entity_category=EntityCategory.DIAGNOSTIC))
        async_add_entities(entities, True)
        return

    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device

        # Avoid duplicating the curated sensors with generic attribute sensors.
        curated = {
            ("main", "ocf", "mnmo"),
            ("main", "ocf", "mnfv"),
            ("main", "samsungvd.thingStatus", "status"),
            ("main", "custom.picturemode", "pictureMode"),
            ("main", "custom.soundmode", "soundMode"),
        }

        # Expose *all* attributes as sensors when enabled.
        if dev.runtime and dev.runtime.expose_all:
            seen: set[tuple[str, str, str]] = set()
            for comp, cap, attr, _val, unit in dev.flatten_attributes():
                key = (comp, cap, attr)
                if key in curated:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                entities.append(
                    SamsungSmartThingsAttrSensor(
                        coordinator,
                        SmartThingsAttr(component=comp, capability=cap, attribute=attr, unit=unit),
                    )
                )

        # A few useful "always-on" device info sensors (even if expose_all is off).
        entities.append(
            SamsungSmartThingsSimpleSensor(
                coordinator,
                "model_number",
                "Model Number",
                lambda d: d.get_attr("ocf", "mnmo"),
            )
        )
        entities.append(
            SamsungSmartThingsSimpleSensor(
                coordinator,
                "firmware_version",
                "Firmware Version",
                lambda d: d.get_attr("ocf", "mnfv"),
            )
        )
        entities.append(
            SamsungSmartThingsSimpleSensor(
                coordinator,
                "status",
                "Status",
                lambda d: d.get_attr("samsungvd.thingStatus", "status"),
            )
        )

        # TV: expose picture/sound mode as read-only sensors by default.
        if dev.has_capability("custom.picturemode"):
            entities.append(
                SamsungSmartThingsSimpleSensor(
                    coordinator,
                    "picture_mode",
                    "Picture Mode",
                    lambda d: d.get_attr("custom.picturemode", "pictureMode"),
                )
            )
        if dev.has_capability("custom.soundmode"):
            entities.append(
                SamsungSmartThingsSimpleSensor(
                    coordinator,
                    "sound_mode",
                    "Sound Mode",
                    lambda d: d.get_attr("custom.soundmode", "soundMode"),
                )
            )

        # Soundbar: show the current audio input source as a read-only sensor.
        # Many soundbars expose supported sources but SmartThings cloud often cannot
        # reliably set them (dropdown would be misleading).
        if dev.has_capability("samsungvd.audioInputSource"):
            entities.append(
                SamsungSmartThingsSimpleSensor(
                    coordinator,
                    "audio_input_source",
                    "Audio Input Source",
                    lambda d: d.get_attr("samsungvd.audioInputSource", "inputSource"),
                )
            )

    async_add_entities(entities)


class SamsungSmartThingsAttrSensor(SamsungSmartThingsEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SmartThingsCoordinator, desc: SmartThingsAttr) -> None:
        super().__init__(coordinator)
        self.desc = desc
        self._attr_unique_id = f"{self.device.device_id}_attr_{desc.component}_{desc.capability}_{desc.attribute}"
        # Prefer human-friendly names; keep component/capability context.
        comp_prefix = "" if desc.component == "main" else f"{humanize_token(desc.component)}: "
        cap = capability_label(desc.capability)
        attr = attribute_label(desc.capability, desc.attribute)
        self._attr_name = f"{comp_prefix}{cap}: {attr}"
        self._attr_native_unit_of_measurement = desc.unit

    @property
    def native_value(self) -> Any:
        v = self.device.get_attr(self.desc.capability, self.desc.attribute, component=self.desc.component)
        # Avoid invalid HA sensor states for dict/list: keep state short, put full value in attributes.
        if isinstance(v, list):
            return f"list({len(v)})"
        if isinstance(v, dict):
            return f"dict({len(v)})"
        if isinstance(v, str) and len(v) > 240:
            return v[:240] + "..."
        return v

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        v = self.device.get_attr(self.desc.capability, self.desc.attribute, component=self.desc.component)
        if isinstance(v, (list, dict)):
            return {"value": v}
        if isinstance(v, str) and len(v) > 240:
            return {"value_full": v}
        return None


class SamsungSmartThingsSimpleSensor(SamsungSmartThingsEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator, key: str, name: str, fn) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{self.device.device_id}_{key}"
        self._attr_name = name
        self._fn = fn

    @property
    def native_value(self) -> Any:
        return self._fn(self.device)


class SoundbarLocalSimpleSensor(SensorEntity):
    """Simple sensor backed by the local soundbar coordinator data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        host: str,
        key: str,
        name: str,
        data_key: str,
        *,
        entity_category: EntityCategory | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._data_key = data_key
        self._attr_unique_id = f"soundbar_local_{host}_{key}"
        self._attr_name = name
        self._attr_entity_category = entity_category
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

    @property
    def native_value(self) -> Any:
        return self._coordinator.data.get(self._data_key)


class FrameLocalSimpleSensor(SensorEntity):
    """Simple sensor backed by local Frame coordinator data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        frame: AsyncFrameLocal,
        host: str,
        key: str,
        name: str,
        data_key: str,
        *,
        entity_category: EntityCategory | None = None,
        enabled_default: bool = True,
        visible_default: bool = True,
    ) -> None:
        self._coordinator = coordinator
        self._frame = frame
        self._data_key = data_key
        self._attr_unique_id = f"frame_local_{host}_{key}"
        self._attr_name = name
        self._attr_entity_category = entity_category
        self._attr_entity_registry_enabled_default = enabled_default
        self._attr_entity_registry_visible_default = visible_default
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

    @property
    def native_value(self) -> Any:
        value = self._coordinator.data.get(self._data_key)
        if isinstance(value, list):
            return f"list({len(value)})"
        if isinstance(value, dict):
            return f"dict({len(value)})"
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        value = self._coordinator.data.get(self._data_key)
        if isinstance(value, (list, dict)):
            return {"value": value}
        return None
