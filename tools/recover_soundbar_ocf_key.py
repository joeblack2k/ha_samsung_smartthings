"""Recover one owned soundbar PSK from an explicitly selected plain iOS backup.

No cloud access, Keychain access, pairing, ownership transfer or setting writes.
Output is a private credential file, never raw keys or device IDs on stdout.
Encrypted backups and unrecognised stores are intentionally unsupported.
"""
from __future__ import annotations

import argparse
import base64
import importlib
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import sys
from types import ModuleType
from uuid import UUID

import cbor2

MAX_STORE = 1024 * 1024
SOURCE = Path(__file__).resolve().parents[1] / 'custom_components/samsung_smartthings'


def load_client_modules():
    """Load protocol code without importing Home Assistant or its setup hooks."""
    name = '_samsung_soundbar_ocf_tool'
    if name not in sys.modules:
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        sys.modules[name] = package
    return tuple(importlib.import_module(name + '.' + module) for module in (
        'soundbar_ocf_api', 'soundbar_ocf_settings', 'soundbar_ocf_discovery'
    ))


def decode(payload):
    if not isinstance(payload, bytes) or len(payload) > MAX_STORE:
        raise ValueError('Store outside supported bounds')
    stream = io.BytesIO(payload)
    value = cbor2.CBORDecoder(stream).decode()
    if stream.read(1):
        raise ValueError('Trailing store data')
    return value


def ocf_uuid(value):
    if isinstance(value, bytes) and len(value) == 16:
        result = UUID(bytes=value)
    elif isinstance(value, str):
        result = UUID(value.removeprefix('urn:uuid:'))
    else:
        raise ValueError('Unsupported OCF identity')
    if not result.int:
        raise ValueError('Empty OCF identity')
    return result


def select_credential(payload, device_id):
    root = decode(payload)
    if not isinstance(root, dict):
        raise ValueError('Unsupported OCF store')

    def section(name):
        value = root.get(name)
        if isinstance(value, bytes):
            value = decode(value)
        return value if isinstance(value, dict) else {}

    cred = section('cred')
    entries = cred.get('creds')
    if not isinstance(entries, list) or len(entries) > 512:
        raise ValueError('Invalid credential section')
    matches = []
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get('credtype')) is not int or not entry['credtype'] & 1:
            continue
        try:
            subject = ocf_uuid(entry.get('subjectuuid'))
        except ValueError:
            continue
        if subject == device_id:
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError('A unique device credential was not found')
    identity = ocf_uuid(cred.get('rowneruuid'))
    doxm = section('doxm')
    if doxm.get('deviceuuid') and ocf_uuid(doxm['deviceuuid']) != identity:
        raise ValueError('Conflicting client identities')
    private = matches[0].get('privatedata')
    if not isinstance(private, dict):
        raise ValueError('Missing private credential data')
    key, encoding = private.get('data'), private.get('encoding')
    if isinstance(key, str) and encoding == 'oic.sec.encoding.base64':
        key = base64.b64decode(key, validate=True)
    elif not isinstance(key, bytes) or encoding not in (None, 'oic.sec.encoding.raw'):
        raise ValueError('Unsupported credential encoding')
    if len(key) not in (16, 32):
        raise ValueError('Unsupported PSK length')
    return identity, key


def read_store(backup):
    """Query only SmartThings' OCF store, never enumerate other apps/files."""
    root = Path(backup).resolve(strict=True)
    def small_plist(name):
        with (root / name).open('rb') as stream:
            payload = stream.read(MAX_STORE + 1)
        if len(payload) > MAX_STORE:
            raise ValueError('Backup metadata outside bounds')
        return plistlib.loads(payload)
    if small_plist('Manifest.plist').get('IsEncrypted') is not False:
        raise ValueError('Only plain local backups are supported')
    if small_plist('Status.plist').get('SnapshotState') != 'finished':
        raise ValueError('Backup is incomplete')
    connection = sqlite3.connect((root / 'Manifest.db').as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        rows = connection.execute(
            'SELECT fileID FROM Files WHERE domain LIKE ? AND '
            '(relativePath = ? OR relativePath LIKE ?) LIMIT 2',
            ('AppDomain%oneconnect4ios%', 'oic_svr_db_client.dat', '%/oic_svr_db_client.dat'),
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not re.fullmatch('[0-9a-f]{40}', rows[0][0]):
        raise ValueError('A unique supported SmartThings store was not found')
    file_id = rows[0][0]
    source = (root / file_id[:2] / file_id).resolve(strict=True)
    if not source.is_relative_to(root):
        raise ValueError('Store outside selected backup')
    with source.open('rb') as stream:
        raw = stream.read(MAX_STORE + 1)
    if not 0 < len(raw) <= MAX_STORE:
        raise ValueError('Store outside supported bounds')
    return raw


def save_private_file(output, contents):
    """Exclusive creation; never overwrite an existing credential file."""
    path = Path(output)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(contents, stream, indent=2)
            stream.write('\n')
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def recover(backup, host, output):
    address = ipaddress.IPv4Address(host)
    if not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
        raise ValueError('Private LAN address required')
    # Check destination before opening the credential store or authenticating.
    if Path(output).exists() or Path(output).is_symlink():
        raise ValueError('Output already exists')
    api, settings, discovery = load_client_modules()
    from smartthings_local.protocol.ocf_discovery import read_plaintext_ocf_resource
    found = discovery.discover(str(address))['plaintext_ports']
    if len(found) != 1:
        raise ValueError('Unique local OCF endpoint unavailable')
    reply = read_plaintext_ocf_resource(str(address), '/oic/sec/doxm',
                                        port=found[0], timeout=3, retries=0)
    if not reply.successful:
        raise ValueError('Public device identity unavailable')
    body = decode(reply.payload)
    device_id = ocf_uuid(body.get('deviceuuid'))
    identity, key = select_credential(read_store(backup), device_id)
    content = {'version': 1, 'host': str(address), 'device_id': str(device_id),
               'identity': str(identity), 'key': base64.b64encode(key).decode('ascii')}
    credentials = api.Credentials.parse(json.dumps(content))
    # The target ID above is not trusted until an authenticated /oic/d agrees.
    client = settings.SettingsClient(credentials)
    client._plain_port = found[0]
    values = client.read()
    save_private_file(output, content)
    return {'status': 'verified_private_file_created', 'transport': 'local_ocf',
            'settings_read': sum(control in values for control in settings.ALL),
            'settings_changed': False, 'pairing_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True, help='Explicit finished local iPhone backup directory')
    parser.add_argument('--host', required=True, help='Owned soundbar LAN IPv4 address')
    parser.add_argument('--output', required=True, help='New private credential file outside the repository')
    args = parser.parse_args()
    # Library debug output is not a credential export interface.
    logging.getLogger('smartthings_local').setLevel(logging.CRITICAL)
    try:
        output = Path(args.output).resolve()
        if output.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError('Private output must be outside the checkout')
        result = recover(args.backup, args.host, args.output)
    except Exception:
        print(json.dumps({'status': 'not_exported', 'error':
              'Backup, matching credential, output path or authenticated local read unavailable. See the guide.'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
