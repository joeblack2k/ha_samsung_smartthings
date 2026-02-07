# Samsung SmartThings (Cloud) for Home Assistant

Samsung soundbars and TVs (including The Frame) via the SmartThings cloud API.

This integration is built for maximum coverage:
- Exposes all reported device attributes as sensors (optional, but enabled by default)
- Creates buttons for no-argument commands (optional, but enabled by default)
- Adds “nice” entities for common Samsung TV / soundbar controls (picture mode, sound mode, input source, remote keys, ambient/art mode, etc.)
- Includes a universal `raw_command` service for anything new/unknown

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
4. Select the device to add

## Services

- `samsung_smartthings.raw_command`: send any capability command (JSON args supported)

## Notes

- Cloud polling means latency and rate limiting can happen, especially on `execute`/OCF models.
- SmartThings TVs often report volume as `0`; this is a SmartThings-side limitation.

