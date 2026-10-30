"""Identity-bound settings mapped and write/readback-tested on HW-Q990H."""
from .soundbar_ocf_api import LocalClient, LocalControlError, CONTROLS
import cbor2

P = 'x.com.samsung.networkaudio.'
BASE = '/sec/networkaudio/'
SPEAKERS = {'speaker_center': 'Spk_Center', 'speaker_side': 'Spk_Side',
            'speaker_wide': 'Spk_Wide', 'speaker_front_top': 'Spk_Front_Top',
            'speaker_rear': 'Spk_Rear', 'speaker_rear_top': 'Spk_Rear_Top'}
TOGGLES = {'night_mode': ('advancedaudio', P+'nightmode'),
           'bass_boost': ('advancedaudio', P+'bassboost'),
           'active_voice_amplifier': ('av/soundsetting', 'activevoiceamplifier'),
           'spacefit_sound': ('av/soundsetting', 'spacefitsound')}
LABELS = {'voice_enhancement': 'Voice Enhancement', 'rear_speaker_mode': 'Rear Speaker Mode',
          'night_mode': 'Night Mode', 'bass_boost': 'Bass Boost',
          'active_voice_amplifier': 'Active Voice Amplifier', 'spacefit_sound': 'SpaceFit Sound',
          'woofer_level': 'Woofer Level', 'equalizer_preset': 'Equalizer Preset',
          'bass': 'Bass', 'treble': 'Treble',
          **{key: key.replace('_', ' ').title() for key in SPEAKERS}}
SWITCHES = ('voice_enhancement', *TOGGLES)
SELECTS = ('rear_speaker_mode', 'equalizer_preset')
NUMBERS = ('woofer_level', *SPEAKERS, 'bass', 'treble')
ALL = (*SWITCHES, *SELECTS, *NUMBERS)
PRESETS = ('NONE', 'POP', 'JAZZ', 'CLASSIC', 'CUSTOM')  # CUSTOM selects the saved bank; band editing is separate.


class SettingsClient(LocalClient):
    def _path(self, control):
        if control in CONTROLS: return CONTROLS[control][0]
        if control in TOGGLES: return BASE+TOGGLES[control][0]
        if control in SPEAKERS: return BASE+'channelVolume'
        if control == 'woofer_level': return BASE+'woofer'
        if control == 'equalizer_preset': return BASE+'eq'
        if control in ('bass', 'treble'): return BASE+'tone'
        raise LocalControlError('Unknown local soundbar control')

    def _value(self, body, control):
        if control in CONTROLS:
            actual = body.get(CONTROLS[control][1])
            for label, expected in CONTROLS[control][2].items():
                if type(actual) is type(expected) and actual == expected: return label
        elif control in TOGGLES:
            value = body.get(TOGGLES[control][1])
            if type(value) is int and value in (0, 1): return 'on' if value else 'off'
        elif control in SPEAKERS:
            channels = body.get(P+'channelVolume', [])
            if not isinstance(channels, list):
                raise LocalControlError('Unrecognised local channel data')
            rows = [r for r in channels if isinstance(r, dict)
                    and r.get('name') == SPEAKERS[control] and r.get('status') == 1]
            if len(rows) == 1:
                value = rows[0].get('value')
                if type(value) is int and -6 <= value <= 6: return value
        elif control == 'woofer_level':
            value = body.get(P+'woofer')
            if type(value) is int and -12 <= value <= 6: return value
        elif control in ('bass', 'treble'):
            value = body.get(P+control)
            if type(value) is int and -6 <= value <= 6: return value
        elif control == 'equalizer_preset':
            value = body.get(P+'EQname')
            if value in PRESETS: return value
        raise LocalControlError('Unrecognised local setting value')

    def _read(self, session, control):
        return self._value(self._get(session, self._path(control)), control)

    def _snapshot(self, session):
        bodies = {}
        for path in dict.fromkeys(self._path(c) for c in ALL):
            try:
                bodies[path] = self._get(session, path)
            except LocalControlError:
                # One unavailable model-specific resource must not hide the rest.
                bodies[path] = {}
        values = {}
        # A missing model-specific feature must not hide the other confirmed controls.
        for control in ALL:
            try: values[control] = self._value(bodies[self._path(control)], control)
            except LocalControlError: pass
        eq = bodies[BASE+'eq']
        supported = eq.get(P+'supportedList', [])
        values['equalizer_options'] = [v for v in PRESETS if isinstance(supported, list) and v in supported]
        if not any(control in values for control in ALL):
            raise LocalControlError('No supported local settings returned')
        return values

    def read(self):
        with self._lock:
            session, _ = self._open_identity()
            try: return self._snapshot(session)
            finally: self._close(session)

    def _open_identity(self):
        """Do not require a particular optional feature to open a session."""
        from .soundbar_ocf_api import IdentityMismatch
        for attempt in range(2):
            session = None
            try:
                session = self._factory(self._endpoint())
                session.connect(timeout=4)
                session.start_reader()
                self._identity(session)
                return session, {}
            except Exception as error:
                if session is not None:
                    self._close(session)
                if isinstance(error, IdentityMismatch):
                    raise error from None
                self._plain_port = None
                if attempt:
                    raise LocalControlError('Local soundbar connection unavailable') from None

    def _payload(self, control, value):
        if control in CONTROLS:
            if value not in CONTROLS[control][2]: raise LocalControlError('Invalid local setting')
            return {CONTROLS[control][1]: CONTROLS[control][2][value]}
        if control in TOGGLES:
            if value not in ('on', 'off'): raise LocalControlError('Invalid local setting')
            return {TOGGLES[control][1]: int(value == 'on')}
        if control in NUMBERS:
            minimum = -12 if control == 'woofer_level' else -6
            if type(value) is not int or not minimum <= value <= 6:
                raise LocalControlError('Local level is outside the confirmed integer range')
            if control in ('bass', 'treble'):
                return {P+control: value}
            if control in SPEAKERS:
                return {P+'channelVolume': [{'name': SPEAKERS[control], 'value': value}]}
            return {P+'woofer': value}
        if control == 'equalizer_preset' and value in PRESETS:
            return {P+'EQname': value, P+'action': 'setEQmode'}
        raise LocalControlError('Invalid local setting')

    def apply(self, control, value):
        body = self._payload(control, value)  # Reject unknown/range-invalid writes before connecting.
        with self._lock:
            session, before = self._open(control)
            try:
                if control == 'equalizer_preset' and value not in self._get(session, BASE+'eq').get(P+'supportedList', []):
                    raise LocalControlError('Preset not advertised by this soundbar')
                changed = before[control] != value
                if changed:
                    try:
                        code, _ = session.post(self._path(control).strip('/').split('/'),
                                               cbor2.dumps(body), timeout=4)
                    except Exception: code = None  # Ambiguous outcome: read, never resend.
                    if code is not None and code >> 5 != 2:
                        raise LocalControlError('Local soundbar rejected the setting')
                    for attempt in range(2):
                        try:
                            if self._read(session, control) == value: break
                        except Exception: pass
                        if attempt: raise LocalControlError('Local setting was not confirmed; no command resent')
                        self._sleep(0.5)
                values = self._snapshot(session)
                if values.get(control) != value:
                    raise LocalControlError('Local setting changed during readback')
                return {'accepted': True, 'device_confirmed': True, 'transport': 'local',
                        'value': value, 'changed': changed, 'values': values}
            finally: self._close(session)
