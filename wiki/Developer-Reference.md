# Developer Reference (SmartThings + Local APIs)

This page is intended as a technical reference for maintainers, contributors, and reverse-engineers.

## 1) Integration architecture

Domain package: `custom_components/samsung_smartthings`

Key modules:
- `__init__.py` -> setup, services, coordinators, panel API
- `config_flow.py` -> onboarding for cloud/local entry types
- `device.py` -> SmartThings device helper, execute logic, retries
- `smartthings_api.py` -> SmartThings REST client
- `frame_local_api.py` -> local websocket art client (`samsungtvws` wrapper)
- `soundbar_local_api.py` -> local JSON-RPC client (HTTPS 1516)
- Platform modules (`media_player.py`, `select.py`, `switch.py`, etc.)

Entry types:
- `cloud`
- `soundbar_local`
- `frame_local`

## 2) SmartThings cloud API contract

Base URL:
- `https://api.smartthings.com/v1`

Main endpoints used:
- `GET /devices`
- `GET /devices/{deviceId}`
- `GET /devices/{deviceId}/status`
- `POST /devices/{deviceId}/commands`

Command payload shape:

```json
{
  "commands": [
    {
      "component": "main",
      "capability": "custom.launchapp",
      "command": "launchApp",
      "arguments": ["111299001912", "YouTube"]
    }
  ]
}
```

## 3) Cloud throttling and retry behavior

The integration hardens against common transient statuses:
- `409 Conflict`
- `429 TooManyRequestError`
- `503 Service Unavailable`

Design:
- Per-device command serialization (`asyncio.Lock`)
- Retry/backoff ladder in `send_command`
- Conservative default polling intervals
- Last-known state retained when refresh hits 429

## 4) Execute capability (soundbar)

Many soundbar controls are implemented through `execute`.

Important href routes:
- `/sec/networkaudio/soundmode`
- `/sec/networkaudio/woofer`
- `/sec/networkaudio/eq`
- `/sec/networkaudio/advancedaudio`
- `/sec/networkaudio/channelVolume`
- `/sec/networkaudio/surroundspeaker`
- `/sec/networkaudio/activeVoiceAmplifier`
- `/sec/networkaudio/spacefitSound`

Known issue:
- Some models accept writes but return empty/null execute readback payload.

Mitigation:
- Fallback sound mode candidate sets
- Adaptive alias normalization
- Throttled validation cycles

## 5) Adaptive sound alias handling

To handle firmware differences, write candidates include:
- `adaptive`
- `adaptive_sound`
- `adaptive sound`
- Uppercase variants

## 6) Local soundbar API (1516)

Transport:
- HTTPS JSON-RPC (`https://<host>:1516/`)
- Typically self-signed cert

Token flow:
- `createAccessToken`
- Subsequent methods include `AccessToken`

Core methods:
- `powerControl`
- `remoteKeyControl`
- `inputSelectControl`
- `soundModeControl`
- `setAdvancedSoundSettings`
- `getVolume`, `getMute`, `getCodec`, `getIdentifier`

Night mode strategy:
1. `setAdvancedSoundSettings` with `nightMode`
2. Fallback `ms.channel.emit` payload

## 7) Frame local API

The wrapper uses `samsungtvws` art API and adds reliability behavior.

Port probing strategy:
- Last known active port
- Configured port
- Alternates (`8002`, `8001`)

Key methods:
- `get_api_version`, `get_artmode`, `set_artmode`
- `upload`, `select_image`, `delete`, `delete_list`
- `get_thumbnail_list`, `get_current`
- `get_matte_list`, `change_matte`
- `get_photo_filter_list`, `set_photo_filter`
- `get_slideshow_status`, `set_slideshow_status`
- Motion + brightness-sensor setters
- TV-level app methods: `app_list`, `run_app`, `open_browser`

Error heuristics:
- `-1`: unsupported operation for current model/firmware
- `-9`: invalid slideshow category

## 8) App launch implementation

Cloud TV:
- Capability: `custom.launchapp`
- Entity: `select.<tv>_app`
- Media behavior: `media_player.play_media` with `app` type and YouTube URL fallback

Frame local:
- Entity: `select.frame_tv_<ip>_app`
- Media behavior:
  - `app` -> `run_app`
  - `url` -> YouTube deep-link or browser open

App catalog:
- Curated list of common Tizen app IDs
- Resolver accepts app ID, label, or `app:<id>` format

## 9) Home Assistant service contracts

Main service groups:
- Generic cloud: `raw_command`, `launch_app`, `set_art_mode`, `set_ambient_content`
- Soundbar local: `set_night_mode`
- Frame lifecycle: upload/select/delete/sync
- Frame advanced: slideshow/motion/brightness sensor
- Frame content helpers: local file, internet collections, favorites

Use `services.yaml` as the source of truth for service schemas.

## 10) Observability and debugging

Minimal debug workflow:
1. `ha core check` / `ha_check_config`
2. Restart or targeted reload
3. Trigger service call
4. Inspect `home-assistant.log`

Notes:
- Not all HA errors are caused by this integration.
- Filter logs by `samsung_smartthings` first for signal over noise.

## 11) Security and auth notes

- PAT tokens can be temporary (often ~24h)
- For long-term setups: use HA SmartThings login reuse or OAuth2 app flow
- Local APIs provide strong LAN control; limit network exposure accordingly

## 12) Contributor guidelines

- Prefer capability-driven behavior over hardcoded model-only logic
- Add fallbacks for inconsistent readback behavior
- Default noisy/fragile entities to diagnostic hidden/disabled
- Retry only known transient status classes
- Provide automation examples for every new user-facing feature

