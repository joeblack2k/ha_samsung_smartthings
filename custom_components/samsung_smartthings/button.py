from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity
from .naming import capability_label, command_label


@dataclass(frozen=True, kw_only=True)
class SmartThingsButton:
    capability: str
    command: str
    name: str
    arguments: list[Any] | None = None
    enabled_by_default: bool = True


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for it in domain.get("items") or []:
        coordinator: SmartThingsCoordinator = it["coordinator"]
        dev = coordinator.device
        rt = dev.runtime

        # Remote keys are exposed as a proper `remote` entity (not buttons).

        # Ambient / Frame mode trigger
        if dev.has_capability("samsungvd.ambient") and dev.get_command_def("samsungvd.ambient", "setAmbientOn"):
            entities.append(
                SamsungSmartThingsCommandButton(
                    coordinator,
                    SmartThingsButton(
                        capability="samsungvd.ambient",
                        command="setAmbientOn",
                        name="Ambient/Art Mode",
                        arguments=[],
                        enabled_by_default=True,
                    ),
                    unique_suffix="ambient_on",
                )
            )
        if dev.has_capability("samsungvd.ambient18") and dev.get_command_def("samsungvd.ambient18", "setAmbientOn"):
            entities.append(
                SamsungSmartThingsCommandButton(
                    coordinator,
                    SmartThingsButton(
                        capability="samsungvd.ambient18",
                        command="setAmbientOn",
                        name="Ambient/Art Mode",
                        arguments=[],
                        enabled_by_default=True,
                    ),
                    unique_suffix="ambient18_on",
                )
            )

        # Soundbar: next input source
        if dev.has_capability("samsungvd.audioInputSource") and dev.get_command_def(
            "samsungvd.audioInputSource", "setNextInputSource"
        ):
            entities.append(
                SamsungSmartThingsCommandButton(
                    coordinator,
                    SmartThingsButton(
                        capability="samsungvd.audioInputSource",
                        command="setNextInputSource",
                        name="Next Input Source",
                        arguments=[],
                        enabled_by_default=True,
                    ),
                    unique_suffix="next_input",
                )
            )

        # Generic "no-arg commands" when expose_all is enabled.
        if rt and rt.expose_all:
            # Skip commands already covered by nicer entities (media_player/select/remote/services).
            skip_caps = {
                "switch",
                "audioMute",
                "audioVolume",
                "mediaPlayback",
                "mediaTrackControl",
                "tvChannel",
                "custom.picturemode",
                "custom.soundmode",
                "custom.launchapp",
                "audioNotification",
                "samsungvd.remoteControl",
                "samsungvd.mediaInputSource",
                "mediaInputSource",
                "samsungvd.ambient",
                "samsungvd.ambient18",
                "samsungvd.audioInputSource",
            }
            skip_prefixes = (
                "custom.recording",
                "samsungim.",
            )
            for key, capdef in rt.capability_defs.items():
                if not isinstance(capdef, dict):
                    continue
                cap_id = capdef.get("id")
                if cap_id in skip_caps:
                    continue
                if isinstance(cap_id, str) and cap_id.startswith(skip_prefixes):
                    continue
                cmds = capdef.get("commands")
                if not isinstance(cap_id, str) or not isinstance(cmds, dict):
                    continue
                for cmd_name, cmd_def in cmds.items():
                    if not isinstance(cmd_name, str) or not isinstance(cmd_def, dict):
                        continue
                    args = cmd_def.get("arguments") or []
                    # Create a button only when there are no required args.
                    if isinstance(args, list) and all(isinstance(a, dict) and a.get("optional") is True for a in args):
                        entities.append(
                            SamsungSmartThingsCommandButton(
                                coordinator,
                                SmartThingsButton(
                                    capability=cap_id,
                                    command=cmd_name,
                                    name=f"{capability_label(cap_id)}: {command_label(cap_id, cmd_name)}",
                                    arguments=[],
                                    enabled_by_default=False,
                                ),
                                unique_suffix=f"cmd_{cap_id}_{cmd_name}",
                            )
                        )
                    elif args == []:
                        entities.append(
                            SamsungSmartThingsCommandButton(
                                coordinator,
                                SmartThingsButton(
                                    capability=cap_id,
                                    command=cmd_name,
                                    name=f"{capability_label(cap_id)}: {command_label(cap_id, cmd_name)}",
                                    arguments=[],
                                    enabled_by_default=False,
                                ),
                                unique_suffix=f"cmd_{cap_id}_{cmd_name}",
                            )
                        )

    async_add_entities(entities)


class SamsungSmartThingsCommandButton(SamsungSmartThingsEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartThingsCoordinator, desc: SmartThingsButton, *, unique_suffix: str) -> None:
        super().__init__(coordinator)
        self.desc = desc
        self._attr_unique_id = f"{self.device.device_id}_{unique_suffix}"
        self._attr_name = desc.name
        self._attr_entity_registry_enabled_default = bool(desc.enabled_by_default)
        if not desc.enabled_by_default:
            # Generic / advanced buttons should not clutter the UI.
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        try:
            await self.device.send_command(self.desc.capability, self.desc.command, arguments=self.desc.arguments)
        except Exception:
            # Avoid noisy UI toasts for optional/advanced buttons; log in HA logs.
            import logging
            _LOGGER = logging.getLogger(__name__)
            _LOGGER.exception(
                "Button command failed: device=%s cap=%s cmd=%s",
                self.device.device_id,
                self.desc.capability,
                self.desc.command,
            )
        finally:
            await self.coordinator.async_request_refresh()
