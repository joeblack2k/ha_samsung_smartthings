# Samsung SmartThings (Cloud + Local) for Home Assistant

Samsung soundbars and TVs (including The Frame) via the SmartThings cloud API.
This project now combines cloud control with local LAN control paths for deeper reliability and feature coverage.

![Integration Logo](custom_components/samsung_smartthings/logo.png)

This integration is built for maximum coverage, but with sane defaults (no entity spam, avoid SmartThings rate limits):
- Adds “nice” entities for common Samsung TV / soundbar controls (picture mode, sound mode, input source, remote keys, etc.)
- Adds a universal `raw_command` service for anything new/unknown
- Optionally exposes lots of raw SmartThings attributes/controls (disabled/hidden by default)

## Full Documentation Wiki

For complete documentation (user + developer), see the wiki pages in this repository:

- [Wiki Home](wiki/Home.md)
- [Quick Start & Login Flows](wiki/Quick-Start-and-Login-Flows.md)
- [Compatibility Matrix](wiki/Compatibility-Matrix.md)
- [Frame TV Deep Dive](wiki/FrameTV-Deep-Dive.md)
- [Soundbar Deep Dive](wiki/Soundbar-Deep-Dive.md)
- [Automation Recipes](wiki/Automation-Recipes.md)
- [Developer Reference](wiki/Developer-Reference.md)
- [Credits & References](wiki/Credits-and-References.md)

## Install (HACS)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=joeblack2k&repository=ha_samsung_smartthings&category=integration)

1. Open HACS
2. Add this repository as a custom repository (category: Integration)
3. Install
4. Restart Home Assistant

## Install (Manual)

Copy `custom_components/samsung_smartthings` into your Home Assistant `custom_components/`.

## Setup

1. Home Assistant: Settings -> Devices & services -> Add integration
2. Search for `Samsung SmartThings (Cloud)`
3. Choose the setup type:
   - `SmartThings Cloud (Use Home Assistant SmartThings login, recommended)` (easy, permanent login)
   - `SmartThings Cloud (OAuth2, bring your own app)` (developer/advanced)
   - `SmartThings Cloud (PAT token)` (temporary; SmartThings PATs may expire in 24h)
   - `Soundbar Local (LAN)` (optional, for supported 2024 Wi-Fi soundbars)
   - `Frame TV Local (LAN, Art API)` (recommended for reliable Frame Art functions)
4. Home Assistant SmartThings login: this reuses the built-in Home Assistant `SmartThings` integration's OAuth login. If you don't have it yet, add the built-in SmartThings integration first (it will open SmartThings in your browser and return to HA).
5. OAuth2 (bring your own app): create Application Credentials for SmartThings (client id/secret + redirect URL), then complete the login flow
6. PAT: paste a SmartThings Personal Access Token
7. Cloud mode: the integration adds a single “hub” entry and auto-discovers all Samsung devices on the account
8. (Optional) Adjust options like polling intervals / “expose all” if you really want everything

## Soundbar Support (Cloud vs LAN)

This integration supports Samsung soundbars through two paths:

- `SmartThings Cloud` (works for many models, broad compatibility, but API is inconsistent/rate-limited)
- `Soundbar Local (LAN)` (best control quality, currently for 2024 Wi-Fi soundbars with IP control enabled)

### Quick recommendation

- Use `Soundbar Local (LAN)` whenever your model supports it and you can enable IP control.
- Keep `SmartThings Cloud` enabled for discovery/account-level convenience and extra metadata.

## TV App Launch (Cloud + Frame Local)

This integration now exposes app launch controls in multiple ways:

- `select.<tv>_app` dropdown (readable app names + app IDs)
- `media_player.play_media` with:
  - `media_content_type: "app"` and `media_content_id: "app:<app_id>"` (or known app label)
  - `media_content_type: "url"` and YouTube URL (best effort per path)
- `samsung_smartthings.launch_app` service (direct control)

Supported common app IDs include: YouTube, Netflix, Disney+, Prime Video, Apple TV, Spotify, Plex, Web Browser.

### Cloud TV behavior

