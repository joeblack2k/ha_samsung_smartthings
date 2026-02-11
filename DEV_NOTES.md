# Developer Notes

## Current Focus
- Extend integration beyond cloud + soundbar local with a full Frame TV local path.
- Keep cloud and soundbar behavior stable while adding Frame local features.

## Implemented in this cycle
- Added `frame_local` config entry type.
- Added local Frame API wrapper (`frame_local_api.py`) based on `samsungtvws`.
- Added Frame local entities:
  - switch: Art Mode (+ diagnostic brightness sensor toggle)
  - select: Artwork, Matte, Photo Filter
  - number: Art Brightness (+ diagnostic slideshow minutes and motion sensitivity)
  - sensor: Art API version, current artwork, artwork count (+ diagnostic last errors)
- Added Frame services:
  - upload/select/delete/delete_list artwork
  - folder sync with dedup + optional orphan cleanup
  - slideshow/motion/brightness-sensor controls

## Known Constraints / Caveats
- Samsung local APIs vary by firmware; some advanced fields may be absent.
- Local Frame features depend on first TV pairing approval popup.
- Some advanced settings are exposed as diagnostic entities by default.

## Debugging Checklist
- If Frame entities are unavailable:
  - verify TV IP and websocket port (8002 preferred)
  - verify pairing popup was accepted on TV
  - check HA logs for `frame_local` errors
- If services fail:
  - validate file path/content_id
  - run service once and inspect `sensor.*_last_errors` payload
