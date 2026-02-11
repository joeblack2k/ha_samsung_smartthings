# Automation Recipes

## 1) Play a specific YouTube video on The Frame (cloud media player)

```yaml
alias: Play specific YouTube video on The Frame (Cloud)
trigger:
  - platform: state
    entity_id: input_boolean.start_video
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.samsung_the_frame_65_media
    data:
      media_content_id: "https://www.youtube.com/watch?v=MNkDPfjr0E8"
      media_content_type: "url"
```

## 2) Start YouTube playlist on Frame Local

```yaml
alias: Start YouTube playlist on Frame Local
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

## 3) Launch app via select entity

```yaml
alias: Launch Netflix via app select
trigger:
  - platform: state
    entity_id: input_boolean.netflix
    to: "on"
action:
  - service: select.select_option
    target:
      entity_id: select.samsung_the_frame_65_app
    data:
      option: "Netflix (11101200001)"
```

## 4) Launch app via media_player.play_media

```yaml
alias: Launch Disney+ via media_player.play_media
trigger:
  - platform: state
    entity_id: input_boolean.disney
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.samsung_the_frame_65_media
    data:
      media_content_type: app
      media_content_id: "app:3201901017640"
```

## 5) First day of month: random internet art by category

```yaml
alias: Frame monthly random internet art
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

## 6) First day of month: fixed local file from /config/FrameTV

```yaml
alias: Frame monthly fixed local wallpaper
trigger:
  - platform: time
    at: "09:10:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
action:
  - service: samsung_smartthings.frame_set_local_file
    data:
      frame_entity_id: media_player.frame_tv_192_168_2_172_art_browser
      path: "foto.jpg"
      show_now: true
      use_border: false
```

## 7) Monthly random favorite artwork

```yaml
alias: Frame monthly random favorite
trigger:
  - platform: time
    at: "09:15:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
action:
  - service: samsung_smartthings.frame_set_favorite_artwork
    data:
      frame_entity_id: media_player.frame_tv_192_168_2_172_art_browser
      random: true
      show_now: true
      use_border: false
```

## 8) Soundbar night mode schedule

```yaml
alias: Soundbar night mode schedule
trigger:
  - platform: time
    at: "23:00:00"
action:
  - service: samsung_smartthings.set_night_mode
    data:
      entity_id: media_player.soundbar_192_168_2_165
      night: true
```

## 9) Soundbar input source switch

```yaml
alias: Soundbar switch to HDMI 1
trigger:
  - platform: state
    entity_id: input_boolean.soundbar_hdmi1
    to: "on"
action:
  - service: select.select_option
    target:
      entity_id: select.input_source_2
    data:
      option: "HDMI_IN1"
```

