# Quick Start & Login Flows

## Installatie

### HACS (aanbevolen)

1. Open HACS -> Integrations.
2. Voeg deze repo toe als custom repository.
3. Installeer de integratie.
4. Restart Home Assistant.

### Manual

Kopieer `custom_components/samsung_smartthings` naar je HA `custom_components/` map en restart HA.

## Setup Types

Tijdens `Add integration` krijg je meerdere setup paden:

1. **SmartThings Cloud (Use Home Assistant SmartThings login, recommended)**
2. **SmartThings Cloud (OAuth2, bring your own app)**
3. **SmartThings Cloud (PAT token)**
4. **Soundbar Local (LAN)**
5. **Frame TV Local (LAN, Art API)**

---

## Login/Authenticatie Flows

## 1) Cloud via Home Assistant SmartThings login (aanbevolen)

Dit is de meest gebruiksvriendelijke en stabiele route.

### Stappen

1. Configureer eerst de officiële Home Assistant `SmartThings` integratie.
2. Rond browser-login af op SmartThings website.
3. Voeg daarna deze custom integratie toe.
4. Kies `SmartThings Cloud (Use Home Assistant SmartThings login, recommended)`.
5. Selecteer de bestaande SmartThings account/location entry.

### Waarom dit pad

- Geen handmatige PAT rotatie.
- Geen developer app registratie nodig.
- Werkt voor meeste gebruikers direct.

---

## 2) Cloud via OAuth2 (bring your own app)

Voor developers/advanced deploys.

### Benodigd

- SmartThings developer app (OAuth2 client id/secret)
- Application Credentials in Home Assistant

### Stappen

1. Maak SmartThings OAuth app in SmartThings Developer Workspace.
2. Zet redirect URI van Home Assistant in de app.
3. Voeg credential toe in HA `Application Credentials`.
4. Kies in config flow: `SmartThings Cloud (OAuth2, bring your own app)`.

---

## 3) Cloud via PAT token

Snel voor debug/probes, minder geschikt voor permanente setup.

### Belangrijk

- SmartThings PAT’s kunnen kort leven (vaak ~24 uur).
- Gebruik liever HA SmartThings login of OAuth2 voor duurzame setup.

---

## 4) Soundbar Local (LAN)

Voor ondersteunde Samsung Wi-Fi soundbars met local JSON-RPC API.

### Vereisten

- Soundbar op Wi-Fi
- Device in SmartThings app
- **IP control** ingeschakeld in SmartThings app

### Stappen

1. Voeg integratie toe en kies `Soundbar Local (LAN)`.
2. Vul soundbar IP in.
3. `verify_ssl` meestal uit (self-signed cert).

---

## 5) Frame TV Local (LAN, Art API)

Voor betrouwbare art mode en artwork lifecycle.

### Vereisten

- Samsung The Frame op LAN
- TV bereikbaar vanaf HA host
- Smart Hub / websocket pairing beschikbaar

### Stappen

1. Kies `Frame TV Local (LAN, Art API)`.
2. Vul TV IP in.
3. Stel client name in (optioneel).
4. Accepteer pairing popup op TV.

### Troubleshooting

- Als connectie faalt: controleer IP, slaapstand, netwerksegment, firewall.
- De config flow valideert host vóór entry-creation om false positives te voorkomen.

