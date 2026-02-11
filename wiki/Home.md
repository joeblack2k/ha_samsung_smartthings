# Samsung SmartThings (Cloud + Local) Wiki

![Samsung SmartThings Integration](assets/integration-logo.png)

Welcome to the detailed wiki for **ha_samsung_smartthings**.

This integration combines:
- SmartThings **Cloud** control for Samsung TVs and soundbars
- **Local LAN** control for supported Samsung soundbars
- **Frame TV Local Art API** for reliable art mode and artwork workflows

## Wiki goals

- Practical onboarding without forcing a single auth strategy
- Technical compatibility matrix per device type and control path (cloud/local)
- Advanced automation examples
- Developer-grade reference (capabilities, endpoints, error codes, retries)
- A curated knowledge base built from real device testing and open-source research

## Start here

1. [Quick Start & Login Flows](Quick-Start-and-Login-Flows)
2. [Compatibility Matrix](Compatibility-Matrix)
3. [Frame TV Deep Dive](FrameTV-Deep-Dive)
4. [Soundbar Deep Dive](Soundbar-Deep-Dive)
5. [Automation Recipes](Automation-Recipes)
6. [Developer Reference](Developer-Reference)
7. [Credits & References](Credits-and-References)

## Integration principles

- **Sane defaults**: no entity spam; noisy diagnostics are hidden/disabled by default.
- **Best effort + graceful fallback**: Samsung APIs are inconsistent; behavior degrades safely.
- **Cloud and local can coexist**: cloud for account discovery, local for deterministic control.
- **Automation-first**: services and media-player behavior are designed for HA automations.

## Quick summary

- For The Frame art workflows, prefer **Frame TV Local (LAN Art API)**.
- For 2024 Wi-Fi soundbars, prefer **Soundbar Local (LAN)**.
- Keep SmartThings Cloud for discovery and broad model coverage.
- App launch is available through:
  - `select.<tv>_app`
  - `media_player.play_media` with `app` media type
  - `samsung_smartthings.launch_app` service

