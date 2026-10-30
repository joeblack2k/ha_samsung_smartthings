"""Synthetic protocol/credential regressions; no network or personal data.

Run: python -m unittest discover -s tests -v
Dependencies: smartthings-local==0.1.24 and voluptuous.
HA lifecycle/entities/config-flow use framework doubles, not a full HA instance.
"""
from __future__ import annotations

import ast
import asyncio
import base64
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import plistlib
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import cbor2
import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_soundbar_ocf_test'
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'custom_components/samsung_smartthings')]
sys.modules[PACKAGE] = package
api = importlib.import_module(PACKAGE + '.soundbar_ocf_api')
settings = importlib.import_module(PACKAGE + '.soundbar_ocf_settings')
discovery = importlib.import_module(PACKAGE + '.soundbar_ocf_discovery')
spec = importlib.util.spec_from_file_location('_soundbar_ocf_recovery_test', ROOT / 'tools/recover_soundbar_ocf_key.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

DEVICE = UUID('11111111-1111-4111-8111-111111111111')
CLIENT = UUID('22222222-2222-4222-8222-222222222222')
OTHER = UUID('33333333-3333-4333-8333-333333333333')
KEY = bytes(range(16))
P = 'x.com.samsung.networkaudio.'


def config(**changes):
    return {'version': 1, 'host': '192.168.1.50', 'device_id': str(DEVICE),
            'identity': str(CLIENT), 'key': base64.b64encode(KEY).decode(), **changes}


class Session:
    def __init__(self):
        self.posts = []
        self.reads = []
        self.closed = False
        self.apply_writes = True
        self.lose_ack = False
        self.code = 68
        self.bodies = {
            '/oic/d': {'di': str(DEVICE)},
            '/sec/networkaudio/advancedaudio': {P+'voiceamplifier': 0, P+'nightmode': 0, P+'bassboost': 0},
            '/sec/networkaudio/surroundspeaker': {P+'currentRearPosition': 'Rear'},
            '/sec/networkaudio/av/soundsetting': {'activevoiceamplifier': 0, 'spacefitsound': 1},
            '/sec/networkaudio/woofer': {P+'woofer': 0},
            '/sec/networkaudio/tone': {P+'bass': 0, P+'treble': 0},
            '/sec/networkaudio/eq': {P+'EQname': 'NONE', P+'supportedList': ['NONE', 'POP', 'JAZZ', 'CLASSIC', 'CUSTOM']},
            '/sec/networkaudio/channelVolume': {P+'channelVolume': [
                {'name': value, 'status': 1, 'value': 0} for value in settings.SPEAKERS.values()
            ]},
        }

    def connect(self, **kwargs):
        pass

    def start_reader(self):
        pass

    def close(self):
        self.closed = True

    def get(self, path, **kwargs):
        path = '/' + '/'.join(path)
        self.reads.append(path)
        return (69, cbor2.dumps(self.bodies[path])) if path in self.bodies else (132, b'')

    def post(self, path, payload, **kwargs):
        path = '/' + '/'.join(path)
        body = cbor2.loads(payload)
        self.posts.append((path, deepcopy(body)))
        if self.apply_writes and self.code >> 5 == 2:
            if P+'channelVolume' in body:
                wanted = body[P+'channelVolume'][0]
                for row in self.bodies[path][P+'channelVolume']:
                    if row['name'] == wanted['name']:
                        row['value'] = wanted['value']
            else:
                self.bodies[path].update(body)
        if self.lose_ack:
            raise TimeoutError('synthetic lost acknowledgement')
        return self.code, b''


def client(session=None):
    session = session or Session()
    result = settings.SettingsClient(api.Credentials.parse(json.dumps(config())),
        endpoint=lambda: 49155, session_factory=lambda _: session, sleep=lambda _: None)
    return result, session


class TransportTests(unittest.TestCase):
    def test_credentials_are_validated_and_repr_hides_secrets(self):
        credential = api.Credentials.parse(json.dumps(config()))
        self.assertNotIn(base64.b64encode(KEY).decode(), repr(credential))
        self.assertNotIn(str(CLIENT), repr(credential))
        for change in ({'host': '127.0.0.1'}, {'host': '8.8.8.8'}, {'host': '224.0.0.1'},
                       {'version': True}, {'key': 'bad'}, {'key': base64.b64encode(b'x').decode()},
                       {'identity': str(UUID(int=0))}, {'device_id': 'not-a-uuid'}):
            with self.subTest(change=change), self.assertRaises(api.LocalControlError):
                api.Credentials.parse(json.dumps(config(**change)))
        with self.assertRaises(api.LocalControlError):
            api.Credentials.parse(' ' * 4097)

    def test_private_loader_refuses_escape_and_oversize(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'credentials.json').write_text(json.dumps(config()))
            self.assertEqual(api.load_credentials(root, 'credentials.json').device_id, DEVICE)
            for filename in ('../credentials.json', 'a/b.json', 'a\\b.json', 'missing.json', 'not-json'):
                with self.subTest(filename=filename), self.assertRaises(api.LocalControlError):
                    api.load_credentials(root, filename)
            (root/'credentials.json').write_text('x'*4097)
            with self.assertRaises(api.LocalControlError):
                api.load_credentials(root, 'credentials.json')

    def test_identity_mismatch_stops_before_setting_reads_or_writes(self):
        c, s = client()
        s.bodies['/oic/d']['di'] = str(OTHER)
        factory = Mock(return_value=s)
        c._factory = factory
        with self.assertRaises(api.IdentityMismatch):
            c.apply('night_mode', 'on')
        self.assertEqual(s.reads, ['/oic/d'])
        self.assertEqual(factory.call_count, 1)
        self.assertFalse(s.posts)
        self.assertTrue(s.closed)

    def test_prewrite_connection_failure_can_retry_and_closes_sessions(self):
        bad, good = Session(), Session()
        bad.connect = Mock(side_effect=TimeoutError())
        c, _ = client()
        c._factory = Mock(side_effect=[bad, good])
        self.assertEqual(c.read()['night_mode'], 'off')
        self.assertTrue(bad.closed and good.closed)
        self.assertFalse(good.posts)

    def test_all_sixteen_settings_read_without_any_write(self):
        c, s = client()
        self.assertEqual(set(c.read()) - {'equalizer_options'}, set(settings.ALL))
        self.assertEqual(len(settings.ALL), 16)
        self.assertFalse(s.posts)
        self.assertTrue(s.closed)

    def test_unsupported_optional_resource_does_not_hide_other_settings(self):
        c, s = client()
        del s.bodies['/sec/networkaudio/av/soundsetting']
        del s.bodies['/sec/networkaudio/tone']
        values = c.read()
        self.assertEqual(values['voice_enhancement'], 'off')
        self.assertNotIn('spacefit_sound', values)
        self.assertNotIn('bass', values)

    def test_absent_voice_enhancement_does_not_block_other_supported_controls(self):
        c, s = client()
        s.bodies['/sec/networkaudio/advancedaudio'].pop(P+'voiceamplifier')
        self.assertEqual(c.read()['rear_speaker_mode'], 'Rear')

    def test_inactive_channel_and_wrong_types_are_not_reported_as_zero(self):
        c, s = client()
        s.bodies['/sec/networkaudio/channelVolume'][P+'channelVolume'][0]['status'] = 0
        s.bodies['/sec/networkaudio/woofer'][P+'woofer'] = True
        values = c.read()
        self.assertNotIn('speaker_center', values)
        self.assertNotIn('woofer_level', values)

    def test_malformed_channel_list_is_isolated_from_other_features(self):
        c, s = client()
        s.bodies['/sec/networkaudio/channelVolume'][P+'channelVolume'] = None
        result = c.read()
        self.assertEqual(result['night_mode'], 'off')
        self.assertNotIn('speaker_rear', result)

    def test_empty_supported_settings_fail_instead_of_success_with_stale_state(self):
        c, s = client()
        s.bodies = {'/oic/d': {'di': str(DEVICE)}}
        with self.assertRaises(api.LocalControlError):
            c.read()

    def test_invalid_values_are_rejected_before_connecting(self):
        c, s = client()
        for control, value in [('bass', 1.5), ('bass', True), ('woofer_level', -13),
                               ('treble', 7), ('speaker_rear', -7), ('night_mode', 'bad'),
                               ('unknown', 0), ('rear_speaker_mode', 'Invalid')]:
            with self.subTest(control=control, value=value), self.assertRaises(api.LocalControlError):
                c.apply(control, value)
        self.assertFalse(s.reads or s.posts)

    def test_noop_reads_back_without_writing(self):
        c, s = client()
        result = c.apply('rear_speaker_mode', 'Rear')
        self.assertFalse(result['changed'])
        self.assertTrue(result['device_confirmed'])
        self.assertFalse(s.posts)

    def test_all_settings_apply_with_one_post_each_and_verified_readback(self):
        choices = {'voice_enhancement': 'on', 'rear_speaker_mode': 'Front',
                   'equalizer_preset': 'CUSTOM', **{k: 'on' for k in settings.TOGGLES},
                   **{k: 1 for k in settings.NUMBERS}}
        choices['spacefit_sound'] = 'off'
        for control, value in choices.items():
            with self.subTest(control=control):
                c, s = client()
                result = c.apply(control, value)
                self.assertEqual(result['values'][control], value)
                self.assertEqual(len(s.posts), 1)
                self.assertTrue(s.closed)

    def test_speaker_post_only_changes_selected_channel(self):
        c, s = client()
        result = c.apply('speaker_rear', 2)
        self.assertEqual(s.posts, [('/sec/networkaudio/channelVolume', {
            P+'channelVolume': [{'name': 'Spk_Rear', 'value': 2}]
        })])
        self.assertEqual(result['values']['speaker_center'], 0)

    def test_eq_requires_advertisement_and_explicit_set_action(self):
        c, s = client()
        c.apply('equalizer_preset', 'CUSTOM')
        self.assertEqual(s.posts[0][1], {P+'EQname': 'CUSTOM', P+'action': 'setEQmode'})
        c, s = client()
        s.bodies['/sec/networkaudio/eq'][P+'supportedList'] = ['NONE']
        with self.assertRaises(api.LocalControlError):
            c.apply('equalizer_preset', 'CUSTOM')
        self.assertFalse(s.posts)

    def test_lost_acknowledgement_uses_readback_never_resends(self):
        c, s = client()
        s.lose_ack = True
        self.assertTrue(c.apply('night_mode', 'on')['device_confirmed'])
        self.assertEqual(len(s.posts), 1)

    def test_lost_acknowledgement_without_effect_fails_once(self):
        c, s = client()
        s.lose_ack, s.apply_writes = True, False
        with self.assertRaises(api.LocalControlError):
            c.apply('night_mode', 'on')
        self.assertEqual(len(s.posts), 1)
        self.assertTrue(s.closed)

    def test_rejected_post_is_not_retried(self):
        c, s = client()
        s.code = 132
        with self.assertRaises(api.LocalControlError):
            c.apply('voice_enhancement', 'on')
        self.assertEqual(len(s.posts), 1)

    def test_malformed_responses_do_not_leak_their_contents(self):
        for payload in (b'', b'private-not-cbor', cbor2.dumps({})+b'extra', b'x'*65537):
            with self.subTest(size=len(payload)), self.assertRaises(api.LocalControlError) as error:
                api.decode(payload)
            self.assertEqual(str(error.exception), 'Invalid local response')


class DiscoveryTests(unittest.TestCase):
    def test_known_host_and_token_are_required(self):
        token = b'12345678'
        data = bytes([0x58, 69, 0, 1]) + token + b'\xff\xa0'
        self.assertEqual(discovery.matched_reply(data, ('192.168.1.50', 49152), '192.168.1.50', {token}), (1, 1, 49152))
        self.assertIsNone(discovery.matched_reply(data, ('192.168.1.51', 49152), '192.168.1.50', {token}))
        self.assertIsNone(discovery.matched_reply(data, ('192.168.1.50', 49152), '192.168.1.50', {b'other'}))
        self.assertIsNone(discovery.matched_reply(data[:-1], ('192.168.1.50', 49152), '192.168.1.50', {token}))

    def test_discovery_is_get_only_and_socket_free_invalid_input(self):
        packet = discovery.request(b'12345678', 1, True, True)
        self.assertEqual(packet[1], 1)  # CoAP GET
        with patch.object(discovery.socket, 'socket') as socket:
            for target in ('8.8.8.8', '224.0.0.1', '0.0.0.0', '127.0.0.1'):
                with self.assertRaises(ValueError):
                    discovery.discover(target)
            socket.assert_not_called()


def store(entries, **sections):
    return cbor2.dumps({'cred': cbor2.dumps({'rowneruuid': str(CLIENT), 'creds': entries}),
                        'doxm': {'deviceuuid': str(CLIENT)}, **sections})


def credential(subject=DEVICE, key=KEY, encoding=None):
    return {'credtype': 1, 'subjectuuid': str(subject),
            'privatedata': {'data': key, 'encoding': encoding}}


class RecoveryTests(unittest.TestCase):
    def test_only_one_exact_matching_key_selected(self):
        self.assertEqual(recovery.select_credential(store([credential(OTHER), credential()]), DEVICE), (CLIENT, KEY))
        for entries in ([credential(OTHER)], [credential(), credential()]):
            with self.assertRaises(ValueError):
                recovery.select_credential(store(entries), DEVICE)

    def test_base64_key_supported_conflicting_identity_rejected(self):
        payload = store([credential(key=base64.b64encode(KEY).decode(), encoding='oic.sec.encoding.base64')])
        self.assertEqual(recovery.select_credential(payload, DEVICE), (CLIENT, KEY))
        with self.assertRaises(ValueError):
            recovery.select_credential(store([credential()], doxm={'deviceuuid': str(OTHER)}), DEVICE)

    def test_corrupt_trailing_and_oversized_store_rejected(self):
        for payload in (cbor2.dumps({})+b'x', b'x'*(recovery.MAX_STORE+1)):
            with self.assertRaises(ValueError):
                recovery.decode(payload)

    def test_output_created_exclusively_not_printed_and_private_on_posix(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'credentials.json'
            recovery.save_private_file(path, config())
            self.assertEqual(json.loads(path.read_text()), config())
            if sys.platform != 'win32':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                recovery.save_private_file(path, {'wrong': True})
            self.assertEqual(json.loads(path.read_text()), config())

    def test_explicit_backup_reads_only_supported_app_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'Manifest.plist').write_bytes(plistlib.dumps({'IsEncrypted': False}))
            (root/'Status.plist').write_bytes(plistlib.dumps({'SnapshotState': 'finished'}))
            file_id = 'a'*40
            (root/'aa').mkdir()
            payload = store([credential()])
            (root/'aa'/file_id).write_bytes(payload)
            connection = sqlite3.connect(root/'Manifest.db')
            connection.execute('CREATE TABLE Files (fileID, domain, relativePath)')
            connection.executemany('INSERT INTO Files VALUES (?,?,?)', [
                (file_id, 'AppDomain-com.samsung.oneconnect4ios', 'Library/oic_svr_db_client.dat'),
                ('b'*40, 'AppDomain-unrelated', 'Library/oic_svr_db_client.dat'),
            ])
            connection.commit(); connection.close()
            self.assertEqual(recovery.read_store(root), payload)
            (root/'Manifest.plist').write_bytes(plistlib.dumps({'IsEncrypted': True}))
            with self.assertRaises(ValueError):
                recovery.read_store(root)

    def test_output_not_written_if_authenticated_read_fails(self):
        api_module, settings_module = Mock(), Mock()
        discovery_module = Mock()
        discovery_module.discover.return_value = {'plaintext_ports': [49152]}
        public = NS(successful=True, payload=cbor2.dumps({'deviceuuid': str(DEVICE)}))
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(recovery, 'load_client_modules', return_value=(api_module, settings_module, discovery_module)), \
             patch.object(recovery, 'read_store', return_value=store([credential()])), \
             patch('smartthings_local.protocol.ocf_discovery.read_plaintext_ocf_resource', return_value=public):
            settings_module.SettingsClient.return_value.read.side_effect = RuntimeError('synthetic failure')
            output = Path(temporary)/'credentials.json'
            with self.assertRaises(RuntimeError):
                recovery.recover(temporary, '192.168.1.50', output)
            self.assertFalse(output.exists())


def module(name, **attrs):
    result = ModuleType(name)
    result.__dict__.update(attrs)
    return result


class FrameworkError(Exception):
    pass


class Coordinator:
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.data = None
        self.last_update_success = True

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()

    async def async_request_refresh(self):
        try:
            self.data = await self._async_update_data()
        except FrameworkError:
            self.last_update_success = False

    def async_set_updated_data(self, data):
        self.data = data


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


DOUBLES = {
    'homeassistant': module('homeassistant'),
    'homeassistant.components': module('homeassistant.components'),
    'homeassistant.components.number': module('homeassistant.components.number', NumberEntity=type('NumberEntity', (), {})),
    'homeassistant.components.select': module('homeassistant.components.select', SelectEntity=type('SelectEntity', (), {})),
    'homeassistant.components.switch': module('homeassistant.components.switch', SwitchEntity=type('SwitchEntity', (), {})),
    'homeassistant.exceptions': module('homeassistant.exceptions', HomeAssistantError=FrameworkError, ConfigEntryError=FrameworkError),
    'homeassistant.helpers': module('homeassistant.helpers'),
    'homeassistant.helpers.entity': module('homeassistant.helpers.entity', DeviceInfo=dict),
    'homeassistant.helpers.update_coordinator': module('homeassistant.helpers.update_coordinator',
        CoordinatorEntity=CoordinatorEntity, DataUpdateCoordinator=Coordinator, UpdateFailed=FrameworkError),
}
with patch.dict(sys.modules, DOUBLES):
    native = importlib.import_module(PACKAGE + '.soundbar_ocf')


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        framework = patch.dict(sys.modules, {**DOUBLES, PACKAGE+'.soundbar_ocf': native})
        framework.start()
        self.addCleanup(framework.stop)
        async def executor(fn, *args):
            return fn(*args)
        self.hass = NS(async_add_executor_job=executor, data={},
            config=NS(path=lambda *parts: '/'.join(parts)), config_entries=NS(
                async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True)))
        self.entry = NS(entry_id='synthetic-entry', unique_id=f'soundbar_ocf_{DEVICE}',
                        data={'entry_type': 'soundbar_ocf', 'credential_file': 'credentials.json'})
        self.coordinator = native.SoundbarOcfCoordinator(self.hass, self.entry,
            api.Credentials.parse(json.dumps(config())))
        self.coordinator.client, self.session = client()
        self.coordinator.data = self.coordinator.client.read()

    async def test_distinct_device_scoped_entities_and_confirmed_state(self):
        entities = [e for platform in native.PLATFORMS for e in native.entities_for(self.coordinator, platform)]
        self.assertEqual(len(entities), 16)
        self.assertEqual(len({e._attr_unique_id for e in entities}), 16)
        self.assertTrue(all(e.available for e in entities))
        self.coordinator.last_update_success = False
        self.assertTrue(all(not e.available for e in entities))

    async def test_native_service_setters_route_locally_with_readback(self):
        switch = native.SoundbarOcfSwitch(self.coordinator, 'night_mode')
        await switch.async_turn_on()
        self.assertTrue(switch.is_on)
        number = native.SoundbarOcfNumber(self.coordinator, 'bass')
        await number.async_set_native_value(2.0)
        self.assertEqual(number.native_value, 2)
        select = native.SoundbarOcfSelect(self.coordinator, 'rear_speaker_mode')
        await select.async_select_option('Front')
        self.assertEqual(select.current_option, 'Front')

    async def test_fractional_nan_bool_and_unadvertised_input_rejected(self):
        number = native.SoundbarOcfNumber(self.coordinator, 'bass')
        for value in (0.5, True, float('nan'), float('inf'), -7):
            with self.assertRaises(FrameworkError):
                await number.async_set_native_value(value)
        select = native.SoundbarOcfSelect(self.coordinator, 'equalizer_preset')
        with self.assertRaises(FrameworkError):
            await select.async_select_option('invented')
        self.assertFalse(self.session.posts)

    async def test_failed_write_never_returns_optimistic_state(self):
        self.session.apply_writes = False
        with self.assertRaises(FrameworkError):
            await self.coordinator.apply('night_mode', 'on')
        self.assertEqual(self.coordinator.data['night_mode'], 'off')
        self.assertEqual(len(self.session.posts), 1)

    async def test_lifecycle_uses_only_three_local_platforms_and_unloads(self):
        with patch.object(native, 'read_configuration', AsyncMock(return_value=api.Credentials.parse(json.dumps(config())))), \
             patch.object(native, 'SoundbarOcfCoordinator', return_value=self.coordinator):
            self.assertTrue(await native.async_setup_entry(self.hass, self.entry))
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(self.entry, ['switch', 'select', 'number'])
        self.assertTrue(await native.async_unload_entry(self.hass, self.entry))
        self.assertNotIn(self.entry.entry_id, self.hass.data[native.DOMAIN])

    async def test_credential_device_change_refused_before_network(self):
        with patch.dict(sys.modules, DOUBLES), \
             patch.object(native, 'read_configuration', AsyncMock(return_value=api.Credentials.parse(json.dumps(config(device_id=str(OTHER)))))), \
             patch.object(native, 'SoundbarOcfCoordinator') as create:
            with self.assertRaises(FrameworkError):
                await native.async_setup_entry(self.hass, self.entry)
            create.assert_not_called()

    async def test_flow_validates_readonly_before_creating_entry(self):
        source = ast.parse((ROOT/'custom_components/samsung_smartthings/config_flow.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'ConfigFlow')
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_step_soundbar_ocf')
        tree = ast.Module(body=[method], type_ignores=[])
        namespace = {'__name__': PACKAGE+'.flow_test', '__package__': PACKAGE,
                     'vol': vol, 'CONF_ENTRY_TYPE': 'entry_type'}
        exec(compile(ast.fix_missing_locations(tree), '<actual-ocf-flow>', 'exec'), namespace)
        flow = NS(hass=self.hass, async_set_unique_id=AsyncMock(),
            _abort_if_unique_id_configured=Mock(), async_create_entry=Mock(return_value='created'),
            async_show_form=Mock(return_value='form'))
        with patch.object(native, 'read_configuration', AsyncMock(return_value=api.Credentials.parse(json.dumps(config())))), \
             patch.object(settings, 'SettingsClient', return_value=self.coordinator.client):
            result = await namespace['async_step_soundbar_ocf'](flow, {'credential_file': 'credentials.json'})
        self.assertEqual(result, 'created')
        self.assertFalse(self.session.posts)
        saved = flow.async_create_entry.call_args.kwargs['data']
        self.assertEqual(saved, {'entry_type': 'soundbar_ocf', 'credential_file': 'credentials.json'})
        self.assertNotIn('key', saved)


if __name__ == '__main__':
    unittest.main()
