#!/usr/bin/env python3
"""SmartThings device "full dump" tool.

Reads ST_TOKEN and DEVICE_ID from a keys file (default: /Users/nijssen/keys.txt)
or environment variables and writes:
  - device.json (GET /devices/{id})
  - status.json (GET /devices/{id}/status)
  - capability definitions (GET /capabilities/{capability}/{version})
  - a small summary report (summary.json + summary.txt)

Keys file formats supported:
  - KEY=VALUE lines (ST_TOKEN=..., DEVICE_ID=...)
  - convenience: first non-empty line is token, optional second line is device id

Purpose: make it obvious which capabilities expose actual *setter* commands
for features like "Private Rear Sound" and "Sound Grouping".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

API_BASE = "https://api.smartthings.com/v1"


def _read_keys_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data

    raw_lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()]

    for line in raw_lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v:
            data[k] = v

    # Allow /Users/nijssen/Tokens/smartthings.txt style keys files
    # (TOKEN="...") by mapping TOKEN -> ST_TOKEN.
    if "ST_TOKEN" not in data and "TOKEN" in data:
        data["ST_TOKEN"] = data["TOKEN"]

    if not data:
        compact = [ln for ln in raw_lines if ln and not ln.startswith("#")]
        if len(compact) >= 1:
            data["ST_TOKEN"] = compact[0]
        if len(compact) >= 2:
            data["DEVICE_ID"] = compact[1]

    return data


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "_", s)
    return s[:180] or "capability"


class SmartThingsClient:
    def __init__(self, token: str, timeout: float = 20.0, max_retries: int = 6) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "ha-samsung-smartthings-dev-dump/1.0",
            }
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self._session.request(method, url, timeout=self._timeout, **kwargs)
            except requests.RequestException:
                if attempt >= self._max_retries:
                    raise
                time.sleep(min(2**attempt, 10))
                continue

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    sleep_s = float(retry_after) if retry_after else 2.0
                except ValueError:
                    sleep_s = 2.0
                if attempt >= self._max_retries:
                    r.raise_for_status()
                time.sleep(min(max(sleep_s, 0.5), 30.0))
                continue

            if 500 <= r.status_code < 600 and attempt < self._max_retries:
                time.sleep(min(2**attempt, 10))
                continue

            r.raise_for_status()
            if not r.content:
                return None
            return r.json()

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._request("GET", f"{API_BASE}{path}", params=params)


def _iter_capabilities_from_device(device: dict) -> Iterable[Tuple[str, int]]:
    comps = device.get("components") or []
    for comp in comps:
        for cap in comp.get("capabilities") or []:
            cid = cap.get("id")
            ver = cap.get("version")
            if cid and isinstance(ver, int):
                yield (cid, ver)


def _commands_from_capdef(capdef: dict) -> List[str]:
    cmds = capdef.get("commands")
    if isinstance(cmds, dict):
        return sorted([k for k in cmds.keys() if isinstance(k, str)])
    return []


def _attributes_from_capdef(capdef: dict) -> List[str]:
    attrs = capdef.get("attributes")
    if isinstance(attrs, dict):
        return sorted([k for k in attrs.keys() if isinstance(k, str)])
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--keys",
        default="/Users/nijssen/keys.txt",
        help="Path to keys file (KEY=VALUE lines, or token on first line)",
    )
    ap.add_argument("--device-id", default=None, help="Override DEVICE_ID from keys/env")
    ap.add_argument("--token", default=None, help="Override ST_TOKEN from keys/env (NOT written to output)")
    ap.add_argument("--out", default="tools/output", help="Output directory")
    ap.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    args = ap.parse_args()

    keys = _read_keys_file(Path(args.keys))
    token = args.token or os.environ.get("ST_TOKEN") or keys.get("ST_TOKEN") or ""
    device_id = args.device_id or os.environ.get("DEVICE_ID") or keys.get("DEVICE_ID") or ""

    if not token:
        print("Missing ST_TOKEN (env or keys file).", file=sys.stderr)
        return 2
    if not device_id:
        print("Missing DEVICE_ID (env or keys file).", file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser()
    _mkdir(out_dir)
    capdef_dir = out_dir / "capdefs"
    _mkdir(capdef_dir)

    st = SmartThingsClient(token=token, timeout=args.timeout)

    try:
        device = st.get(f"/devices/{device_id}")
        status = st.get(f"/devices/{device_id}/status")
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 401:
            print("SmartThings API returned 401 Unauthorized (token likely expired/invalid).", file=sys.stderr)
            return 3
        raise

    _write_json(out_dir / "device.json", device)
    _write_json(out_dir / "status.json", status)

    cap_pairs = sorted(set(_iter_capabilities_from_device(device)))
    capdefs: Dict[str, dict] = {}
    cap_fail: List[dict] = []
    for cid, ver in cap_pairs:
        fn = f"{_slug(cid)}_v{ver}.json"
        path = capdef_dir / fn
        if path.exists():
            try:
                capdef = json.loads(path.read_text(encoding="utf-8"))
                capdefs[f"{cid}@{ver}"] = capdef
                continue
            except Exception:
                pass
        try:
            capdef = st.get(f"/capabilities/{cid}/{ver}")
            capdefs[f"{cid}@{ver}"] = capdef
            _write_json(path, capdef)
        except Exception as e:
            cap_fail.append({"capability": cid, "version": ver, "error": str(e)})

    focus_terms = [
        "audioGroup",
        "group",
        "rear",
        "private",
        "surround",
        "speaker",
        "night",
        "voice",
        "soundmode",
        "audioInput",
        "inputSource",
    ]
    focus_re = re.compile("|".join([re.escape(t) for t in focus_terms]), re.IGNORECASE)

    summary_caps: List[dict] = []
    for key, capdef in sorted(capdefs.items()):
        cid = capdef.get("id") or key.split("@", 1)[0]
        ver = capdef.get("version")
        cmds = _commands_from_capdef(capdef)
        attrs = _attributes_from_capdef(capdef)
        if not cmds and not attrs:
            continue
        if focus_re.search(str(cid)) or any(focus_re.search(c) for c in cmds) or any(focus_re.search(a) for a in attrs):
            summary_caps.append({"capability": cid, "version": ver, "commands": cmds, "attributes": attrs})

    private_rear_candidates = []
    for c in summary_caps:
        if re.search(r"private|rear", str(c.get("capability", "")), re.IGNORECASE):
            private_rear_candidates.append(c)
        else:
            for cmd in c.get("commands", []):
                if re.search(r"private|rear", cmd, re.IGNORECASE):
                    private_rear_candidates.append(c)
                    break

    grouping_candidates = []
    for c in summary_caps:
        if re.search(r"group", str(c.get("capability", "")), re.IGNORECASE):
            grouping_candidates.append(c)
        else:
            for cmd in c.get("commands", []):
                if re.search(r"group|join|leave", cmd, re.IGNORECASE):
                    grouping_candidates.append(c)
                    break

    summary = {
        "device_id": device_id,
        "label": device.get("label"),
        "name": device.get("name"),
        "manufacturer": device.get("manufacturerName"),
        "model": device.get("model"),
        "deviceTypeName": device.get("deviceTypeName"),
        "components": [c.get("id") for c in (device.get("components") or [])],
        "capability_count": len(cap_pairs),
        "capability_definitions_downloaded": len(capdefs),
        "capability_definitions_failed": cap_fail,
        "focus_capabilities": summary_caps,
        "private_rear_sound_candidates": private_rear_candidates,
        "sound_grouping_candidates": grouping_candidates,
        "notes": {
            "interpretation": (
                "If there is no capability exposing a setter-style command for a feature, "
                "it is usually not controllable through the public SmartThings Device API. "
                "Some Samsung app features use private endpoints and will not show up here."
            )
        },
    }

    _write_json(out_dir / "summary.json", summary)

    lines: List[str] = []
    lines.append(f"Device: {summary.get('label') or summary.get('name')} ({device_id})")
    lines.append(
        f"Manufacturer: {summary.get('manufacturer')}  Model: {summary.get('model')}  Type: {summary.get('deviceTypeName')}"
    )
    lines.append(f"Capabilities (from device payload): {summary.get('capability_count')}")
    lines.append("")

    lines.append("Private Rear Sound candidates (capabilities/commands mentioning private/rear):")
    if private_rear_candidates:
        for c in private_rear_candidates:
            lines.append(f"- {c.get('capability')} v{c.get('version')} commands={c.get('commands')}")
    else:
        lines.append("- (none found)")
    lines.append("")

    lines.append("Sound Grouping candidates (capabilities/commands mentioning group/join/leave):")
    if grouping_candidates:
        for c in grouping_candidates:
            lines.append(f"- {c.get('capability')} v{c.get('version')} commands={c.get('commands')}")
    else:
        lines.append("- (none found)")
    lines.append("")

    lines.append("Focus capabilities:")
    for c in summary_caps:
        lines.append(
            f"- {c.get('capability')} v{c.get('version')} commands={c.get('commands')} attrs={c.get('attributes')}"
        )

    if cap_fail:
        lines.append("")
        lines.append("Capability definition download failures:")
        for f in cap_fail:
            lines.append(f"- {f.get('capability')} v{f.get('version')}: {f.get('error')}")

    (out_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "output.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote: {out_dir}/device.json")
    print(f"Wrote: {out_dir}/status.json")
    print(f"Wrote: {out_dir}/summary.json")
    print(f"Wrote: {out_dir}/summary.txt")
    print(f"Wrote capdefs: {capdef_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
