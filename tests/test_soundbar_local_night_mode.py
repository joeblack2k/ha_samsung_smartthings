"""Offline regression for the soundbar local night-mode fallback.

Run: python -m unittest discover -s tests -v
Install test dependencies: aiohttp, async-timeout.

``soundbar_local_api.py`` has no Home Assistant dependency, so it is loaded
directly from this checkout by file path (bypassing the integration
package's own ``__init__.py``, which does import Home Assistant). No
device, account or network access is used.

Background: on an HW-Q995GF, the primary local method
(``setAdvancedSoundSettings``) returns a JSON-RPC "Method not found" error,
and the documented app-style fallback (``ms.channel.emit`` /
``ed.installedApp.event``) returns a bare, unenveloped
``{"code": ..., "message": "Parse error"}`` object instead of a normal
JSON-RPC envelope. ``_post_any`` only raises on HTTP-level failures, so
without an explicit content check, ``set_night_mode`` would read that
error object, not notice anything wrong, and record the requested state as
if the device had actually changed it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "custom_components/samsung_smartthings/soundbar_local_api.py"

_spec = importlib.util.spec_from_file_location(
    "_soundbar_local_api_night_mode_regression", MODULE_PATH
)
local_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = local_module
_spec.loader.exec_module(local_module)


class NightModeFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = local_module.AsyncSoundbarLocal("soundbar.invalid", None)
        self.client._token = "synthetic-test-token"
        # Force the primary method to behave like firmware that does not
        # implement it at all, as observed live on an HW-Q995GF
        # ("-32601: Method not found"), so every test exercises the fallback.
        self.client.set_advanced_sound_settings = AsyncMock(
            side_effect=local_module.SoundbarLocalError("-32601: Method not found")
        )

    async def test_fallback_success_is_recorded(self):
        self.client._post_any = AsyncMock(return_value={"result": "ok"})
        await self.client.set_night_mode(True)
        self.assertTrue(self.client._night_mode)

    async def test_bare_error_object_from_fallback_is_not_treated_as_success(self):
        # Observed live response: firmware that doesn't understand the
        # ms.channel.emit fallback either, returned as a bare error with no
        # envelope and no "result" key.
        self.client._post_any = AsyncMock(
            return_value={"code": -32700, "message": "Parse error"}
        )
        with self.assertRaises(local_module.SoundbarLocalError):
            await self.client.set_night_mode(True)
        self.assertIsNone(self.client._night_mode)

    async def test_wrapped_error_from_fallback_is_not_treated_as_success(self):
        self.client._post_any = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "id": "1",
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
        with self.assertRaises(local_module.SoundbarLocalError):
            await self.client.set_night_mode(False)
        self.assertIsNone(self.client._night_mode)

    async def test_cache_not_corrupted_by_a_later_failed_attempt(self):
        # A prior successful toggle must not be silently overwritten by a
        # later failed one.
        self.client._post_any = AsyncMock(return_value={"result": "ok"})
        await self.client.set_night_mode(True)
        self.assertTrue(self.client._night_mode)

        self.client._post_any = AsyncMock(
            return_value={"code": -32700, "message": "Parse error"}
        )
        with self.assertRaises(local_module.SoundbarLocalError):
            await self.client.set_night_mode(False)
        # Still True: the failed "off" attempt never actually reached the device.
        self.assertTrue(self.client._night_mode)


if __name__ == "__main__":
    unittest.main()