- App launch works via SmartThings `custom.launchapp`.
- YouTube URL via `play_media` opens YouTube app (deep-link not guaranteed by SmartThings).
- Arbitrary web URL deep-link is not reliably supported by SmartThings cloud.

### Frame Local behavior

- App launch uses local websocket (`run_app`) directly on the TV.
- `play_media` with YouTube URL uses app deep-link.
- `play_media` with other URLs opens TV browser (`org.tizen.browser`).

## Frame TV Support (Cloud vs Local)

For The Frame, this integration now supports two paths:

- `SmartThings Cloud` (easy auth reuse, broad model discovery, but command reliability depends on SmartThings)
- `Frame TV Local (LAN, Art API)` (direct websocket API on TV; best for Art Mode and artwork lifecycle)

### Frame feature matrix

| Feature | SmartThings Cloud | Frame TV Local (LAN) |
|---|---|---|
| TV discovery and account-level onboarding | Yes | No (per-IP local entry) |
| Art Mode switch | Best-effort, model/account dependent | Yes |
| Current artwork id | Limited/inconsistent | Yes |
| Artwork list | No | Yes |
| Upload artwork | No | Yes |
| Delete artwork | No | Yes |
| Matte / photo filter | No | Yes (if TV firmware exposes options) |
| Slideshow / motion / brightness sensor settings | No | Yes (advanced services) |

### Frame Local setup

1. Add integration -> choose `Frame TV Local (LAN, Art API)`
2. Enter Frame IP
3. Keep websocket port `8002` unless your network requires `8001`
4. First connection triggers a permission popup on TV, accept it
5. Token is stored in Home Assistant `.storage` and reused automatically

### Capability matrix

| Feature | SmartThings Cloud | Soundbar Local (LAN) |
|---|---|---|
| Power on/off | Yes | Yes |
| Volume up/down/set | Yes | Yes |
| Mute | Yes | Yes |
| Input source select | Often unreliable on many soundbars (model/firmware dependent) | Reliable (HDMI/eARC/etc) |
| Next input source | Yes (`setNextInputSource`) | Yes (direct input select) |
| Sound mode select | Yes, with fallback aliases and model candidates | Yes |
| Adaptive mode variants | `adaptive`, `adaptive_sound`, `adaptive sound` aliases supported | Candidate includes `ADAPTIVE SOUND` |
| Night mode | Cloud execute path (availability depends on device payload support) | Yes (local advanced call + app-style fallback) |
| Bass boost / voice amplifier / spacefit / AVA | Cloud execute path (often no readback) | Not fully standardized locally yet |
| Woofer/channel level controls | Cloud execute path | Subwoofer +/- buttons available |
| Art/TV controls | Cloud only (TV capabilities) | N/A (soundbar path) |

### What we expose for soundbars today

#### Cloud soundbar entities

- `media_player`: power/volume/mute + URL playback via SmartThings `audioNotification` (works with Music Assistant)
- `switch`: power
- `number`: volume slider
- `button`: next input source
- `sensor`: model/firmware/status/input and selected useful attributes
- Advanced execute entities (night/bass/voice/spacefit/woofer/speaker levels/sound mode):
  - created as diagnostic entities
  - hidden/disabled by default (to avoid noisy/broken controls on models that do not support readback)
  - can be manually enabled

#### Local soundbar entities

- `media_player.soundbar_<ip>`: power, volume, mute, input source, sound mode
- `switch`: power, mute
- `select`: input source, sound mode
- `sensor`: codec, identifier
- Diagnostic controls (hidden/disabled by default):
  - `switch`: Night Mode
  - `button`: Subwoofer +, Subwoofer -

### Cloud sound mode behavior (important)

SmartThings frequently returns empty/null execute payloads for sound mode capabilities, even when commands are accepted.
To keep the UI usable, this integration:

- Seeds sound mode fallback candidates when payloads are empty or rate-limited
- Supports adaptive aliases: `adaptive`, `adaptive_sound`, `adaptive sound`
- Lets you configure custom cloud candidates in options via `cloud_soundmodes` (comma-separated)

