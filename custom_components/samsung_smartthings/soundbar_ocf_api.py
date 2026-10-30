"""Identity-bound OCF transport; no ownership or cloud API operations."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import io
import json
import threading
import time
from uuid import UUID
from pathlib import Path

import cbor2

CONTROLS = {
    'voice_enhancement': ('/sec/networkaudio/advancedaudio',
        'x.com.samsung.networkaudio.voiceamplifier', {'off': 0, 'on': 1}),
    'rear_speaker_mode': ('/sec/networkaudio/surroundspeaker',
        'x.com.samsung.networkaudio.currentRearPosition', {'Front': 'Front', 'Rear': 'Rear'}),
}


class LocalControlError(Exception):
    """Only fixed, non-secret messages may leave the transport boundary."""


class IdentityMismatch(LocalControlError):
    """Never reconnect/retry against a peer with an unexpected identity."""


@dataclass(frozen=True, repr=False)
class Credentials:
    host: str
    device_id: UUID
    identity: bytes
    key: bytes

    @classmethod
    def parse(cls, raw):
        try:
            if len(raw) > 4096:
                raise ValueError()
            data = json.loads(raw)
            if type(data['version']) is not int or data['version'] != 1:
                raise ValueError()
            address = ipaddress.IPv4Address(data['host'])
            if not address.is_private or address.is_multicast or address.is_unspecified or address.is_loopback:
                raise ValueError()
            device = UUID(data['device_id'])
            identity = UUID(data['identity'])
            key = base64.b64decode(data['key'], validate=True)
            if not device.int or not identity.int or len(key) not in (16, 32):
                raise ValueError()
            return cls(str(address), device, identity.bytes, key)
        except Exception:
            raise LocalControlError('Invalid local credential configuration') from None


def load_credentials(directory, filename):
    """Only read a bounded regular file within the integration's private folder."""
    try:
        if not isinstance(filename, str) or not filename.endswith('.json'):
            raise ValueError()
        if Path(filename).name != filename or '/' in filename or '\\' in filename:
            raise ValueError()
        root = Path(directory).resolve(strict=True)
        path = (root / filename).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError()
        with path.open('rb') as stream:
            return Credentials.parse(stream.read(4097))
    except Exception:
        raise LocalControlError('Local credential file unavailable or invalid') from None


def decode(payload):
    if not isinstance(payload, bytes) or len(payload) > 65536:
        raise LocalControlError('Invalid local response')
    try:
        stream = io.BytesIO(payload)
        # Core currently ships cbor2 5.9; avoid newer constructor-only options.
        # Responses are size bounded, authenticated and schema checked below.
        result = cbor2.CBORDecoder(stream).decode()
        if stream.read(1) or not isinstance(result, dict):
            raise ValueError()
        return result
    except Exception:
        raise LocalControlError('Invalid local response') from None


class LocalClient:
    """Serialize sessions, rediscover dynamic ports, and verify every write.

    A new authenticated session is used per transaction and always closed.
    Only connection/pre-write reads can retry. An ambiguous POST is never
    resent; GET readback may establish that it already took effect.
    """

    def __init__(self, credentials, *, endpoint=None, session_factory=None, sleep=time.sleep):
        self._credentials = credentials
        self._endpoint = endpoint or self._discover
        self._factory = session_factory or self._session
        self._sleep = sleep
        self._lock = threading.Lock()
        self._plain_port = None

    def _discover(self):
        from smartthings_local.protocol.ocf_discovery import discover_ocf_secure_ports
        from .soundbar_ocf_discovery import discover
        # Cached public endpoint first; never scan arbitrary ports.
        if self._plain_port:
            found = discover_ocf_secure_ports(self._credentials.host,
                discovery_port=self._plain_port, timeout=3, retries=0).ports
            if len(found) == 1:
                return found[0]
        self._plain_port = None
        candidates = discover(self._credentials.host)['plaintext_ports']
        if len(candidates) != 1:
            raise LocalControlError('Local endpoint discovery failed')
        self._plain_port = candidates[0]
        ports = discover_ocf_secure_ports(self._credentials.host,
            discovery_port=self._plain_port, timeout=3, retries=0).ports
        if len(ports) != 1:
            raise LocalControlError('Local endpoint discovery failed')
        return ports[0]

    def _session(self, port):
        from smartthings_local.protocol.auth import PskAuth
        from smartthings_local.protocol.dtls_session import DtlsCoapSession
        return DtlsCoapSession(self._credentials.host, port,
            auth=PskAuth(identity=self._credentials.identity, key=self._credentials.key),
            rate_limit_rps=2, write_max_attempts=1)

    def _get(self, session, path):
        code, payload = session.get(path.strip('/').split('/'), timeout=3)
        if code >> 5 != 2:
            raise LocalControlError('Local read rejected')
        return decode(payload)

    def _identity(self, session):
        body = self._get(session, '/oic/d')
        try:
            actual = UUID(body['di'].removeprefix('urn:uuid:'))
        except Exception:
            raise IdentityMismatch('Authenticated device identity check failed') from None
        if actual != self._credentials.device_id:
            raise IdentityMismatch('Authenticated device identity check failed')

    def _read(self, session, control):
        path, field, choices = CONTROLS[control]
        actual = self._get(session, path).get(field)
        for label, expected in choices.items():
            if type(actual) is type(expected) and actual == expected:
                return label
        raise LocalControlError('Unrecognised local setting value')

    def _open(self, control=None):
        for attempt in range(2):
            session = None
            try:
                session = self._factory(self._endpoint())
                session.connect(timeout=4)
                session.start_reader()
                self._identity(session)
                values = {name: self._read(session, name)
                          for name in ([control] if control else CONTROLS)}
                return session, values
            except Exception as error:
                if session is not None:
                    self._close(session)
                # Identity failures must never prompt a second authentication.
                if isinstance(error, IdentityMismatch):
                    raise error from None
                self._plain_port = None
                if attempt:
                    raise LocalControlError('Local soundbar connection unavailable') from None
        raise LocalControlError('Local soundbar connection unavailable')

    @staticmethod
    def _close(session):
        try:
            session.close()
        except Exception:
            pass

    def read(self):
        with self._lock:
            session, values = self._open()
            try:
                return values
            finally:
                self._close(session)

    def apply(self, control, value):
        if control not in CONTROLS or value not in CONTROLS[control][2]:
            raise LocalControlError('Invalid local control value')
        with self._lock:
            session, before = self._open(control)
            try:
                changed = before[control] != value
                if changed:
                    path, field, choices = CONTROLS[control]
                    try:
                        code, _ = session.post(path.strip('/').split('/'),
                            cbor2.dumps({field: choices[value]}), timeout=4)
                    except Exception:
                        code = None  # Unknown outcome: read, never resend.
                    if code is not None and code >> 5 != 2:
                        raise LocalControlError('Local soundbar rejected the setting')
                    for attempt in range(2):
                        try:
                            if self._read(session, control) == value:
                                break
                        except Exception:
                            pass
                        if attempt:
                            raise LocalControlError('Local setting was not confirmed; no command resent')
                        self._sleep(0.5)
                return {'accepted': True, 'device_confirmed': True,
                        'transport': 'local', 'value': value, 'changed': changed}
            finally:
                self._close(session)
