# Soundbar Deep Dive

## Architecture

There are two soundbar paths:

1. **SmartThings Cloud soundbar** (capabilities + execute)
2. **Soundbar Local LAN** (JSON-RPC over HTTPS on port 1516)

## Cloud soundbar realities

SmartThings cloud can be inconsistent on some firmware:
- Commands may return `200` while state does not change
- `execute` readback payload is often empty/null
- Input source switching can fail despite capability presence
- Rate limits (`429`) occur quickly with aggressive polling/command bursts

Mitigations in this integration:
- Per-device command serialization
- Retry/backoff for transient cloud failures
- Fallback sound mode candidates and alias handling
- Diagnostic entities hidden/disabled by default

## Execute endpoints (cloud)

Important execute routes:
- `/sec/networkaudio/soundmode`
- `/sec/networkaudio/woofer`
- `/sec/networkaudio/eq`
- `/sec/networkaudio/advancedaudio`
- `/sec/networkaudio/channelVolume`
- `/sec/networkaudio/surroundspeaker`
- `/sec/networkaudio/activeVoiceAmplifier`
- `/sec/networkaudio/spacefitSound`

## Sound mode alias strategy

Adaptive variants are handled with multiple write aliases:
- `adaptive`
- `adaptive_sound`
- `adaptive sound`
- Uppercase variants

This avoids model/firmware mismatches seen on Q-series devices.

## Local soundbar API (port 1516)

Transport:
- HTTPS POST JSON-RPC (`https://<host>:1516/`)
- Self-signed cert in most home setups

Token/auth flow:
- `createAccessToken`
- Token is passed as `AccessToken` for subsequent methods

Core methods:
- `powerControl`
- `remoteKeyControl`
- `inputSelectControl`
- `soundModeControl`
- `setAdvancedSoundSettings`
- getters: `getVolume`, `getMute`, `getCodec`, `getIdentifier`

## Night mode local strategy

Night mode tries:
1. `setAdvancedSoundSettings({nightMode})`
2. Fallback app-style event (`ms.channel.emit`) payload

## Exposed local soundbar entities

- `media_player.soundbar_<ip>`
- `select` input source / sound mode
- `switch` power / mute
- `sensor` codec / identifier
- Optional diagnostic controls (subwoofer +/-, etc.)

## Input source reliability

Cloud:
- Often cycle-based (`setNextInputSource`) and not deterministic

Local:
- Direct source selection methods are more reliable on supported models

## Troubleshooting

## 422 / 409 errors

Usually indicate:
- Device state mismatch
- Command unsupported in current state
- Device powered off / source locked

## 429 Too Many Requests

Recommendations:
- Increase polling interval
- Avoid rapid command bursts
- Reduce parallel command paths against same account/token

