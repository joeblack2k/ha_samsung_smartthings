# Samsung SmartThings (Cloud + Local) Wiki

![Samsung SmartThings Integration](assets/integration-logo.png)

Welkom bij de uitgebreide wiki voor **ha_samsung_smartthings**.

Deze integratie combineert:
- SmartThings **Cloud** besturing voor Samsung TV's en soundbars
- **Local LAN** besturing voor ondersteunde Samsung soundbars
- **Frame TV Local Art API** voor betrouwbare art mode/artwork workflows

## Doelen van deze wiki

- Praktische onboarding (zonder vendor lock-in in 1 methode)
- Technische compatibiliteitsmatrix per device type en pad (cloud/local)
- Geavanceerde automation voorbeelden
- Developer-level referentie (capabilities, endpoints, foutcodes, retries)
- Gecureerde kennisbank gebaseerd op echte field-testing en open-source research

## Start Hier

1. [Quick Start & Login Flows](Quick-Start-and-Login-Flows)
2. [Compatibility Matrix](Compatibility-Matrix)
3. [Frame TV Deep Dive](FrameTV-Deep-Dive)
4. [Soundbar Deep Dive](Soundbar-Deep-Dive)
5. [Automation Recipes](Automation-Recipes)
6. [Developer Reference](Developer-Reference)
7. [Credits & References](Credits-and-References)

## Kernprincipes van deze integratie

- **Sane defaults**: geen entity-spam en diagnostiek standaard verborgen/uitgeschakeld.
- **Best effort + graceful fallback**: veel Samsung API's zijn inconsistent; de integratie degradeert netjes.
- **Cloud én local mogelijk**: cloud voor account-discovery en brede compatibiliteit, local voor deterministische controle.
- **Automation-first**: services en media-player gedrag zijn ontworpen voor Home Assistant automation gebruik.

## Snelle Samenvatting

- Gebruik voor The Frame art workflows bij voorkeur **Frame TV Local (LAN Art API)**.
- Gebruik voor 2024 Wi-Fi soundbars bij voorkeur **Soundbar Local (LAN)**.
- Gebruik SmartThings Cloud voor discovery en brede device-support.
- Voor app launch is nu beschikbaar:
  - `select.<tv>_app`
  - `media_player.play_media` met `media_content_type: app`
  - YouTube URL best effort (cloud/local verschillend)

