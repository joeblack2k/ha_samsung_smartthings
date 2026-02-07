DOMAIN = "samsung_smartthings"

CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_IDS = "device_ids"
CONF_DEVICE_NAME = "device_name"
CONF_EXPOSE_ALL = "expose_all"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ADD_ALL = "add_all"

DEFAULT_EXPOSE_ALL = True
DEFAULT_SCAN_INTERVAL = 15  # seconds

# Platforms we create dynamically based on capabilities.
PLATFORMS: list[str] = [
    "media_player",
    "sensor",
    "switch",
    "button",
    "remote",
    "select",
    "number",
    "text",
]

# SmartThings API base
API_BASE = "https://api.smartthings.com/v1"


# -- Execute-based soundbar constants --

from enum import Enum


class SpeakerIdentifier(Enum):
    CENTER = "Spk_Center"
    SIDE = "Spk_Side"
    WIDE = "Spk_Wide"
    FRONT_TOP = "Spk_Front_Top"
    REAR = "Spk_Rear"
    REAR_TOP = "Spk_Rear_Top"


class RearSpeakerMode(Enum):
    FRONT = "Front"
    REAR = "Rear"


# Execute href paths for soundbar OCF features.
EXECUTE_SOUNDMODE = "/sec/networkaudio/soundmode"
EXECUTE_WOOFER = "/sec/networkaudio/woofer"
EXECUTE_EQ = "/sec/networkaudio/eq"
EXECUTE_ADVANCED_AUDIO = "/sec/networkaudio/advancedaudio"
EXECUTE_CHANNEL_VOLUME = "/sec/networkaudio/channelVolume"
EXECUTE_SURROUND_SPEAKER = "/sec/networkaudio/surroundspeaker"
EXECUTE_AVA = "/sec/networkaudio/activeVoiceAmplifier"
EXECUTE_SPACE_FIT = "/sec/networkaudio/spacefitSound"
