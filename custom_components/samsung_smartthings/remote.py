from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.components.remote import RemoteEntity

# Home Assistant has moved/renamed feature flags for `remote` across versions.
# Try known import locations and fall back to the numeric bit for SEND_COMMAND.
try:
    from homeassistant.components.remote import RemoteEntityFeature  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - depends on HA version
    try:
        from homeassistant.components.remote.const import RemoteEntityFeature  # type: ignore
    except Exception:  # pragma: no cover - depends on HA version
        from enum import IntFlag

        class RemoteEntityFeature(IntFlag):  # type: ignore[no-redef]
            SEND_COMMAND = 1

def _get_send_mask() -> int:
    """Return the bitmask for the 'send command' feature across HA versions."""
    for name in ("SEND_COMMAND", "COMMAND", "SEND"):
        f = getattr(RemoteEntityFeature, name, None)
        if f is None:
            continue
        try:
            return int(f)
        except Exception:
            # Some Enums may not be int-like; fall through to legacy bit.
            pass
    # Last resort: legacy bit value.
    return 1


class _FeatureMask(int):
    """Int mask that also supports `feature in mask` membership checks."""

    def __contains__(self, item: object) -> bool:
        try:
            iv = int(item)  # RemoteEntityFeature is typically IntFlag
        except Exception:
            return False
        return (int(self) & iv) == iv


_SEND_MASK = _get_send_mask()
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    entities: list[RemoteEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device
        if dev.has_capability("samsungvd.remoteControl"):
            entities.append(SamsungSmartThingsRemote(coordinator))
    async_add_entities(entities)


class SamsungSmartThingsRemote(SamsungSmartThingsEntity, RemoteEntity):
    _attr_has_entity_name = True

    @property
    def supported_features(self):  # type: ignore[override]
        # Return an int-like mask for old HA versions, but also support new versions
        # that do membership checks (`FEATURE in supported_features`).
        return _FeatureMask(_SEND_MASK)

    def __init__(self, coordinator: SmartThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.device.device_id}_remote"
        self._attr_name = "Remote"

    async def async_send_command(self, command: list[str], **kwargs: Any) -> None:
        # SmartThings expects: send(keyValue, keyState)
        # We allow entries like "HOME" or "HOME:PRESSED".
        for cmd in command:
            if not isinstance(cmd, str) or not cmd:
                continue
            if ":" in cmd:
                key, state = cmd.split(":", 1)
                key = key.strip()
                state = state.strip() or "PRESS_AND_RELEASED"
            else:
                key = cmd.strip()
                state = "PRESS_AND_RELEASED"
            try:
                await self.device.send_command(
                    "samsungvd.remoteControl",
                    "send",
                    arguments=[key, state],
                )
            except ClientResponseError as exc:
                # Remote failures are common if TV is off/asleep; keep noise low.
                _LOGGER.warning(
                    "Remote command failed: device=%s key=%s status=%s",
                    self.device.device_id,
                    key,
                    exc.status,
                )
            except Exception:
                _LOGGER.exception("Remote command failed: device=%s key=%s", self.device.device_id, key)

        await self.coordinator.async_request_refresh()
