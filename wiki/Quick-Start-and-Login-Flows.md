# Quick Start & Login Flows

## Installation

### HACS (recommended)

1. Open HACS -> Integrations.
2. Add this repository as a custom repository.
3. Install the integration.
4. Restart Home Assistant.

### Manual installation

Copy `custom_components/samsung_smartthings` into your HA `custom_components/` folder and restart HA.

## Setup types

When adding the integration, you can choose:

1. **SmartThings Cloud (Use Home Assistant SmartThings login, recommended)**
2. **SmartThings Cloud (OAuth2, bring your own app)**
3. **SmartThings Cloud (PAT token)**
4. **Soundbar Local (LAN)**
5. **Frame TV Local (LAN, Art API)**

## 1) Cloud using Home Assistant SmartThings login (recommended)

This is the easiest and most durable path for most users.

### Steps

1. Configure the official Home Assistant `SmartThings` integration first.
2. Complete the browser login on SmartThings.
3. Add this custom integration.
4. Choose `SmartThings Cloud (Use Home Assistant SmartThings login, recommended)`.
5. Select the existing SmartThings account/location entry.

### Why this path

- No manual PAT rotation.
- No separate developer app registration.
- Fast onboarding for regular users.

## 2) Cloud via OAuth2 (bring your own app)

For advanced users and developers.

### Requirements

- SmartThings OAuth app (client ID/secret)
- Home Assistant Application Credentials

### Steps

1. Create a SmartThings OAuth app in SmartThings Developer Workspace.
2. Add Home Assistant callback URL to that app.
3. Add credentials under HA `Application Credentials`.
4. Choose `SmartThings Cloud (OAuth2, bring your own app)` in config flow.

## 3) Cloud via PAT token

Useful for quick testing, less suitable for long-term setups.

### Important

- SmartThings PAT tokens can be short-lived (often around 24 hours).
- Prefer HA SmartThings login or OAuth2 for permanent usage.

## 4) Soundbar Local (LAN)

For supported Samsung Wi-Fi soundbars with local JSON-RPC API.

### Requirements

- Soundbar connected to Wi-Fi
- Device added in SmartThings app
- **IP control** enabled in SmartThings app settings for that soundbar

### Steps

1. Add integration and choose `Soundbar Local (LAN)`.
2. Enter soundbar IP address.
3. Keep `verify_ssl` disabled in most setups (self-signed certificate).

## 5) Frame TV Local (LAN, Art API)

For reliable Art Mode and artwork lifecycle control.

### Requirements

- Samsung The Frame reachable on LAN
- TV accessible from the HA host network
- Smart Hub / websocket pairing available

### Steps

1. Choose `Frame TV Local (LAN, Art API)`.
2. Enter TV IP address.
3. Optionally set client name shown on TV.
4. Accept the pairing popup on the TV.

### Troubleshooting

- If connection fails: verify IP, standby state, subnet routing, and firewall rules.
- Config flow validates host connectivity before creating the entry.

