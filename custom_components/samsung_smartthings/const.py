DOMAIN = "samsung_smartthings"

CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_IDS = "device_ids"
CONF_DEVICE_NAME = "device_name"
CONF_EXPOSE_ALL = "expose_all"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ADD_ALL = "add_all"
CONF_INCLUDE_NON_SAMSUNG = "include_non_samsung"
CONF_DISCOVERY_INTERVAL = "discovery_interval"
CONF_MANAGE_DIAGNOSTICS = "manage_diagnostics"

# Default to a clean, reliable setup. SmartThings cloud rate-limits aggressively
# (429) and many Samsung devices expose a very large capability surface.
DEFAULT_EXPOSE_ALL = False
# Polling faster than ~30s tends to trigger 429 when multiple devices/integrations
# share the same SmartThings account. Users can lower this in options.
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_INCLUDE_NON_SAMSUNG = False
# Separate from state polling. This is how often we re-list devices and reload
# the entry if new devices appear.
DEFAULT_DISCOVERY_INTERVAL = 3600  # seconds
DEFAULT_MANAGE_DIAGNOSTICS = True

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
