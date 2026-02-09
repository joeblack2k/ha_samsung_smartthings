## Work Notes

### 2026-02-09
- Goal: execute tasks sequentially, integrate LAN reference where useful, then audit and validate in HA.
- Found setup bug: `ENTRY_TYPE_CLOUD` used in `__init__.py` without import. This can break setup paths.
- Added local-mode entity expansion:
  - Forward local entry platforms: `media_player`, `sensor`, `switch`, `select`.
  - Added local switches: `Power`, `Mute`.
  - Added local selects: `Input Source`, `Sound Mode`.
- Kept execute-heavy soundbar entities diagnostic/disabled by default unless proven useful.
- Next validation steps:
  - Python syntax compile.
  - Copy integration to HA `custom_components`.
  - HA config check + restart.
  - Log audit focused on `custom_components.samsung_smartthings`.
