from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

class FrameLocalError(Exception):
    """Raised when local Frame TV operations fail."""


class FrameLocalUnsupportedError(FrameLocalError):
    """Raised when a Frame local API feature is unsupported by the TV model/firmware."""


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            return {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return decoded
        except json.JSONDecodeError:
            return []
    return []


class AsyncFrameLocal:
    """Async-safe wrapper around samsungtvws art API."""

    def __init__(
        self,
        hass,
        *,
        host: str,
        ws_port: int,
        timeout: int,
        ws_name: str,
        token_file: str,
    ) -> None:
        self._hass = hass
        self._host = host
        self._ws_port = ws_port
        self._timeout = timeout
        self._ws_name = ws_name
        self._token_file = token_file
        self._lock = asyncio.Lock()
        self._active_port: int | None = None
        self._unsupported_methods: set[str] = set()

    @property
    def host(self) -> str:
        return self._host

    def _tv(self, port: int):
        try:
            from samsungtvws import SamsungTVWS
        except Exception as err:
            raise FrameLocalError("Missing dependency samsungtvws") from err

        return SamsungTVWS(
            host=self._host,
            port=port,
            timeout=self._timeout,
            name=self._ws_name,
            token_file=self._token_file,
        )

    @staticmethod
    def _is_connection_error(err: Exception) -> bool:
        if isinstance(err, (TimeoutError, ConnectionError, OSError)):
            return True
        # samsungtvws-specific transient network class
        if err.__class__.__name__ == "ConnectionFailure":
            return True
        msg = str(err).lower()
        return any(
            token in msg
            for token in (
                "cannot connect",
                "connect call failed",
                "timed out",
                "host is down",
                "no route to host",
                "connection refused",
                "websocket",
                "clientdisconnect",
            )
        )

    def _candidate_ports(self, *, include_alternates: bool = True) -> list[int]:
        # Prefer the last successful port and configured port first.
        # Alternate probing can be disabled for the first pass to reduce long stalls.
        out: list[int] = []
        for p in (self._active_port, self._ws_port):
            if isinstance(p, int) and p not in out:
                out.append(p)
        if include_alternates:
            for p in (8002, 8001):
                if isinstance(p, int) and p not in out:
                    out.append(p)
        return out

    @staticmethod
    def _extract_error_code(err: Exception) -> int | None:
        match = re.search(r"error number\s*(-?\d+)", str(err), re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _is_unsupported_error(self, method: str, err: Exception) -> bool:
        code = self._extract_error_code(err)
        if code is None:
            return False
        # `-1` is typically returned when the command is unsupported on this model.
        if code == -1 and method in {"set_motion_sensitivity", "set_motion_timer", "set_brightness_sensor_setting"}:
            return True
        return False

    async def _call(self, fn, *args, **kwargs):
        async with self._lock:
            return await self._hass.async_add_executor_job(lambda: fn(*args, **kwargs))

    async def _tv_call(self, method: str, *args, **kwargs):
        last_err: Exception | None = None
        ports = self._candidate_ports(include_alternates=True)
        for port in ports:
            tv = self._tv(port)
            try:
                fn = getattr(tv, method)
                value = await self._call(fn, *args, **kwargs)
                self._active_port = port
                return value
            except Exception as err:
                last_err = err
                if not self._is_connection_error(err):
                    raise FrameLocalError(str(err)) from err
            finally:
                try:
                    await self._call(tv.close)
                except Exception:
                    pass
        if last_err is not None:
            raise FrameLocalError(str(last_err)) from last_err
        raise FrameLocalError("Unknown frame local error")

    async def _art_call(self, method: str, *args, **kwargs):
        if method in self._unsupported_methods:
            raise FrameLocalUnsupportedError(f"{method} is unsupported on this Frame TV")

        last_err: Exception | None = None
        primary_ports = self._candidate_ports(include_alternates=self._active_port is None)
        all_ports = self._candidate_ports(include_alternates=True)
        extra_ports = [p for p in all_ports if p not in primary_ports]
        ports = [*primary_ports, *extra_ports]
        # Older Frame firmware can timeout on first command even when the TV applies it.
        per_port_attempts_by_method = {
            "select_image": 2,
            "set_artmode": 2,
            "upload": 2,
        }
        per_port_attempts = per_port_attempts_by_method.get(method, 1)

        for port in ports:
            for attempt in range(per_port_attempts):
                tv = self._tv(port)
                art = tv.art()
                try:
                    fn = getattr(art, method)
                    value = await self._call(fn, *args, **kwargs)
                    self._active_port = port
                    return value
                except Exception as err:
                    last_err = err
                    if self._is_unsupported_error(method, err):
                        self._unsupported_methods.add(method)
                        raise FrameLocalUnsupportedError(f"{method} unsupported: {err}") from err
                    is_conn = self._is_connection_error(err)
                    if not is_conn:
                        raise FrameLocalError(str(err)) from err
                    if attempt + 1 < per_port_attempts:
                        await asyncio.sleep(0.35)
                finally:
                    try:
                        await self._call(art.close)
                    except Exception:
                        pass
        if last_err is not None:
            raise FrameLocalError(str(last_err)) from last_err
        raise FrameLocalError("Unknown frame local error")

    async def ping(self) -> bool:
        try:
            await self.get_api_version()
            return True
        except Exception:
            return False

    async def get_api_version(self) -> str | None:
        value = await self._art_call("get_api_version")
        return str(value) if value is not None else None

    async def get_art_mode(self) -> str | None:
        value = await self._art_call("get_artmode")
        if isinstance(value, bool):
            return "on" if value else "off"
        if isinstance(value, str):
            vv = value.lower()
            if vv in ("on", "off"):
                return vv
        return None

    async def set_art_mode(self, enabled: bool) -> None:
        target = "on" if enabled else "off"
        try:
            await self._art_call("set_artmode", enabled)
            return
        except FrameLocalUnsupportedError:
            raise
        except FrameLocalError:
            # Some firmware applies the change even if websocket times out.
            try:
                current = await self.get_art_mode()
                if current == target:
                    return
            except Exception:
                pass
            # Retry with explicit "on/off" string for models that dislike bool payloads.
            await self._art_call("set_artmode", target)

    async def get_brightness(self) -> float | None:
        value = await self._art_call("get_brightness")
        try:
            brightness = float(value)
            # Some models report sentinel values (e.g. 10000) when unavailable.
            if 1 <= brightness <= 10:
                return brightness
            return None
        except Exception:
            return None

    async def set_brightness(self, value: float) -> None:
        await self._art_call("set_brightness", int(value))

    async def get_current_artwork(self) -> dict[str, Any]:
        data = await self._art_call("get_current")
        if isinstance(data, dict):
            return data
        return {}

    async def list_artworks(self) -> list[dict[str, Any]]:
        data = await self._art_call("get_thumbnail_list")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("content_list"), list):
                return [x for x in data["content_list"] if isinstance(x, dict)]
            return [data]
        return []

    async def list_artwork_ids(self) -> list[str]:
        out: list[str] = []
        for item in await self.list_artworks():
            cid = item.get("content_id") or item.get("contentId") or item.get("id")
            if isinstance(cid, str) and cid and cid not in out:
                out.append(cid)
        return out

    async def select_artwork(self, content_id: str, show_now: bool = True) -> None:
        await self._art_call("select_image", content_id, None, bool(show_now))

    async def upload_artwork(
        self,
        path: str,
        *,
        matte: str = "shadowbox_polar",
        portrait_matte: str = "shadowbox_polar",
        file_type: str | None = None,
    ) -> str:
        upload_type = file_type
        if not upload_type:
            ext = Path(path).suffix.lower().lstrip(".")
            upload_type = ext or "jpg"
        content_id = await self._art_call(
            "upload",
            path,
            matte,
            portrait_matte,
            upload_type,
            None,
        )
        if not isinstance(content_id, str) or not content_id:
            raise FrameLocalError("Upload succeeded but no content_id returned")
        return content_id

    async def delete_artwork(self, content_id: str) -> bool:
        result = await self._art_call("delete", content_id)
        return bool(result)

    async def delete_artwork_list(self, content_ids: list[str]) -> bool:
        if not content_ids:
            return True
        result = await self._art_call("delete_list", content_ids)
        return bool(result)

    async def get_matte_options(self) -> list[str]:
        data = await self._art_call("get_matte_list")
        parsed = _as_dict(data)
        out: list[str] = []
        for item in _as_list(parsed.get("matte_types")):
            if isinstance(item, dict):
                key = item.get("id") or item.get("name")
                if isinstance(key, str) and key and key not in out:
                    out.append(key)
            elif isinstance(item, str) and item not in out:
                out.append(item)
        return out

    async def get_photo_filter_options(self) -> list[str]:
        data = await self._art_call("get_photo_filter_list")
        out: list[str] = []
        for item in _as_list(data):
            if isinstance(item, dict):
                key = item.get("id") or item.get("name")
                if isinstance(key, str) and key and key not in out:
                    out.append(key)
            elif isinstance(item, str) and item not in out:
                out.append(item)
        return out

    async def change_matte(self, content_id: str, matte: str) -> None:
        await self._art_call("change_matte", content_id, matte, None)

    async def set_photo_filter(self, content_id: str, filter_id: str) -> None:
        await self._art_call("set_photo_filter", content_id, filter_id)

    async def get_slideshow_status(self) -> dict[str, Any]:
        data = await self._art_call("get_slideshow_status")
        return _as_dict(data)

    async def set_slideshow_status(self, minutes: int, shuffled: bool, category_id: str | None = None) -> None:
        duration = int(minutes)
        order = bool(shuffled)
        first_category = category_id
        try:
            await self._art_call("set_slideshow_status", duration, order, 2, first_category)
            return
        except FrameLocalUnsupportedError:
            raise
        except FrameLocalError as err:
            code = self._extract_error_code(err)
            # `-9` usually means the category is invalid on this model/profile.
            if code != -9:
                raise

        tried = {str(first_category)} if first_category else set()
        for fallback_category in ("MY-C0002", "MY-C0004", "MY-C0008"):
            if fallback_category in tried:
                continue
            try:
                await self._art_call("set_slideshow_status", duration, order, 2, fallback_category)
                return
            except FrameLocalUnsupportedError:
                raise
            except FrameLocalError:
                continue
        self._unsupported_methods.add("set_slideshow_status")
        raise FrameLocalError("set_slideshow_status failed: unsupported or invalid category on this model")

    async def set_motion_timer(self, value: str) -> None:
        await self._art_call("set_motion_timer", str(value))

    async def set_motion_sensitivity(self, value: str) -> None:
        await self._art_call("set_motion_sensitivity", str(value))

    async def set_brightness_sensor(self, enabled: bool) -> None:
        await self._art_call("set_brightness_sensor_setting", bool(enabled))

    async def get_artmode_settings(self) -> list[str]:
        data = await self._art_call("get_artmode_settings")
        parsed = _as_dict(data)
        out: list[str] = []
        for item in _as_list(parsed.get("data")):
            if isinstance(item, dict):
                key = item.get("item")
                if isinstance(key, str) and key and key not in out:
                    out.append(key)
        return out

    async def list_apps(self) -> list[dict[str, Any]]:
        data = await self._tv_call("app_list")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []

    async def run_app(self, app_id: str, app_type: str = "DEEP_LINK", meta_tag: str = "") -> None:
        await self._tv_call("run_app", str(app_id), str(app_type), str(meta_tag))

    async def open_url(self, url: str) -> None:
        await self._tv_call("open_browser", str(url))

    async def get_state(self) -> dict[str, Any]:
        api_version = None
        art_mode = None
        current = {}
        brightness = None
        art_ids: list[str] = []
        matte_options: list[str] = []
        filter_options: list[str] = []
        slideshow = {}
        artmode_settings: list[str] = []
        online = False
        errors: list[str] = []

        try:
            api_version = await self.get_api_version()
            online = True
        except Exception as err:
            errors.append(f"api_version: {err}")

        if online:
            for label, fn in (
                ("art_mode", self.get_art_mode),
                ("current_artwork", self.get_current_artwork),
                ("brightness", self.get_brightness),
                ("artworks", self.list_artwork_ids),
                ("matte", self.get_matte_options),
                ("photo_filter", self.get_photo_filter_options),
                ("slideshow", self.get_slideshow_status),
                ("artmode_settings", self.get_artmode_settings),
            ):
                try:
                    value = await fn()
                    if label == "art_mode":
                        art_mode = value
                    elif label == "current_artwork" and isinstance(value, dict):
                        current = value
                    elif label == "brightness":
                        brightness = value
                    elif label == "artworks" and isinstance(value, list):
                        art_ids = value
                    elif label == "matte" and isinstance(value, list):
                        matte_options = value
                    elif label == "photo_filter" and isinstance(value, list):
                        filter_options = value
                    elif label == "slideshow" and isinstance(value, dict):
                        slideshow = value
                    elif label == "artmode_settings" and isinstance(value, list):
                        artmode_settings = value
                except Exception as err:
                    errors.append(f"{label}: {err}")

        current_id = current.get("content_id") or current.get("contentId")
        current_filter = current.get("filter_id") or current.get("filterId")
        current_matte = current.get("matte_id") or current.get("matteId")
        if isinstance(current_id, str) and current_id and current_id not in art_ids:
            # Some Frame firmware only returns the active artwork id and no thumbnail list.
            art_ids = [current_id, *art_ids]

        def _supports(method: str, *setting_keys: str) -> bool:
            # Rely on observed command failures to determine unsupported features.
            # Art mode settings payload is inconsistent across models/firmware and
            # may omit keys for features that still work.
            return method not in self._unsupported_methods

        return {
            "online": online,
            "api_version": api_version,
            "art_mode": art_mode,
            "current_artwork_id": current_id if isinstance(current_id, str) else None,
            "current_filter": current_filter if isinstance(current_filter, str) else None,
            "current_matte": current_matte if isinstance(current_matte, str) else None,
            "current_artwork_payload": current,
            "brightness": brightness,
            "artwork_ids": art_ids,
            "art_count": len(art_ids),
            "matte_options": matte_options,
            "photo_filter_options": filter_options,
            "slideshow": slideshow,
            "artmode_settings": artmode_settings,
            "supports_art_mode": _supports("set_artmode", "artmode_status"),
            "supports_slideshow": _supports("set_slideshow_status", "slideshow_status", "slideshow"),
            "supports_motion_timer": _supports("set_motion_timer", "motion_timer"),
            "supports_motion_sensitivity": _supports("set_motion_sensitivity", "motion_sensitivity"),
            "supports_brightness_sensor": _supports("set_brightness_sensor_setting", "brightness_sensor_setting"),
            "last_errors": errors,
        }

    @staticmethod
    def file_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def file_mtime(path: str) -> int:
        return int(os.path.getmtime(path))
