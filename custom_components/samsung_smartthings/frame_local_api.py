from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class FrameLocalError(Exception):
    """Raised when local Frame TV operations fail."""


class FrameLocalConnectionError(FrameLocalError):
    """Raised when the Frame TV local API is temporarily unreachable."""


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


def _open_art_channel_resilient(art: Any) -> Any:
    """Open Art API while ignoring Samsung's existing-client notifications."""
    from samsungtvws.connection import SamsungTVWSConnection
    from samsungtvws.event import (
        MS_CHANNEL_CLIENT_CONNECT_EVENT,
        MS_CHANNEL_CLIENT_DISCONNECT_EVENT,
        MS_CHANNEL_READY_EVENT,
    )
    from samsungtvws.exceptions import ConnectionFailure

    SamsungTVWSConnection.open(art)
    while True:
        event, frame = art._recv_frame()
        if event in (
            MS_CHANNEL_CLIENT_CONNECT_EVENT,
            MS_CHANNEL_CLIENT_DISCONNECT_EVENT,
        ):
            continue
        if event != MS_CHANNEL_READY_EVENT:
            art.close()
            raise ConnectionFailure(frame)
        return art.connection


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
        # samsungtvws can occasionally hang beyond its socket timeout depending on TV state.
        # Use a hard wall-clock timeout to avoid blocking the coordinator forever.
        self._hard_timeout = max(int(timeout) + 5, 15)
        self._ws_name = ws_name
        self._token_file = token_file
        self._lock = asyncio.Lock()
        self._active_port: int | None = None
        self._unsupported_methods: set[str] = set()
        self._unsupported_tv_methods: set[str] = set()
        # The Frame REST endpoint can remain healthy while the Art WebSocket
        # stops answering (this is common on newer Frame firmware).  Do not
        # reconnect to a wedged Art channel on every 20-second coordinator
        # poll; retry it periodically while keeping REST reachability alive.
        self._art_api_available: bool | None = None
        self._last_art_probe = 0.0
        self._art_probe_interval = 60.0
        self._offline_grace_cycles = 3

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
                "host is unreachable",
                "host is down",
                "no route to host",
                "connection refused",
                "network is unreachable",
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
        # Observed in the field:
        # - `get_thumbnail_list` can return -6 on some firmware profiles (treat as unsupported; fall back to current only).
        if code == -6 and method == "get_thumbnail_list":
            return True
        # - `set_slideshow_status` can return -7 on models that don't expose slideshow control.
        if code == -7 and method == "set_slideshow_status":
            return True
        # - `delete_list` can return -10 even when individual delete works.
        if code == -10 and method == "delete_list":
            return True
        return False

    async def _call(self, fn, *args, **kwargs):
        async with self._lock:
            job = self._hass.async_add_executor_job(lambda: fn(*args, **kwargs))
            try:
                return await asyncio.wait_for(job, timeout=self._hard_timeout)
            except asyncio.TimeoutError as err:
                raise TimeoutError(f"Frame local call hard-timeout after {self._hard_timeout}s") from err

    async def _tv_call(self, method: str, *args, **kwargs):
        if method in self._unsupported_tv_methods:
            raise FrameLocalUnsupportedError(f"{method} is unavailable/unauthorized on this Frame TV")

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
                # Apps/remote methods require a separate websocket auth/pairing on many TVs.
                # When not authorized, samsungtvws raises UnauthorizedError with event ms.channel.unauthorized.
                if "ms.channel.unauthorized" in str(err).lower():
                    self._unsupported_tv_methods.add(method)
                    raise FrameLocalUnsupportedError(
                        f"{method} unauthorized. Approve pairing on the TV for remote control features."
                    ) from err
                if not self._is_connection_error(err):
                    raise FrameLocalError(str(err)) from err
            finally:
                try:
                    await self._call(tv.close)
                except Exception:
                    pass
        if last_err is not None:
            raise FrameLocalConnectionError(str(last_err)) from last_err
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
                    # samsungtvws 3.0.3 treats clientConnect/clientDisconnect
                    # notifications from other Art clients as a failed
                    # handshake. On Frame firmware these events are normal;
                    # drain them until the channel-ready event arrives.
                    art.open = lambda: _open_art_channel_resilient(art)
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
            raise FrameLocalConnectionError(str(last_err)) from last_err
        raise FrameLocalError("Unknown frame local error")

    async def ping(self) -> bool:
        try:
            await self.get_api_version()
            return True
        except Exception:
            return False

    async def is_reachable(self) -> bool:
        """Return whether a Samsung local API port accepts TCP connections.

        Samsung can leave the Art WebSocket handshake hanging while the TV is
        still on the LAN. Availability must not depend on that protocol-level
        handshake; Art reads remain best-effort below.
        """
        for port in self._candidate_ports(include_alternates=True):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, port),
                    timeout=3,
                )
                writer.close()
                await writer.wait_closed()
                self._active_port = port
                return True
            except Exception:
                continue
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
            # The Art channel can accept TCP and then never complete its
            # protocol handshake. Commands must fail fast so a cloud fallback
            # can keep the user-facing flow responsive.
            await asyncio.wait_for(
                self._art_call("set_artmode", enabled),
                timeout=8,
            )
            return
        except (FrameLocalUnsupportedError, TimeoutError, asyncio.TimeoutError):
            raise
        except FrameLocalConnectionError:
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
            await asyncio.wait_for(
                self._art_call("set_artmode", target),
                timeout=8,
            )

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
        try:
            data = await self._art_call("get_thumbnail_list")
        except FrameLocalUnsupportedError:
            # Some Frame firmware only returns the active artwork id and no thumbnail list.
            return []
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
        upload_path = path
        upload_type = file_type
        cleanup_path: str | None = None

        if not upload_type:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                upload_type = "jpg"
            elif ext == ".png":
                # Many Frame firmwares reject PNG uploads with error -1. Convert to JPEG when possible.
                try:
                    from PIL import Image  # type: ignore
                except Exception:
                    raise FrameLocalError("PNG upload not supported on this TV without Pillow; convert to JPG") from None

                tmp = Path(tempfile.gettempdir()) / f"frame_upload_{os.getpid()}_{int(asyncio.get_running_loop().time()*1000)}.jpg"
                img = Image.open(path)
                # Ensure compatible JPEG mode.
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(tmp, format="JPEG", quality=92, optimize=True)
                upload_path = str(tmp)
                upload_type = "jpg"
                cleanup_path = upload_path
            elif ext == ".webp":
                # WebP is not reliably accepted by Frame local API; best-effort convert to JPEG.
                try:
                    from PIL import Image  # type: ignore
                except Exception:
                    raise FrameLocalError("WebP upload not supported on this TV without Pillow; convert to JPG") from None

                tmp = Path(tempfile.gettempdir()) / f"frame_upload_{os.getpid()}_{int(asyncio.get_running_loop().time()*1000)}.jpg"
                img = Image.open(path)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(tmp, format="JPEG", quality=92, optimize=True)
                upload_path = str(tmp)
                upload_type = "jpg"
                cleanup_path = upload_path
            else:
                upload_type = ext.lstrip(".") or "jpg"

        try:
            content_id = await self._art_call(
            "upload",
            upload_path,
            matte,
            portrait_matte,
            upload_type,
            None,
        )
        finally:
            if cleanup_path:
                try:
                    Path(cleanup_path).unlink(missing_ok=True)
                except Exception:
                    pass
        if not isinstance(content_id, str) or not content_id:
            raise FrameLocalError("Upload succeeded but no content_id returned")
        return content_id

    async def delete_artwork(self, content_id: str) -> bool:
        result = await self._art_call("delete", content_id)
        return bool(result)

    async def delete_artwork_list(self, content_ids: list[str]) -> bool:
        if not content_ids:
            return True
        try:
            result = await self._art_call("delete_list", content_ids)
            return bool(result)
        except FrameLocalUnsupportedError:
            # Some models reject delete_list (-10) even when single delete works.
            ok = True
            for cid in content_ids:
                try:
                    await self.delete_artwork(cid)
                except Exception:
                    ok = False
            return ok

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

    async def get_state(self, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read Frame state without coupling availability to Art WebSocket health.

        The REST endpoint is a fast, reliable liveness signal.  Art commands
        are best-effort because the Art WebSocket is a separate Samsung
        service and can become a zombie channel.  When that happens, retain
        the last known Art values and retry the channel on a slow cadence.
        """
        previous = previous if isinstance(previous, dict) else {}

        def _previous(key: str, default: Any) -> Any:
            return previous[key] if key in previous else default

        api_version = _previous("api_version", None)
        art_mode = _previous("art_mode", None)
        current = _previous("current_artwork_payload", {})
        if not isinstance(current, dict):
            current = {}
        brightness = _previous("brightness", None)
        art_ids = list(_previous("artwork_ids", [])) if isinstance(_previous("artwork_ids", []), list) else []
        matte_options = list(_previous("matte_options", [])) if isinstance(_previous("matte_options", []), list) else []
        filter_options = list(_previous("photo_filter_options", [])) if isinstance(_previous("photo_filter_options", []), list) else []
        slideshow = _previous("slideshow", {})
        if not isinstance(slideshow, dict):
            slideshow = {}
        artmode_settings = list(_previous("artmode_settings", [])) if isinstance(_previous("artmode_settings", []), list) else []
        online = await self.is_reachable()
        probe_offline = not online
        errors: list[str] = []
        art_api_available = self._art_api_available is True
        offline_cycles = int(previous.get("offline_cycles", 0) or 0)

        if not online:
            errors.append("rest_api: TV REST endpoint is unreachable")
            self._art_api_available = False
            offline_cycles += 1
            # A short TCP/WebSocket interruption is common while a Frame
            # changes power/art state. Keep the last known state available for
            # a few polls so automations do not power-cycle a healthy TV.
            if previous.get("online") is True and offline_cycles <= self._offline_grace_cycles:
                online = True
        else:
            offline_cycles = 0
            now = asyncio.get_running_loop().time()
            should_probe_art = (
                self._art_api_available is not False
                or now - self._last_art_probe >= self._art_probe_interval
            )
            if should_probe_art:
                self._last_art_probe = now
                try:
                    api_version = await self.get_api_version()
                    art_api_available = True
                    self._art_api_available = True
                except Exception as err:
                    # Keep LAN availability independent from the optional Art
                    # handshake. The TCP port is healthy even when this
                    # protocol probe times out or returns a clientConnect
                    # event instead of a version reply.
                    art_api_available = False
                    self._art_api_available = False
                    errors.append(f"art_api_version: {err}")

            # Avoid a long series of WebSocket timeouts when the channel is
            # unhealthy.  Values from the last successful Art poll remain in
            # the coordinator data until the next successful probe.
            if art_api_available:
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
            "art_api_available": art_api_available,
            "transport": (
                "offline_grace"
                if probe_offline and online
                else "art_websocket"
                if art_api_available
                else "rest_only"
                if online
                else "offline"
            ),
            "offline_cycles": offline_cycles,
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
