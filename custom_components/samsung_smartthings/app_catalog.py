from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SamsungApp:
    name: str
    app_id: str

    @property
    def option(self) -> str:
        return f"{self.name} ({self.app_id})"


_APPS: tuple[SamsungApp, ...] = (
    SamsungApp("Disney+", "3201901017640"),
    SamsungApp("Disney+ (new)", "3202204027038"),
    SamsungApp("Netflix", "11101200001"),
    SamsungApp("Netflix (new)", "3201907018807"),
    SamsungApp("YouTube", "111299001912"),
    SamsungApp("YouTube Kids", "3201611010983"),
    SamsungApp("Prime Video", "3201512006785"),
    SamsungApp("Prime Video (new)", "3201910019365"),
    SamsungApp("Viaplay", "11111300404"),
    SamsungApp("KPN iTV", "3201803015963"),
    SamsungApp("RTL XL / Videoland", "3201906018642"),
    SamsungApp("Ziggo GO", "3201901017581"),
    SamsungApp("Apple TV", "3201807016597"),
    SamsungApp("Apple TV (new)", "3202106024097"),
    SamsungApp("Spotify", "3201606009684"),
    SamsungApp("Plex", "3201512006963"),
    SamsungApp("Web Browser", "org.tizen.browser"),
    SamsungApp("SmartThings", "3201910019378"),
)

_BY_ID: dict[str, SamsungApp] = {app.app_id: app for app in _APPS}
_BY_NAME: dict[str, SamsungApp] = {app.name.lower(): app for app in _APPS}

YOUTUBE_APP = SamsungApp("YouTube", "111299001912")
BROWSER_APP = SamsungApp("Web Browser", "org.tizen.browser")


def app_options() -> list[str]:
    return [app.option for app in _APPS]


def resolve_app(value: str | None) -> SamsungApp | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None

    # Exact app id.
    if raw in _BY_ID:
        return _BY_ID[raw]

    # Label format "Name (app_id)".
    if raw.endswith(")") and " (" in raw:
        name, _, tail = raw.rpartition(" (")
        app_id = tail[:-1].strip()
        if app_id in _BY_ID:
            return _BY_ID[app_id]
        if name.strip().lower() in _BY_NAME:
            return _BY_NAME[name.strip().lower()]

    # Name lookup.
    return _BY_NAME.get(raw.lower())


def is_http_url(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def is_youtube_url(value: str | None) -> bool:
    if not is_http_url(value):
        return False
    host = (urlparse(value.strip()).hostname or "").lower()
    return "youtube.com" in host or "youtu.be" in host

