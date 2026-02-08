"""SmartThings OAuth2 application credentials support."""

from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer


async def async_get_authorization_server(hass) -> AuthorizationServer:
    """Return the authorization server for SmartThings."""
    return AuthorizationServer(
        authorize_url="https://api.smartthings.com/oauth/authorize",
        token_url="https://api.smartthings.com/oauth/token",
    )


async def async_get_auth_scopes(hass) -> list[str]:
    """Return SmartThings OAuth2 scopes required by this integration."""
    # Read devices + execute commands + (some accounts require locations read).
    return ["r:devices:*", "x:devices:*", "r:locations:*"]


# Back-compat: older HA versions used async_get_scopes.
async def async_get_scopes(hass) -> list[str]:
    return await async_get_auth_scopes(hass)
