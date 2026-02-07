# Samsung SmartThings (Cloud) for Home Assistant

Samsung soundbars and TVs (including The Frame) via the SmartThings cloud API.

This integration is built for maximum coverage, but with sane defaults (no entity spam, avoid SmartThings rate limits):
- Adds “nice” entities for common Samsung TV / soundbar controls (picture mode, sound mode, input source, remote keys, etc.)
- Adds a universal `raw_command` service for anything new/unknown
- Optionally exposes lots of raw SmartThings attributes/controls (disabled/hidden by default)

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
3. Paste a SmartThings Personal Access Token
4. The integration adds a single “hub” entry for that token and auto-discovers all Samsung devices on the account
5. (Optional) Adjust options like polling intervals / “expose all” if you really want everything

## Services

- `samsung_smartthings.raw_command`: send any capability command (JSON args supported)
- `samsung_smartthings.play_track`: audioNotification.playTrack wrapper (soundbars)
- `samsung_smartthings.play_track_and_restore`: audioNotification.playTrackAndRestore wrapper
- `samsung_smartthings.play_track_and_resume`: audioNotification.playTrackAndResume wrapper
- `samsung_smartthings.launch_app`: custom.launchapp.launchApp wrapper (TVs)
- `samsung_smartthings.set_art_mode`: best-effort Art/Ambient mode (Frame TVs; model/account dependent)
- `samsung_smartthings.set_ambient_content`: samsungvd.ambientContent.setAmbientContent wrapper (advanced)

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

### Frame TVs / Art Mode
Frame TV Art/Ambient mode is highly model/account dependent in SmartThings cloud. This integration provides best-effort controls, but if you need reliable Art Mode and power state, a local TV integration is often a better fit.
