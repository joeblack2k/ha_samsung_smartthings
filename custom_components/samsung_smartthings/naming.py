from __future__ import annotations

import re


_CAPABILITY_NAME_MAP: dict[str, str] = {
    "ocf": "Device Info",
    "switch": "Power",
    "audioVolume": "Volume",
    "audioMute": "Mute",
    "audioNotification": "Notifications",
    "mediaPlayback": "Playback",
    "mediaTrackControl": "Track Control",
    "tvChannel": "TV Channel",
    "custom.picturemode": "Picture Mode",
    "custom.soundmode": "Sound Mode",
    "custom.launchapp": "Apps",
    "samsungvd.mediaInputSource": "Input Source",
    "mediaInputSource": "Input Source",
    "samsungvd.remoteControl": "Remote",
    "samsungvd.ambient": "Ambient/Art",
    "samsungvd.ambient18": "Ambient/Art",
    "samsungvd.thingStatus": "Status",
    "samsungvd.supportsFeatures": "Supported Features",
    "sec.deviceConnectionState": "Connection",
    "sec.wifiConfiguration": "Wi-Fi",
    "sec.diagnosticsInformation": "Diagnostics",
}


_ATTRIBUTE_NAME_MAP: dict[tuple[str, str], str] = {
    ("ocf", "mnmo"): "Model Number",
    ("ocf", "mnfv"): "Firmware Version",
    ("ocf", "mnmn"): "Manufacturer",
    ("ocf", "mnos"): "OS",
    ("samsungvd.thingStatus", "status"): "Status",
    ("samsungvd.mediaInputSource", "inputSource"): "Input Source",
    ("custom.picturemode", "pictureMode"): "Picture Mode",
    ("custom.soundmode", "soundMode"): "Sound Mode",
    ("audioVolume", "volume"): "Volume",
    ("audioMute", "mute"): "Mute",
}


def _split_camel(s: str) -> str:
    # "mobileCamSupported" -> "mobile Cam Supported"
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return s


def humanize_token(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = s.replace("_", " ")
    s = _split_camel(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:1].upper() + s[1:]


def capability_label(capability: str) -> str:
    return _CAPABILITY_NAME_MAP.get(capability, humanize_token(capability.replace(".", " ")))


def attribute_label(capability: str, attribute: str) -> str:
    return _ATTRIBUTE_NAME_MAP.get((capability, attribute), humanize_token(attribute))


def command_label(capability: str, command: str) -> str:
    # Special-case common commands.
    if command == "on":
        return "Turn On"
    if command == "off":
        return "Turn Off"
    if command == "volumeUp":
        return "Volume Up"
    if command == "volumeDown":
        return "Volume Down"
    if command == "mute":
        return "Mute"
    if command == "unmute":
        return "Unmute"
    if command == "setAmbientOn":
        return "Ambient/Art Mode"
    if command == "setNextInputSource":
        return "Next Input Source"
    return humanize_token(command)

