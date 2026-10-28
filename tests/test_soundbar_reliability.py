"""Offline soundbar regressions; no device, account or network access required.

Run: python -m unittest discover -s tests -v
Install test dependencies: aiohttp, async-timeout, voluptuous.

Cloud device, API, coordinator, model and local client modules load unchanged
from this checkout under an isolated package name. Only Home Assistant framework
dependencies are stubbed. The options-flow classes/factory load their real AST
nodes against a read-only config_entry test double. This is not a full HA test.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientConnectionError, ClientResponseError
import voluptuous as vol


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components/samsung_smartthings"
PACKAGE = "_samsung_soundbar_regression"


class HomeAssistantError(Exception):
    pass


class UpdateFailed(Exception):
    pass


class Coordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval):
        self.data = None
        self.update_interval = update_interval


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    return result


def load_modules():
    package = module(PACKAGE)
    package.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = package

    def forbid_session(*args):
        raise AssertionError("These tests must never create an HTTP session")

    doubles = {
        "homeassistant": module("homeassistant"),
        "homeassistant.core": module("homeassistant.core", HomeAssistant=object),
        "homeassistant.exceptions": module(
            "homeassistant.exceptions", HomeAssistantError=HomeAssistantError
        ),
        "homeassistant.helpers": module("homeassistant.helpers"),
        "homeassistant.helpers.aiohttp_client": module(
            "homeassistant.helpers.aiohttp_client",
            async_get_clientsession=forbid_session,
        ),
        "homeassistant.helpers.update_coordinator": module(
            "homeassistant.helpers.update_coordinator",
            DataUpdateCoordinator=Coordinator, UpdateFailed=UpdateFailed,
        ),
    }
    with patch.dict(sys.modules, doubles):
        return tuple(
            importlib.import_module(f"{PACKAGE}.{name}")
            for name in ("device", "coordinator", "soundbar_local_api", "const")
        )


device_module, coordinator_module, local_module, const = load_modules()


def status(power="on", payload=None):
    return {"components": {"main": {
        "switch": {"switch": {"value": power}},
        "execute": {"data": {"value": {"payload": payload or {}}}},
    }}}


def record(execute=True):
    capabilities = [{"id": "samsungvd.audioInputSource", "version": 1}]
    if execute:
        capabilities.append({"id": "execute", "version": 1})
    return {"components": [{"id": "main", "capabilities": capabilities}]}


class CloudTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = NS(
            send_commands=AsyncMock(), get_status=AsyncMock(return_value=status()),
        )
        self.device = device_module.SmartThingsDevice(
            self.api, "synthetic-device", expose_all=False, device=record(),
        )
        await self.device.async_init()
        self.device.update_runtime_status(status())
        self.clock = NS(value=1000.0)
        self.sleep = AsyncMock()
        self.asyncio_patch = patch.object(device_module, "asyncio", NS(
            get_running_loop=lambda: NS(time=lambda: self.clock.value),
            sleep=self.sleep,
        ))
        self.asyncio_patch.start()
        self.addCleanup(self.asyncio_patch.stop)

    async def test_empty_payload_poll_only_sends_queries_not_mode_writes(self):
        await self.device.update_execute_features()
        sent = self.api.send_commands.await_args_list
        self.assertEqual(len(sent), 4)
        self.assertEqual(
            [call.args[1][0]["arguments"] for call in sent],
            [[const.EXECUTE_SOUNDMODE], [const.EXECUTE_WOOFER],
             [const.EXECUTE_EQ], [const.EXECUTE_ADVANCED_AUDIO]],
        )
        self.assertTrue(self.device._sb_soundmodes)
        self.assertIs(self.device._sb_execute_supported, True)

    async def test_user_cloud_candidates_preserved_without_probing(self):
        self.device._cloud_soundmodes = ["STANDARD", "SURROUND"]
        await self.device.update_execute_features()
        self.assertEqual(self.device._sb_soundmodes, ["STANDARD", "SURROUND"])
        self.assertEqual(self.api.send_commands.await_count, 4)

    async def test_reported_list_takes_precedence_over_fallback(self):
        payload = {"x.com.samsung.networkaudio.supportedSoundmode": ["VENDOR_MODE"],
                   "x.com.samsung.networkaudio.soundmode": "VENDOR_MODE"}
        self.device.execute_query = AsyncMock(return_value=payload)
        await self.device.update_execute_features()
        self.assertEqual(self.device._sb_soundmodes, ["VENDOR_MODE"])
        self.assertEqual(self.device._sb_soundmode, "VENDOR_MODE")

    async def test_empty_supported_list_keeps_last_reported_options_and_mode(self):
        self.device._sb_soundmodes = ["VENDOR_MODE"]
        self.device._sb_soundmode = "VENDOR_MODE"
        self.device.execute_query = AsyncMock(return_value={
            "x.com.samsung.networkaudio.supportedSoundmode": [],
        })
        await self.device.update_execute_features()
        self.assertEqual(self.device._sb_soundmodes, ["VENDOR_MODE"])
        self.assertEqual(self.device._sb_soundmode, "VENDOR_MODE")

    async def test_current_mode_is_preserved_when_building_fallback_options(self):
        self.device.execute_query = AsyncMock(return_value={
            "x.com.samsung.networkaudio.soundmode": "VENDOR_MODE",
        })
        await self.device.update_execute_features()
        self.assertIn("VENDOR_MODE", self.device._sb_soundmodes)
        self.assertEqual(self.device._sb_soundmode, "VENDOR_MODE")

    async def test_invalid_supported_list_is_not_split_into_character_options(self):
        self.device.execute_query = AsyncMock(return_value={
            "x.com.samsung.networkaudio.supportedSoundmode": "STANDARD",
        })
        await self.device.update_execute_features()
        self.assertIn("STANDARD", self.device._sb_soundmodes)
        self.assertNotIn("S", self.device._sb_soundmodes)

    async def test_poll_throttle_is_unchanged(self):
        self.device.execute_query = AsyncMock(return_value={})
        await self.device.update_execute_features()
        self.clock.value += 299
        await self.device.update_execute_features()
        self.assertEqual(self.device.execute_query.await_count, 4)
        self.clock.value += 1
        await self.device.update_execute_features()
        self.assertEqual(self.device.execute_query.await_count, 8)

    async def test_off_and_unknown_power_skip_queries_without_disabling(self):
        for power in ("off", None, "unknown"):
            with self.subTest(power=power):
                self.device.update_runtime_status(status(power))
                await self.device.update_execute_features()
                self.api.send_commands.assert_not_awaited()
                self.assertIs(self.device._sb_execute_supported, True)
                self.assertEqual(self.device._sb_last_execute_poll, 0)
        self.device.update_runtime_status(status("on"))
        await self.device.update_execute_features()
        self.assertEqual(self.api.send_commands.await_count, 4)

    async def test_missing_execute_capability_stays_unsupported(self):
        self.device.runtime.device = record(execute=False)
        await self.device.update_execute_features()
        self.assertIs(self.device._sb_execute_supported, False)
        self.api.send_commands.assert_not_awaited()

    async def test_coordinator_rechecks_capability_instead_of_latching_false(self):
        self.device._sb_execute_supported = False
        coordinator = coordinator_module.SmartThingsCoordinator(
            object(), self.device, hub_id="synthetic-hub", scan_interval=60,
        )
        await coordinator._async_update_data()
        self.assertIs(self.device._sb_execute_supported, True)
        self.assertEqual(self.api.send_commands.await_count, 4)

    async def test_coordinator_off_on_cycle_queries_only_after_power_returns(self):
        coordinator = coordinator_module.SmartThingsCoordinator(
            object(), self.device, hub_id="synthetic-hub", scan_interval=60,
        )
        self.api.get_status.return_value = status("off")
        await coordinator._async_update_data()
        self.api.send_commands.assert_not_awaited()
        self.api.get_status.return_value = status("on")
        await coordinator._async_update_data()
        self.assertEqual(self.api.send_commands.await_count, 4)

    async def test_coordinator_does_not_query_execute_features_on_tvs(self):
        self.device.runtime.device = {"components": [{"capabilities": [
            {"id": "tvChannel", "version": 1}, {"id": "execute", "version": 1},
        ]}]}
        coordinator = coordinator_module.SmartThingsCoordinator(
            object(), self.device, hub_id="synthetic-hub", scan_interval=60,
        )
        await coordinator._async_update_data()
        self.api.send_commands.assert_not_awaited()

    async def test_explicit_selection_still_sends_requested_mode(self):
        await self.device.set_soundbar_soundmode("SURROUND")
        self.api.send_commands.assert_awaited_once_with("synthetic-device", [{
            "component": "main", "capability": "execute", "command": "execute",
            "arguments": [const.EXECUTE_SOUNDMODE,
                          {"x.com.samsung.networkaudio.soundmode": "SURROUND"}],
        }])

    async def test_cancellation_propagates(self):
        self.device.execute_query = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.device.update_execute_features()


def recovery_case(error_factory):
    async def test(self):
        self.device._sb_soundmodes = ["STANDARD"]
        self.device._sb_soundmode = "STANDARD"
        self.device.execute_query = AsyncMock(side_effect=error_factory())
        await self.device.update_execute_features()
        self.assertIs(self.device._sb_execute_supported, True)
        self.assertEqual(self.device._sb_soundmode, "STANDARD")
        self.assertEqual(self.device._sb_soundmodes, ["STANDARD"])
        self.assertEqual(self.device.execute_query.await_count, 1)
        self.clock.value += 1
        await self.device.update_execute_features()
        self.assertEqual(self.device.execute_query.await_count, 1)
        self.clock.value += 300
        self.device.execute_query = AsyncMock(return_value={})
        await self.device.update_execute_features()
        self.assertEqual(self.device.execute_query.await_count, 4)
    return test


for code in (409, 429, 500, 502, 503, 504):
    setattr(CloudTests, f"test_later_poll_recovers_after_http_{code}", recovery_case(
        lambda code=code: ClientResponseError(None, (), status=code),
    ))
CloudTests.test_later_poll_recovers_after_timeout = recovery_case(TimeoutError)
CloudTests.test_later_poll_recovers_after_disconnect = recovery_case(ClientConnectionError)


class MetadataTests(unittest.TestCase):
    def metadata(self, value):
        device = device_module.SmartThingsDevice(
            NS(), "synthetic-device", expose_all=False,
        )
        device.runtime = NS(status={"components": {"main": {
            "audioTrackData": {"audioTrackData": {"value": value}},
        }}})
        return device.get_media_metadata()

    def test_dict_and_json_track_metadata(self):
        data = {"title": "Synthetic title", "artist": "Synthetic artist"}
        for value in (data, json.dumps(data)):
            with self.subTest(value=value):
                self.assertEqual(self.metadata(value), (data["title"], data["artist"]))

    def test_absent_and_malformed_metadata_are_optional(self):
        for value in (None, "", "not json", "null", "[]", [], 42, True):
            with self.subTest(value=value):
                self.assertEqual(self.metadata(value), (None, None))

    def test_each_metadata_field_is_validated_independently(self):
        self.assertEqual(self.metadata({"title": "Synthetic", "artist": []}), ("Synthetic", None))
        self.assertEqual(self.metadata({"title": 42, "artist": "Synthetic"}), (None, "Synthetic"))


class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = local_module.AsyncSoundbarLocal("soundbar.invalid", NS())
        self.client._token = "synthetic-test-token"

        async def response(payload):
            method = payload["method"]
            values = {
                "powerControl": {"power": "powerOn"}, "getVolume": {"volume": 12},
                "getMute": {"mute": False}, "inputSelectControl": {"inputSource": "E_ARC"},
                "soundModeControl": {"soundMode": "VENDOR_MODE"},
                "getCodec": {"codec": "PCM"}, "getIdentifier": {"identifier": "synthetic"},
            }
            params = set(payload.get("params", {})) - {"AccessToken"}
            if params:
                raise AssertionError(f"Unexpected device write: {method} {params}")
            return values[method]

        self.client._post = AsyncMock(side_effect=response)

    async def test_discovery_and_forced_refresh_do_not_contact_device(self):
        expected = self.client.default_sound_mode_candidates()
        modes = await self.client.detect_supported_sound_modes()
        self.assertEqual(modes, expected)
        modes.clear()
        self.assertEqual(await self.client.detect_supported_sound_modes(), expected)
        self.assertEqual(await self.client.detect_supported_sound_modes(force=True), expected)
        self.client._post.assert_not_awaited()

    async def test_initial_and_subsequent_status_only_read(self):
        for _ in range(2):
            state = await self.client.status()
            self.assertEqual(state["sound_mode"], "VENDOR_MODE")
            self.assertIn("VENDOR_MODE", state["supported_sound_modes"])
        self.assertEqual(self.client._post.await_count, 14)

    async def test_explicit_local_selection_still_writes_exact_requested_mode(self):
        self.client._post = AsyncMock(return_value={})
        await self.client.set_sound_mode("SURROUND")
        self.client._post.assert_awaited_once_with({
            "jsonrpc": "2.0", "method": "soundModeControl", "id": 1,
            "params": {"soundMode": "SURROUND", "AccessToken": "synthetic-test-token"},
        })

    async def test_off_status_does_not_probe_modes(self):
        self.client.power_state = AsyncMock(return_value="powerOff")
        state = await self.client.status()
        self.assertEqual(state["power"], "powerOff")
        self.assertEqual(self.client._post.await_count, 6)


class OptionsBase:
    @property
    def config_entry(self):
        return self._entry

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


def callback(function):
    function._callback = True
    return function


def options_flow():
    tree = ast.parse((SOURCE / "config_flow.py").read_text(encoding="utf-8"))
    flow = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConfigFlow")
    factory = next(n for n in flow.body if isinstance(n, ast.FunctionDef) and n.name == "async_get_options_flow")
    options = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OptionsFlowHandler")
    factory_class = ast.ClassDef(name="Factory", bases=[], keywords=[], body=[factory], decorator_list=[])
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    selected = ast.fix_missing_locations(ast.Module(body=[future, options, factory_class], type_ignores=[]))
    scope = {name: getattr(const, name) for name in dir(const) if name.startswith(("CONF_", "DEFAULT_"))}
    scope.update(config_entries=NS(OptionsFlow=OptionsBase), callback=callback, vol=vol)
    exec(compile(selected, "<actual options-flow source>", "exec"), scope)
    return scope["Factory"], scope["OptionsFlowHandler"]


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.factory, self.flow_class = options_flow()
        self.flow = self.factory.async_get_options_flow(NS())
        self.flow._entry = NS(options={const.CONF_SCAN_INTERVAL: 120,
                                      const.CONF_CLOUD_SOUNDMODES: "STANDARD,SURROUND"})

    def test_factory_uses_framework_owned_read_only_config_entry(self):
        self.assertIsInstance(self.flow, self.flow_class)
        self.assertTrue(self.factory.async_get_options_flow._callback)
        with self.assertRaises(AttributeError):
            self.flow.config_entry = NS()

    async def test_form_retains_saved_options(self):
        form = await self.flow.async_step_init()
        self.assertEqual(form["type"], "form")
        defaults = form["data_schema"]({})
        self.assertEqual(defaults[const.CONF_SCAN_INTERVAL], 120)
        self.assertEqual(defaults[const.CONF_CLOUD_SOUNDMODES], "STANDARD,SURROUND")

    async def test_valid_submission_keeps_existing_option_contract(self):
        result = await self.flow.async_step_init({const.CONF_SCAN_INTERVAL: 90})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][const.CONF_SCAN_INTERVAL], 90)

    async def test_invalid_intervals_still_report_form_errors(self):
        for data, error in [
            ({const.CONF_SCAN_INTERVAL: 1}, "invalid_scan_interval"),
            ({const.CONF_DISCOVERY_INTERVAL: 1}, "invalid_discovery_interval"),
        ]:
            with self.subTest(data=data):
                result = await self.flow.async_step_init(data)
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["errors"], {"base": error})


if __name__ == "__main__":
    unittest.main()
