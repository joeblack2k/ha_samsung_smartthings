# Compatibility Matrix

## Overview

Compatibility is split by control path:
- **SmartThings Cloud path** (REST capabilities via `/v1/devices/...`)
- **Local LAN path** (direct device APIs, model/firmware dependent)

## TV matrix

| Feature | SmartThings Cloud TV | Frame TV Local (Art API) |
|---|---|---|
| Device discovery | Yes | No (per-IP setup) |
| Power on/off | Yes | Limited in art API context |
| Volume/mute (if capability exists) | Yes | Not the primary focus of art API |
| App launch | Yes (`custom.launchapp`) | Yes (`run_app`) |
| URL open | Limited (YouTube fallback) | Yes (browser/open + YouTube deep link) |
| Art Mode on/off | Best effort (model/account dependent) | Yes |
| Artwork upload/select/delete | No | Yes |
| Matte/photo filter | No | Yes (if firmware exposes options) |
| Slideshow/motion/brightness sensor settings | No | Yes (if firmware exposes options) |

## Soundbar matrix

| Feature | SmartThings Cloud Soundbar | Soundbar Local (LAN) |
|---|---|---|
| Discovery | Yes | No (per-IP setup) |
| Power | Yes | Yes |
| Volume/mute | Yes | Yes |
| Input source switching | Often inconsistent | Yes (more deterministic) |
| Sound mode | Yes (fallback aliases/candidates) | Yes (validated mode list) |
| Night mode | Execute path (model dependent) | Yes (local advanced call + fallback event) |
| Advanced execute controls | Partial, often limited readback | Partial, method support varies |

## Real-world tested models

## Frame TVs

- `QE65LS03BAUXXN` (The Frame 65): cloud + local tested
- `QE32LS03TBWXXN` (The Frame 32): local path is network/standby sensitive

## Soundbars

- `HW-Q990D`: cloud + local tested
- `Q990C`: community reports confirm cloud input switching can be inconsistent

## Capability-driven support model

This integration is capability-driven:
- If a capability is present and usable, entities/features are enabled.
- If a capability is unreliable (`422`/`409`/missing readback), behavior is hardened with fallbacks.

This is intentional because Samsung behavior differs by model, region, and firmware.

## Why SmartThings app may work when public API does not

Some Samsung app flows rely on internal pathways and state handling that are not fully mirrored by public SmartThings REST behavior. That can cause differences between app UI outcomes and API command outcomes.

