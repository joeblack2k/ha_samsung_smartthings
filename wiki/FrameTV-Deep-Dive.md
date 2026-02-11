# Frame TV Deep Dive

## Architecture

Frame support has two control paths:

1. **Cloud TV** via SmartThings capabilities
2. **Frame Local** via websocket art API (`samsungtvws` wrapper)

For artwork workflows, local mode is the primary path.

## Local Frame API behavior model

The local client:
- Tries configured websocket port and alternates (`8002`, `8001`)
- Remembers the last working port
- Uses an async lock for call safety
- Marks unsupported methods using error code heuristics

Important local error patterns:
- `error number -1`: usually unsupported method for current model/firmware
- `error number -9`: usually invalid slideshow category

## Exposed Frame entities

- `media_player.frame_tv_<ip>_art_browser`
- `switch.frame_tv_<ip>_art_mode`
- `select.frame_tv_<ip>_app`
- `select.frame_tv_<ip>_artwork`
- `number.frame_tv_<ip>_art_brightness`
- Sensors for API version, current artwork, and artwork count
- Diagnostic selects for matte/filter (hidden/disabled by default)

## Frame services

Core lifecycle services:
- `frame_upload_artwork`
- `frame_select_artwork`
- `frame_delete_artwork`
- `frame_delete_artwork_list`
- `frame_sync_folder`
- `frame_set_local_file`
- `frame_set_internet_artwork`
- `frame_set_favorite_artwork`

Advanced services:
- `frame_set_slideshow`
- `frame_set_motion_timer`
- `frame_set_motion_sensitivity`
- `frame_set_brightness_sensor`

## Border/matte handling

- `use_border: false` attempts borderless rendering (`none`/best available equivalent)
- `matte_id` can explicitly force a matte style
- If matte update fails, artwork selection still completes where possible

## Media browser + panel behavior

The integration supports:
- Local folder browsing (`/config/FrameTV`)
- Internet collections (`museums`, `nature`, `architecture`)
- Favorites workflow

## App launch on Frame

Supported via:
- `select.frame_tv_<ip>_app`
- `media_player.play_media` with `media_content_type: app`
- `media_player.play_media` with URL:
  - YouTube URL -> YouTube app deep-link
  - Other URL -> TV browser open

## Automation examples

## Monthly random museum artwork

```yaml
alias: Frame monthly random museum art
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

## First day of month: set local wallpaper file

```yaml
alias: Frame monthly local wallpaper
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

## Play a specific YouTube video on Frame

```yaml
alias: Play YouTube on Frame
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

