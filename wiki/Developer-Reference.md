# Developer Reference (SmartThings + Local APIs)

Deze pagina is bedoeld als **technische hoofdbron** voor maintainers, contributors en reverse-engineers.

## 1. Integratie Architectuur

Repo domein: `custom_components/samsung_smartthings`

Belangrijke modules:
- `__init__.py` -> setup, services, coordinators, panel API
- `config_flow.py` -> onboarding van cloud/local entry types
- `device.py` -> SmartThings device helper, execute logica, retries
- `smartthings_api.py` -> REST client voor SmartThings
- `frame_local_api.py` -> local websocket art client (`samsungtvws` wrapper)
- `soundbar_local_api.py` -> local JSON-RPC client (HTTPS 1516)
- platform modules (`media_player.py`, `select.py`, `switch.py`, etc.)

## Entry types

- `cloud`
- `soundbar_local`
- `frame_local`

## 2. SmartThings Cloud API contract

Base URL:
- `https://api.smartthings.com/v1`

Gebruik:
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

## 3. Cloud throttling en retries

De integratie behandelt veelvoorkomende statuses defensief:
- `409 Conflict`
- `429 TooManyRequestError`
- `503 Service unavailable`

Gedrag:
- Per-device command lock (`asyncio.Lock`)
- Retry/backoff ladder voor send_command
- Polling niet te agressief (defaults conservatief)
- Last-known state behouden bij 429 tijdens refresh

## 4. Execute capability (soundbar)

Veel soundbar functies lopen via capability `execute`.

Belangrijke href routes:
- `/sec/networkaudio/soundmode`
- `/sec/networkaudio/woofer`
- `/sec/networkaudio/eq`
- `/sec/networkaudio/advancedaudio`
- `/sec/networkaudio/channelVolume`
- `/sec/networkaudio/surroundspeaker`
- `/sec/networkaudio/activeVoiceAmplifier`
- `/sec/networkaudio/spacefitSound`

Known issue:
- Sommige modellen accepteren write calls maar geven lege/null payload terug in status readback.

Mitigatie in integratie:
- fallback sound mode candidate sets
- alias-normalisatie voor adaptive mode
- mode validation throttling

## 5. Adaptive mode aliasing

Omdat modellen verschillende waarden accepteren:
- `adaptive`
- `adaptive_sound`
- `adaptive sound`
- uppercase varianten

Write-path probeert meerdere aliases in volgorde totdat een geldige variant geaccepteerd wordt.

## 6. Local Soundbar API (1516)

Transport:
- HTTPS JSON-RPC (`https://<host>:1516/`)
- meestal self-signed cert

Token flow:
- `createAccessToken`
- token in verdere method calls als `AccessToken`

Core methods:
- `powerControl`
- `remoteKeyControl`
- `inputSelectControl`
- `soundModeControl`
- `setAdvancedSoundSettings`
- `getVolume`, `getMute`, `getCodec`, `getIdentifier`

Night mode strategy:
1. `setAdvancedSoundSettings({nightMode})`
2. fallback `ms.channel.emit` event payload

## 7. Frame Local API

Wrapper gebruikt `samsungtvws` art API.

Port strategy:
- preferred active port
- configured port
- alternates (`8002`, `8001`)

Methoden:
- `get_api_version`, `get_artmode`, `set_artmode`
- `upload`, `select_image`, `delete`, `delete_list`
- `get_thumbnail_list`, `get_current`
- `get_matte_list`, `change_matte`
- `get_photo_filter_list`, `set_photo_filter`
- `get_slideshow_status`, `set_slideshow_status`
- motion + brightness sensor setters
- tv-level: `app_list`, `run_app`, `open_browser`

Foutcode heuristieken:
- `-1`: unsupported operation op model/firmware
- `-9`: invalid slideshow category

## 8. App Launch implementatie

Cloud TV:
- capability `custom.launchapp`
- entity: `select.<tv>_app`
- media path: `media_player.play_media` (`app` type, plus YouTube URL fallback)

Frame local:
- `select.frame_tv_<ip>_app`
- `media_player.play_media` met:
  - `app` -> `run_app`
  - `url` -> YouTube deep-link of browser open

Catalogus:
- gecureerde app list met bekende Tizen app IDs
- resolutie op `app_id`, label, en `app:<id>` notatie

## 9. Home Assistant servicecontracten

Belangrijkste servicegroepen:
- Generic cloud: `raw_command`, `launch_app`, `set_art_mode`, `set_ambient_content`
- Soundbar local: `set_night_mode`
- Frame local lifecycle: upload/select/delete/sync
- Frame local advanced: slideshow/motion/brightness sensor
- Frame content helpers: local file, internet collections, favorites

Controleer `services.yaml` voor actuele field schemas.

## 10. Observability en debugging

Minimale debug workflow:
1. `ha core check` / `ha_check_config`
2. restart of gerichte reload
3. trigger service call
4. lees `home-assistant.log`

Let op:
- Niet alle fouten in HA komen van deze integratie.
- Filter logs specifiek op `samsung_smartthings` voor ruisvrije analyse.

## 11. Veiligheid en auth notes

- PAT tokens kunnen tijdelijk zijn (vaak 24u)
- Voor productiesetup: HA SmartThings login of OAuth2 app flow
- Local APIs draaien LAN-only maar geven sterke device-control; beperk netwerktoegang waar mogelijk

## 12. Ontwikkelrichtlijnen voor contributors

- Capability-gedreven bouwen, geen model hardcoding als enige check
- Fallbacks toevoegen bij inconsistent readback gedrag
- Nieuwe entities met high-noise/high-failure kans standaard diagnostisch hidden/disabled
- Retrying alleen op bekende transient statussen
- Maak automation-voorbeelden voor elke nieuwe user-facing functie