This means sound mode controls stay available even when SmartThings metadata is incomplete.

### SmartThings Cloud (Use Home Assistant SmartThings login, recommended)

This is the easiest setup for normal users. No SmartThings CLI, no developer app registration, no client secret handling.

Steps:
1. Add the built-in **SmartThings** integration in Home Assistant (if you haven't already)
2. Add **Samsung SmartThings (Cloud)** (this integration)
3. Choose `SmartThings Cloud (Use Home Assistant SmartThings login, recommended)`
4. Select the SmartThings account/location you want to reuse

### SmartThings Cloud OAuth2 (Bring Your Own App, advanced)

SmartThings Personal Access Tokens may be short-lived (often 24 hours). For a permanent setup, use OAuth2.

High-level steps:
1. Create a SmartThings API app with OAuth in the SmartThings Developer Workspace
2. Add Home Assistant's OAuth redirect URL to that app (typically `<your_ha_base_url>/auth/external/callback`)
3. In Home Assistant, create **Application Credentials** for SmartThings (client id/secret)
4. Add the integration and choose `SmartThings Cloud (OAuth2, bring your own app)`

### Soundbar Local (LAN) Setup (2024 Wi-Fi soundbars)

This mode talks directly to the soundbar on your network (HTTPS JSON-RPC on port `1516`, typically a self-signed certificate).

Requirements:
- Supported model family: 2024-line Samsung Wi-Fi soundbars (e.g. `HW-Q990D`, `HW-Q930D`, etc.)
- Soundbar connected to Wi-Fi and added to the SmartThings app
- SmartThings app: enable **IP control** in the soundbar device settings

Steps:
1. Enable **IP control** in SmartThings for the soundbar
2. Find the soundbar IP address (from your router/DHCP list or LAN discovery)
3. In Home Assistant add integration -> choose `Soundbar Local (LAN)` -> enter the IP
4. Leave `verify_ssl` off unless you installed a trusted certificate on the device

## Services

- `samsung_smartthings.raw_command`: send any capability command (JSON args supported)
- `samsung_smartthings.play_track`: audioNotification.playTrack wrapper (soundbars)
- `samsung_smartthings.play_track_and_restore`: audioNotification.playTrackAndRestore wrapper
- `samsung_smartthings.play_track_and_resume`: audioNotification.playTrackAndResume wrapper
- `samsung_smartthings.launch_app`: custom.launchapp.launchApp wrapper (TVs)
- `samsung_smartthings.set_art_mode`: best-effort Art/Ambient mode (Frame TVs; model/account dependent)
- `samsung_smartthings.set_ambient_content`: samsungvd.ambientContent.setAmbientContent wrapper (advanced)
- `samsung_smartthings.set_night_mode`: local soundbar night mode service (`entity_id` + boolean `night`)
- `samsung_smartthings.frame_upload_artwork`: upload local image and optionally show now (Frame Local)
- `samsung_smartthings.frame_select_artwork`: select existing artwork by `content_id` (Frame Local)
- `samsung_smartthings.frame_delete_artwork`: delete one artwork by `content_id` (Frame Local)
- `samsung_smartthings.frame_delete_artwork_list`: delete multiple artworks by `content_ids` (Frame Local)
- `samsung_smartthings.frame_sync_folder`: folder sync with hash/mtime dedup + optional orphan cleanup (Frame Local)
- `samsung_smartthings.frame_set_slideshow`: configure slideshow duration/shuffle (Frame Local)
- `samsung_smartthings.frame_set_motion_timer`: set motion timer (Frame Local)
- `samsung_smartthings.frame_set_motion_sensitivity`: set motion sensitivity (Frame Local)
- `samsung_smartthings.frame_set_brightness_sensor`: enable/disable brightness sensor (Frame Local)
- `samsung_smartthings.frame_set_local_file`: upload/show one local file (absolute or relative to `FrameTV` folder)
- `samsung_smartthings.frame_set_internet_artwork`: set artwork from internet collection (`museums|nature|architecture`), random or fixed index
- `samsung_smartthings.frame_set_favorite_artwork`: set artwork from favorites by id or random favorite

## Automation Examples (TV App Launch)

### Launch YouTube app by app id

```yaml
alias: Frame TV - Launch YouTube app
trigger:
  - platform: state
    entity_id: input_boolean.start_video
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.samsung_the_frame_65_media
    data:
      media_content_id: "app:111299001912"
      media_content_type: "app"
```

### Open specific YouTube video URL

```yaml
alias: Frame TV - Open YouTube URL
trigger:
  - platform: state
    entity_id: input_boolean.start_video
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.frame_tv_192_168_2_172_art_browser
    data:
      media_content_id: "https://www.youtube.com/watch?v=MNkDPfjr0E8"
      media_content_type: "url"
```

### Open YouTube playlist URL

```yaml
alias: Frame TV - Open YouTube playlist
trigger:
  - platform: state
    entity_id: input_boolean.start_playlist
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.frame_tv_192_168_2_172_art_browser
    data:
      media_content_id: "https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxx"
      media_content_type: "url"
```

## Notes

### SmartThings Rate Limits (HTTP 429)
SmartThings cloud rate-limits aggressively. You may see errors like:
- `429 Too Many Requests`
- `retry in XXXX millis`

What helps:
- Keep polling intervals reasonable (default is intentionally conservative)
- Avoid hammering buttons/selects rapidly
- If you run multiple SmartThings-based integrations on the same account, you will hit limits sooner

This integration tries to be resilient:
- Serializes requests per token to avoid bursts
- Backs off on 429 during polling and keeps last-known state

### Soundbar Input Source Limitations (eARC / D.IN)
Many Samsung soundbars expose `samsungvd.audioInputSource`, but via the SmartThings API it often only supports **cycling** (`setNextInputSource`).

On some models/firmware (notably Q990-series), switching away from `D.IN` (TV ARC/eARC) may not work reliably via the SmartThings REST API even though it works in the SmartThings mobile app.

Workarounds:
- Use the physical remote (most reliable)
- Use HDMI-CEC / TV integration to change soundbar source
- Use an IR blaster if you want deterministic source switching from Home Assistant

### Soundbar Local (LAN) for 2024 Wi-Fi soundbars (input switching)
For supported 2024-line Wi-Fi soundbars, `Soundbar Local (LAN)` uses the soundbar's local JSON-RPC API (HTTPS port `1516`, self-signed cert) and supports reliable input switching (HDMI1/eARC/etc).

Requirements:
- Soundbar connected via Wi-Fi and added to the SmartThings app
- SmartThings app: enable **IP control** for the soundbar

### Soundbar Local (LAN) advanced notes

- Local mode uses HTTPS JSON-RPC on port `1516` and typically a self-signed certificate.
- `verify_ssl` should generally remain disabled unless you installed a trusted cert on the device.
- Night Mode is implemented with multiple local methods to handle firmware differences.
- Some advanced Samsung options are not consistently documented across models/firmware; when in doubt use `raw_command` or local diagnostics entities.

### Frame TVs / Art Mode
Frame TV Art/Ambient mode is highly model/account dependent in SmartThings cloud.  
For reliable Art control use `Frame TV Local (LAN, Art API)` in this integration.

## Automation Examples (Frame Local)

### 1) First day of month: random museum/nature/architecture artwork

```yaml
alias: FrameTV monthly random internet artwork
trigger:
  - platform: time
    at: "09:00:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
action:
  - service: samsung_smartthings.frame_set_internet_artwork
    data:
      frame_entity_id: media_player.frame_tv_192_168_2_172_art_browser
      collection: museums   # or nature / architecture
      random: true
      use_border: true
mode: single
```

### 2) First day of month: fixed local file from FrameTV folder

```yaml
alias: FrameTV monthly fixed local artwork
trigger:
  - platform: time
    at: "09:00:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
action:
  - service: samsung_smartthings.frame_set_local_file
    data:
      frame_entity_id: media_player.frame_tv_192_168_2_172_art_browser
      path: foto.jpg        # relative to /config/FrameTV
      use_border: false
mode: single
```
