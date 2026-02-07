from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DeviceRuntime:
    device_id: str
    device: dict[str, Any]
    status: dict[str, Any]
    capability_defs: dict[str, dict[str, Any]]  # key: "capId/version" -> def payload

    expose_all: bool

