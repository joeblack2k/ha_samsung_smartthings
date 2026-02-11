# Frame TV Deep Dive

## Architectuur

De Frame ondersteuning heeft twee paden:

1. **Cloud TV** via SmartThings capabilities
2. **Frame Local** via websocket art API (`samsungtvws`)

Voor art workflows is local leidend.

## Local Frame API gedragsmodel

De local client:
- probeert geconfigureerde websocket port plus alternates (`8002`, `8001`)
- onthoudt laatste werkende port
- gebruikt lock voor async-safe calls
- markeert unsupported methods op basis van foutcodes

Belangrijke signalen:
- `error number -1` -> vaak unsupported method op dit model
- `error number -9` -> vaak invalid slideshow category

## Exposed Frame Entities

- `media_player.frame_tv_<ip>_art_browser`
- `switch.frame_tv_<ip>_art_mode`
- `select.frame_tv_<ip>_app`
- `select.frame_tv_<ip>_artwork`
- `number.frame_tv_<ip>_art_brightness`
- Sensors voor api version, current artwork, artwork count
- Diagnostische selects voor matte/filter (standaard verborgen/uit)

## Frame Services

Kernservices:
- `frame_upload_artwork`
- `frame_select_artwork`
- `frame_delete_artwork`
- `frame_delete_artwork_list`
- `frame_sync_folder`
- `frame_set_local_file`
- `frame_set_internet_artwork`
- `frame_set_favorite_artwork`

Advanced:
- `frame_set_slideshow`
- `frame_set_motion_timer`
- `frame_set_motion_sensitivity`
- `frame_set_brightness_sensor`

## Border/Matte logica

- `use_border: false` -> probeert border te vermijden (none/zonder matte)
- `matte_id` kan expliciet gekozen worden
- als matte operation faalt, blijft artwork selectie geslaagd waar mogelijk

## Media Browser + Panel

De integratie ondersteunt:
- local folder (`/config/FrameTV`) artwork selectie
- internet collecties (`museums`, `nature`, `architecture`)
- favorites workflow

## App Launch op Frame

Nu ondersteund via:
- `select.frame_tv_<ip>_app`
- `media_player.play_media` met `media_content_type: app`
- `media_player.play_media` met URL:
  - YouTube URL -> YouTube app deep-link
  - overige URL -> browser open

## Automation voorbeelden

## Maandelijks random museum artwork

```yaml
alias: Frame maandelijkse random museum art
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
      collection: museums
      random: true
      show_now: true
      use_border: false
```

## Eerste dag maand lokaal bestand als wallpaper

```yaml
alias: Frame maandelijkse lokale wallpaper
trigger:
  - platform: time
    at: "09:05:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
action:
  - service: samsung_smartthings.frame_set_local_file
    data:
      frame_entity_id: media_player.frame_tv_192_168_2_172_art_browser
      path: foto.jpg
      show_now: true
      use_border: false
```

## YouTube video starten op Frame

```yaml
alias: Speel YouTube op Frame
trigger:
  - platform: state
    entity_id: input_boolean.start_video
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.frame_tv_192_168_2_172_art_browser
    data:
      media_content_type: url
      media_content_id: "https://www.youtube.com/watch?v=MNkDPfjr0E8"
```

