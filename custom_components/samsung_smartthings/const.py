DOMAIN = "samsung_smartthings"

CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_EXPOSE_ALL = "expose_all"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_EXPOSE_ALL = True
DEFAULT_SCAN_INTERVAL = 15  # seconds

# Platforms we create dynamically based on capabilities.
PLATFORMS: list[str] = [
    "media_player",
    "sensor",
    "switch",
    "button",
    "select",
    "number",
    "text",
]

# SmartThings API base
API_BASE = "https://api.smartthings.com/v1"
