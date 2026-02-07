from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartThingsCoordinator
from .entity_base import SamsungSmartThingsEntity


@dataclass(frozen=True, kw_only=True)
class SmartThingsButton:
    capability: str
    command: str
    name: str
    arguments: list[Any] | None = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartThingsCoordinator = domain["coordinator"]
    dev = coordinator.device
    rt = dev.runtime

    entities: list[ButtonEntity] = []

    # TV remote keys -> buttons
    if dev.has_capability("samsungvd.remoteControl") and rt:
        # Try to read enum from cap def.
        keys: list[str] = []
        for k, capdef in rt.capability_defs.items():
            if not k.startswith("samsungvd.remoteControl/"):
                continue
            cmds = capdef.get("commands") or {}
            send = cmds.get("send") or {}
            args = send.get("arguments") or []
            if args and isinstance(args, list) and isinstance(args[0], dict):
                schema = args[0].get("schema") or {}
                enum = schema.get("enum")
                if isinstance(enum, list):
                    keys = [x for x in enum if isinstance(x, str)]
            break
        if not keys:
            keys = ["HOME", "BACK", "OK", "UP", "DOWN", "LEFT", "RIGHT", "SOURCE"]
        for key in keys:
            entities.append(
                SamsungSmartThingsCommandButton(
                    coordinator,
                    SmartThingsButton(
                        capability="samsungvd.remoteControl",
                        command="send",
                        name=f"Remote {key}",
                        arguments=[key, "PRESS_AND_RELEASED"],
                    ),
                    unique_suffix=f"remote_{key}",
                )
            )

    # Ambient / Frame mode trigger
    if dev.has_capability("samsungvd.ambient"):
        entities.append(
            SamsungSmartThingsCommandButton(
                coordinator,
                SmartThingsButton(capability="samsungvd.ambient", command="setAmbientOn", name="Ambient/Art Mode"),
                unique_suffix="ambient_on",
            )
        )
    if dev.has_capability("samsungvd.ambient18"):
        entities.append(
            SamsungSmartThingsCommandButton(
                coordinator,
                SmartThingsButton(capability="samsungvd.ambient18", command="setAmbientOn", name="Ambient/Art Mode (v18)"),
                unique_suffix="ambient18_on",
            )
        )

    # Soundbar: next input source
    if dev.has_capability("samsungvd.audioInputSource"):
        entities.append(
            SamsungSmartThingsCommandButton(
                coordinator,
                SmartThingsButton(capability="samsungvd.audioInputSource", command="setNextInputSource", name="Next Input Source"),
                unique_suffix="next_input",
            )
        )

    # Generic "no-arg commands" when expose_all is enabled.
    if rt and rt.expose_all:
        for key, capdef in rt.capability_defs.items():
            if not isinstance(capdef, dict):
                continue
            cap_id = capdef.get("id")
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
                            SmartThingsButton(capability=cap_id, command=cmd_name, name=f"{cap_id}.{cmd_name}"),
                            unique_suffix=f"cmd_{cap_id}_{cmd_name}",
                        )
                    )
                elif args == []:
                    entities.append(
                        SamsungSmartThingsCommandButton(
                            coordinator,
                            SmartThingsButton(capability=cap_id, command=cmd_name, name=f"{cap_id}.{cmd_name}"),
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

    async def async_press(self) -> None:
        await self.device.send_command(self.desc.capability, self.desc.command, arguments=self.desc.arguments)
        await self.coordinator.async_request_refresh()
