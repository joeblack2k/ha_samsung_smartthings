from __future__ import annotations

import json
import asyncio
from typing import Any, Iterable

from aiohttp import ClientResponseError

from .models import DeviceRuntime
from .smartthings_api import SmartThingsApi


def _cap_key(cap_id: str, version: int) -> str:
    return f"{cap_id}/{version}"


class SmartThingsDevice:
    """Helper around SmartThings device + status payloads."""

    def __init__(self, api: SmartThingsApi, device_id: str, *, expose_all: bool) -> None:
        self.api = api
        self.device_id = device_id
        self.expose_all = expose_all
        self.runtime: DeviceRuntime | None = None

    async def async_init(self) -> None:
        device = await self.api.get_device(self.device_id)
        status = await self.api.get_status(self.device_id)

        cap_defs: dict[str, dict[str, Any]] = {}
        for cap_id, ver in self.iter_capabilities(device):
            try:
                cap_defs[_cap_key(cap_id, ver)] = await self.api.get_capability_def(cap_id, ver)
            except Exception:
                # Don't fail setup if one cap def fetch fails.
                cap_defs[_cap_key(cap_id, ver)] = {}

        self.runtime = DeviceRuntime(
            device_id=self.device_id,
            device=device,
            status=status,
            capability_defs=cap_defs,
            expose_all=self.expose_all,
        )

    def update_runtime(self, device: dict[str, Any], status: dict[str, Any]) -> None:
        assert self.runtime is not None
        self.runtime.device = device
        self.runtime.status = status

    @staticmethod
    def iter_capabilities(device: dict[str, Any]) -> Iterable[tuple[str, int]]:
        seen: set[tuple[str, int]] = set()
        for comp in device.get("components", []) or []:
            for cap in comp.get("capabilities", []) or []:
                cap_id = cap.get("id")
                ver = cap.get("version")
                if isinstance(cap_id, str) and cap_id and isinstance(ver, int):
                    t = (cap_id, ver)
                    if t not in seen:
                        seen.add(t)
                        yield t

    # -------- status helpers --------

    def _components(self) -> dict[str, Any]:
        if not self.runtime:
            return {}
        comps = self.runtime.status.get("components") if isinstance(self.runtime.status, dict) else None
        if not isinstance(comps, dict):
            return {}
        return comps

    def _component(self, component: str) -> dict[str, Any]:
        comp = self._components().get(component)
        return comp if isinstance(comp, dict) else {}

    def _main(self) -> dict[str, Any]:
        return self._component("main")

    def has_capability(self, cap_id: str) -> bool:
        if not self.runtime:
            return False
        dev = self.runtime.device
        for comp in dev.get("components", []) or []:
            for cap in comp.get("capabilities", []) or []:
                if cap.get("id") == cap_id:
                    return True
        return False

    def get_attr(self, cap_id: str, attr: str, *, component: str = "main") -> Any:
        cap = self._component(component).get(cap_id)
        if not isinstance(cap, dict):
            return None
        node = cap.get(attr)
        if not isinstance(node, dict):
            return None
        return node.get("value")

    def get_attr_unit(self, cap_id: str, attr: str, *, component: str = "main") -> str | None:
        cap = self._component(component).get(cap_id)
        if not isinstance(cap, dict):
            return None
        node = cap.get(attr)
        if not isinstance(node, dict):
            return None
        u = node.get("unit")
        return u if isinstance(u, str) else None

    def flatten_attributes(self) -> list[tuple[str, str, str, Any, str | None]]:
        """Return [(component, capability, attribute, value, unit)] for all components."""
        out: list[tuple[str, str, str, Any, str | None]] = []
        for comp_id, comp in self._components().items():
            if not isinstance(comp_id, str) or not isinstance(comp, dict):
                continue
            for cap_id, cap in comp.items():
                if not isinstance(cap_id, str) or not isinstance(cap, dict):
                    continue
                for attr, node in cap.items():
                    if not isinstance(attr, str) or not isinstance(node, dict):
                        continue
                    if "value" not in node:
                        continue
                    out.append((comp_id, cap_id, attr, node.get("value"), node.get("unit")))
        return out

    # -------- command helpers --------

    async def send_command(
        self,
        capability: str,
        command: str,
        *,
        arguments: list[Any] | None = None,
        component: str = "main",
    ) -> None:
        cmd: dict[str, Any] = {
            "component": component,
            "capability": capability,
            "command": command,
        }
        if arguments is not None:
            cmd["arguments"] = arguments
        try:
            await self.api.send_commands(self.device_id, [cmd])
        except ClientResponseError as exc:
            # SmartThings can return 409 while a previous command is still being processed.
            if exc.status in (409, 429):
                await asyncio.sleep(1.0)
                await self.api.send_commands(self.device_id, [cmd])
            else:
                raise

    async def raw_command_json(self, component: str, capability: str, command: str, args_json: str) -> None:
        args = None
        s = (args_json or "").strip()
        if s:
            parsed = json.loads(s)
            if parsed is None:
                args = None
            elif isinstance(parsed, list):
                args = parsed
            else:
                args = [parsed]
        await self.send_command(capability, command, arguments=args, component=component)
