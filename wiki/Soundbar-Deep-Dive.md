# Soundbar Deep Dive

## Architectuur

Twee paden:

1. **SmartThings Cloud soundbar** (capabilities + execute)
2. **Soundbar Local LAN** (JSON-RPC over HTTPS:1516)

## Cloud Soundbar - realiteit

SmartThings cloud voor soundbars is inconsistent op sommige firmware:
- Commands geven `200` maar state blijft gelijk
- `execute` payload/readback vaak leeg
- Input source switching werkt niet altijd ondanks capability aanwezigheid
- Rate limits (`429`) ontstaan snel bij polling + bursts

Daarom bevat de integratie:
- retries/backoff op conflicts/rate limits
- defensieve validatie
- fallback sound mode candidates
- diagnostische entities standaard hidden/disabled

## Execute endpoints (cloud)

Belangrijke paden:
- `/sec/networkaudio/soundmode`
- `/sec/networkaudio/woofer`
- `/sec/networkaudio/eq`
- `/sec/networkaudio/advancedaudio`
- `/sec/networkaudio/channelVolume`
- `/sec/networkaudio/surroundspeaker`
- `/sec/networkaudio/activeVoiceAmplifier`
- `/sec/networkaudio/spacefitSound`

## Sound mode aliases

Voor adaptive varianten ondersteunt de integratie meerdere schrijfwaarden:
- `adaptive`
- `adaptive_sound`
- `adaptive sound`
- uppercase varianten

Dit voorkomt model/firmaware mismatch zoals gerapporteerd op Q-series modellen.

## Local Soundbar API (1516)

Protocol:
- HTTPS POST JSON-RPC
- self-signed cert standaard
- token creation via `createAccessToken`

Voorbeelden van methods:
- `powerControl`
- `remoteKeyControl`
- `inputSelectControl`
- `soundModeControl`
- `setAdvancedSoundSettings`
- getters zoals `getVolume`, `getMute`, `getCodec`, `getIdentifier`

## Night mode local

Night mode probeert:
1. `setAdvancedSoundSettings` met `nightMode`
2. fallback app-style event payload (`ms.channel.emit`)

## Exposed Local Soundbar Entities

- `media_player.soundbar_<ip>`
- `select` input source / sound mode
- `switch` power / mute
- `sensor` codec / identifier
- optionele diagnostische controls (subwoofer plus/min, etc.)

## Input Source betrouwbaarheid

Cloud:
- vaak cycle-based (`setNextInputSource`) en niet deterministisch

Local:
- direct input select methoden geven betere resultaten op ondersteunde modellen

## Troubleshooting

## 422 / 409 errors

- device state mismatch
- command unsupported in huidige context
- device powered off / source locked

## 429 Too Many Requests

- verhoog polling interval
- vermijd rapid-fire automation bursts
- beperk parallel command streams

