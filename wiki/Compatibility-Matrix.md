# Compatibility Matrix

## Overzicht

Compatibiliteit is opgesplitst in:
- **SmartThings Cloud path** (REST `/v1/devices/...` capabilities)
- **Local LAN path** (model/firmware afhankelijk, direct device API)

## TV Matrix

| Feature | SmartThings Cloud TV | Frame TV Local (Art API) |
|---|---|---|
| Device discovery | Ja | Nee (per-IP setup) |
| Power on/off | Ja | Beperkt via art API context |
| Volume/mute (waar capability bestaat) | Ja | Niet focus van art API |
| App launch | Ja (`custom.launchapp`) | Ja (`run_app`) |
| URL open | Beperkt (YouTube app fallback) | Ja (browser/open + YouTube deep link) |
| Art Mode on/off | Best effort (model/account afhankelijk) | Ja |
| Artwork upload/select/delete | Nee | Ja |
| Matte/photo filter | Nee | Ja (indien firmware ondersteund) |
| Slideshow/motion/brightness sensor | Nee | Ja (indien firmware ondersteund) |

## Soundbar Matrix

| Feature | SmartThings Cloud Soundbar | Soundbar Local (LAN) |
|---|---|---|
| Discovery | Ja | Nee (per-IP setup) |
| Power | Ja | Ja |
| Volume/mute | Ja | Ja |
| Input source | Vaak inconsistent op cloud | Ja (deterministischer) |
| Sound mode | Ja (fallback aliases/candidates) | Ja (validated mode list) |
| Night mode | Execute pad (model afhankelijk) | Ja (local advanced calls + fallback event) |
| Advanced execute controls | Gedeeltelijk, vaak zonder readback | Gedeeltelijk, afhankelijk van method support |

## Geteste modellen (praktijk)

## Frame TV

- `QE65LS03BAUXXN` (The Frame 65) -> cloud + local flows gebruikt
- `QE32LS03TBWXXN` (The Frame 32) -> local connectiviteit model/standby afhankelijk

## Soundbars

- `HW-Q990D` -> cloud + local onderzocht/getest
- `Q990C` -> community report: input switching issues op cloud mogelijk

## Niet-exhaustieve support statement

Deze integratie werkt capability-gedreven. Dat betekent:
- Als capability op device aanwezig is, wordt functionaliteit geactiveerd.
- Als capability onbetrouwbaar is (422/409/no readback), wordt gedrag defensief gemaakt.

Dit is bewuste designkeuze, omdat Samsung gedrag per regio/model/firmware varieert.

## Waarom iets in SmartThings app soms wel werkt maar via API niet

Veel Samsung/SmartThings workflows gebruiken interne paden die niet volledig in publiek REST gedrag terugkomen (of met andere validatie). Daardoor kun je verschil zien tussen app UI en API commando-uitkomst.

