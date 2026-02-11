from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import tempfile
from aiohttp import web
from pathlib import Path
from datetime import timedelta
from urllib.parse import quote, urlparse

from aiohttp import ClientResponseError
from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CLOUD_SOUNDMODES,
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_DISCOVERY_INTERVAL,
    CONF_ENTRY_TYPE,
    CONF_EXPOSE_ALL,
    CONF_HOST as CONF_HOST_LOCAL,
    CONF_INCLUDE_NON_SAMSUNG,
    CONF_MANAGE_DIAGNOSTICS,
    CONF_PAT_TOKEN,
    CONF_SMARTTHINGS_ENTRY_ID,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_VERIFY_SSL,
    CONF_WS_NAME,
    CONF_WS_PORT,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_FRAME_WS_NAME,
    DEFAULT_FRAME_WS_PORT,
    DEFAULT_CLOUD_SOUNDMODES,
    DEFAULT_EXPOSE_ALL,
    DEFAULT_INCLUDE_NON_SAMSUNG,
    DEFAULT_LOCAL_FRAME_POLL_INTERVAL,
    DEFAULT_LOCAL_SOUNDBAR_POLL_INTERVAL,
    DEFAULT_MANAGE_DIAGNOSTICS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_CLOUD,
    ENTRY_TYPE_FRAME_LOCAL,
    ENTRY_TYPE_SOUNDBAR_LOCAL,
    PLATFORMS,
)
from .coordinator import SmartThingsCoordinator
from .device import SmartThingsDevice
from .smartthings_api import SmartThingsApi
from .soundbar_local_api import AsyncSoundbarLocal, SoundbarLocalError
from .frame_local_api import AsyncFrameLocal, FrameLocalError, FrameLocalUnsupportedError

_LOGGER = logging.getLogger(__name__)
OAUTH2_TOKEN_KEY = getattr(config_entry_oauth2_flow, "CONF_TOKEN", "token")
FRAME_SYNC_STORE_VERSION = 1
FRAME_SYNC_STORE_KEY = f"{DOMAIN}_frame_sync"
FRAME_PANEL_STORE_VERSION = 1
FRAME_PANEL_STORE_KEY = f"{DOMAIN}_frame_panel"
FRAME_PANEL_URL_PATH = "frame-tv"
FRAME_PANEL_DATA_PATH = "/api/samsung_smartthings/frame_tv_panel/data"
FRAME_PANEL_SET_PATH = "/api/samsung_smartthings/frame_tv_panel/set"
FRAME_PANEL_THUMB_PATH = "/api/samsung_smartthings/frame_tv_panel/thumb"
FRAME_PANEL_FAVORITE_PATH = "/api/samsung_smartthings/frame_tv_panel/favorite"

_INTERNET_COLLECTIONS: dict[str, dict[str, str]] = {
    "museums": {"title": "Museums", "query": "museum,art"},
    "nature": {"title": "Nature", "query": "nature,landscape"},
    "architecture": {"title": "Architecture", "query": "architecture,minimal"},
}
_INTERNET_ITEMS_PER_COLLECTION = 24


def _internet_item_url(collection_slug: str, idx: int) -> str:
    # Use deterministic Picsum URLs for stable previews/downloads without API keys.
    return f"https://picsum.photos/seed/{collection_slug}-{idx}/1920/1080"


def _internet_item_thumb_url(collection_slug: str, idx: int) -> str:
    return f"{FRAME_PANEL_THUMB_PATH}?source=internet&item={quote(f'{collection_slug}:{idx}', safe='')}"


def _local_item_thumb_url(item_id: str) -> str:
    return f"{FRAME_PANEL_THUMB_PATH}?source=local&item={quote(item_id, safe='')}"


def _favorite_item_id(source: str, item_id: str) -> str:
    return hashlib.sha1(f"{source}:{item_id}".encode("utf-8")).hexdigest()[:12]


def _panel_store(hass: HomeAssistant) -> Store:
    hass.data.setdefault(DOMAIN, {})
    key = "_frame_panel_store"
    store = hass.data[DOMAIN].get(key)
    if isinstance(store, Store):
        return store
    store = Store(hass, FRAME_PANEL_STORE_VERSION, FRAME_PANEL_STORE_KEY)
    hass.data[DOMAIN][key] = store
    return store


async def _load_panel_favorites(hass: HomeAssistant) -> dict[str, dict]:
    data = await _panel_store(hass).async_load() or {}
    favorites = data.get("favorites")
    if isinstance(favorites, dict):
        return {str(k): v for k, v in favorites.items() if isinstance(v, dict)}
    return {}


async def _save_panel_favorites(hass: HomeAssistant, favorites: dict[str, dict]) -> None:
    await _panel_store(hass).async_save({"favorites": favorites})


def _svg_placeholder(title: str) -> str:
    safe = (title or "Artwork unavailable").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">'
        '<rect width="100%" height="100%" fill="#111827"/>'
        '<rect x="60" y="60" width="1800" height="960" rx="18" fill="#1f2937" stroke="#374151" stroke-width="4"/>'
        '<text x="960" y="520" text-anchor="middle" fill="#cbd5e1" font-family="Arial, sans-serif" font-size="56">'
        f"{safe}"
        "</text>"
        '<text x="960" y="600" text-anchor="middle" fill="#94a3b8" font-family="Arial, sans-serif" font-size="34">Source unavailable</text>'
        "</svg>"
    )


async def _download_internet_image(hass: HomeAssistant, collection_slug: str, idx: int) -> bytes:
    if collection_slug not in _INTERNET_COLLECTIONS:
        raise ValueError("Unknown internet collection")
    if idx < 1:
        raise ValueError("Invalid internet item index")
    url = _internet_item_url(collection_slug, idx)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid URL")
    session = aiohttp_client.async_get_clientsession(hass)
    async with session.get(url, timeout=20) as resp:
        resp.raise_for_status()
        body = await resp.read()
    if not body:
        raise ValueError("Downloaded image is empty")
    return body


def _resolve_matte_id(
    matte_options: list[str],
    use_border: bool,
    preferred_matte: str | None = None,
) -> str | None:
    opts = [x for x in matte_options if isinstance(x, str) and x]
    if not use_border:
        for item in opts:
            if "none" in item.lower():
                return item
        return "none"
    if preferred_matte:
        return preferred_matte
    if not opts:
        return None
    for item in opts:
        if "none" not in item.lower():
            return item
    return opts[0]


async def _set_frame_artwork_with_border(
    frame: AsyncFrameLocal,
    coordinator: DataUpdateCoordinator,
    content_id: str,
    show_now: bool,
    use_border: bool,
    matte_id: str | None = None,
) -> None:
    await frame.select_artwork(content_id, bool(show_now))
    # On older Frame firmware this follow-up matte call is flaky/timeouts.
    # If border is disabled and no explicit matte override is requested,
    # keep the selection fast and skip extra matte operation.
    if not use_border and not matte_id:
        return
    matte_options = coordinator.data.get("matte_options") if isinstance(coordinator.data, dict) else None
    options = matte_options if isinstance(matte_options, list) else []
    target = _resolve_matte_id(options, use_border=use_border, preferred_matte=matte_id)
    if target:
        try:
            await frame.change_matte(content_id, target)
        except Exception as err:
            _LOGGER.warning("Frame matte change failed for '%s': %s", target, err)


def _frame_local_entries(hass: HomeAssistant) -> list[dict]:
    out: list[dict] = []
    for entry_id, dom in (hass.data.get(DOMAIN) or {}).items():
        if not isinstance(dom, dict):
            continue
        if dom.get("type") != ENTRY_TYPE_FRAME_LOCAL:
            continue
        out.append(
            {
                "entry_id": entry_id,
                "title": f"Frame TV {dom.get('host')}",
                "host": dom.get("host"),
                "frame": dom.get("frame"),
                "coordinator": dom.get("coordinator"),
                "matte_options": (
                    [
                        str(x)
                        for x in (
                            (dom.get("coordinator").data.get("matte_options") if dom.get("coordinator") and isinstance(dom.get("coordinator").data, dict) else [])
                            or []
                        )
                        if isinstance(x, str) and x
                    ]
                    or ["none"]
                ),
            }
        )
    return out


def _frame_local_files(hass: HomeAssistant) -> list[dict[str, str]]:
    base = Path(hass.config.path("FrameTV"))
    if not base.exists():
        return []
    out: list[dict[str, str]] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("._") or p.name.startswith("."):
            continue
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        rel = p.relative_to(base).as_posix()
        out.append({"id": rel, "name": rel, "url": _local_item_thumb_url(rel), "source": "local"})
    return out


class FrameTVPanelDataView(HomeAssistantView):
    url = FRAME_PANEL_DATA_PATH
    name = "api:samsung_smartthings:frame_tv_panel_data"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        favorites = await _load_panel_favorites(self.hass)
        local_items = _frame_local_files(self.hass)
        for it in local_items:
            item_id = str(it.get("id", "") or "")
            fav_id = _favorite_item_id("local", item_id)
            it["favorite_id"] = fav_id
            it["is_favorite"] = fav_id in favorites
        collections = []
        for slug, cfg in _INTERNET_COLLECTIONS.items():
            items = []
            for idx in range(1, _INTERNET_ITEMS_PER_COLLECTION + 1):
                seed = random.randint(1, 999_999)
                item_id = f"{slug}:{seed}"
                fav_id = _favorite_item_id("internet", item_id)
                items.append(
                    {
                        "id": item_id,
                        "title": f"{cfg['title']} #{idx}",
                        "url": _internet_item_thumb_url(slug, seed),
                        "source": "internet",
                        "favorite_id": fav_id,
                        "is_favorite": fav_id in favorites,
                    }
                )
            collections.append({"id": slug, "title": cfg["title"], "items": items})
        favorite_items: list[dict] = []
        for fav_id, fav in favorites.items():
            source = str(fav.get("source", "") or "")
            item_id = str(fav.get("item_id", "") or "")
            title = str(fav.get("title", "") or f"{source}:{item_id}")
            if not source or not item_id:
                continue
            if source == "internet":
                slug, _, idx_raw = item_id.partition(":")
                try:
                    idx = int(idx_raw)
                except Exception:
                    continue
                url = _internet_item_thumb_url(slug, idx)
            elif source == "local":
                url = _local_item_thumb_url(item_id)
            else:
                continue
            favorite_items.append(
                {
                    "id": item_id,
                    "title": title,
                    "url": url,
                    "source": source,
                    "favorite_id": fav_id,
                    "is_favorite": True,
                }
            )
        return self.json(
            {
                "entries": [
                    {
                        "entry_id": e["entry_id"],
                        "title": e["title"],
                        "matte_options": e.get("matte_options") or [],
                    }
                    for e in _frame_local_entries(self.hass)
                ],
                "local": local_items,
                "internet": collections,
                "favorites": favorite_items,
            }
        )


class FrameTVPanelSetView(HomeAssistantView):
    url = FRAME_PANEL_SET_PATH
    name = "api:samsung_smartthings:frame_tv_panel_set"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        data = await request.json()
        entry_id = str(data.get("entry_id", "") or "").strip()
        source = str(data.get("source", "") or "").strip()
        item_id = str(data.get("item_id", "") or "").strip()
        use_border = bool(data.get("use_border", True))
        matte_id_raw = data.get("matte")
        matte_id = str(matte_id_raw).strip() if isinstance(matte_id_raw, str) and matte_id_raw.strip() else None
        if not entry_id or not source or not item_id:
            return self.json({"ok": False, "error": "entry_id, source, item_id required"}, status_code=400)

        dom = (self.hass.data.get(DOMAIN) or {}).get(entry_id)
        if not isinstance(dom, dict) or dom.get("type") != ENTRY_TYPE_FRAME_LOCAL:
            return self.json({"ok": False, "error": "Frame entry not found"}, status_code=404)
        frame = dom.get("frame")
        coordinator = dom.get("coordinator")
        if frame is None or coordinator is None:
            return self.json({"ok": False, "error": "Frame runtime not ready"}, status_code=503)

        try:
            if source == "local":
                base = Path(self.hass.config.path("FrameTV")).resolve()
                target = (base / item_id).resolve()
                if not str(target).startswith(str(base)):
                    raise ValueError("Invalid local path")
                if not target.is_file():
                    raise ValueError("File does not exist")
                if use_border:
                    content_id = await frame.upload_artwork(str(target))
                else:
                    content_id = await frame.upload_artwork(str(target), matte="none", portrait_matte="none")
            elif source == "internet":
                slug, _, idx_raw = item_id.partition(":")
                idx = int(idx_raw)
                body = await _download_internet_image(self.hass, slug, idx)
                with tempfile.NamedTemporaryFile(prefix="frame_panel_", suffix=".jpg", delete=False) as tmp:
                    tmp.write(body)
                    tmp_path = tmp.name
                try:
                    if use_border:
                        content_id = await frame.upload_artwork(tmp_path)
                    else:
                        content_id = await frame.upload_artwork(tmp_path, matte="none", portrait_matte="none")
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
            else:
                raise ValueError("Unknown source")
            await _set_frame_artwork_with_border(
                frame,
                coordinator,
                content_id,
                True,
                use_border=use_border,
                matte_id=matte_id,
            )
            await coordinator.async_request_refresh()
            return self.json({"ok": True, "content_id": content_id})
        except Exception as err:
            return self.json({"ok": False, "error": str(err)}, status_code=400)


class FrameTVPanelUIView(HomeAssistantView):
    url = "/api/samsung_smartthings/frame_tv_panel/ui"
    name = "api:samsung_smartthings:frame_tv_panel_ui"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FrameTV</title>
  <style>
    body {{
      font-family: var(--primary-font-family, system-ui, sans-serif);
      color: var(--primary-text-color);
      background: transparent;
      margin: 0; padding: 12px;
    }}
    .wrap {{ max-width: 100%; }}
    .row {{ display:flex; gap:10px; margin-bottom:10px; flex-wrap:wrap; align-items:center; }}
    select, button {{
      background: var(--ha-card-background, var(--card-background-color));
      color: var(--primary-text-color);
      border: 1px solid var(--divider-color);
      padding: 8px 10px;
      border-radius: 10px;
      font-size: 14px;
    }}
    .chip {{
      display:inline-flex; align-items:center; gap:6px;
      padding: 8px 10px;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      background: var(--ha-card-background, var(--card-background-color));
      color: var(--secondary-text-color);
      font-size: 13px;
    }}
    .chip input {{ margin:0; }}
    .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(200px,1fr)); gap:12px; }}
    .card {{
      background: var(--ha-card-background, var(--card-background-color));
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      overflow:hidden;
    }}
    .card img {{ width:100%; height:130px; object-fit:cover; display:block; background:var(--secondary-background-color); }}
    .meta {{ padding:8px; font-size:13px; min-height:48px; }}
    .meta button {{ width:100%; margin-top:8px; }}
    .meta-row {{ display:flex; gap:8px; margin-top:8px; }}
    .meta-row button {{ margin-top:0; }}
    .fav-btn {{ width:44px; min-width:44px; padding:0; font-size:18px; line-height:1; }}
    .hint {{ color: var(--secondary-text-color); font-size:12px; margin-bottom:10px; }}
  </style>
</head>
<body>
  <div class="wrap">
  <div class="hint">Kies een Frame TV, selecteer een bron, en klik <b>Set on TV</b>.</div>
  <div class="row">
    <select id="entry"></select>
    <select id="source">
      <option value="local">Local</option>
      <option value="internet">Internet</option>
    </select>
    <select id="collection" style="display:none"></select>
    <label class="chip"><input type="checkbox" id="border_toggle" checked /> Border</label>
    <select id="matte" title="Border type"></select>
    <button id="reload">Refresh</button>
  </div>
  <div id="status" class="hint"></div>
  <div id="grid" class="grid"></div>
  </div>
  <script>
    const dataUrl = "{FRAME_PANEL_DATA_PATH}";
    const setUrl = "{FRAME_PANEL_SET_PATH}";
    const favoriteUrl = "{FRAME_PANEL_FAVORITE_PATH}";
    const entrySel = document.getElementById("entry");
    const sourceSel = document.getElementById("source");
    const collectionSel = document.getElementById("collection");
    const borderToggle = document.getElementById("border_toggle");
    const matteSel = document.getElementById("matte");
    const statusEl = document.getElementById("status");
    const grid = document.getElementById("grid");
    let payload = null;

    function setStatus(msg) {{ statusEl.textContent = msg || ""; }}

    async function loadData() {{
      const prevEntry = entrySel.value;
      const prevCollection = collectionSel.value;
      const r = await fetch(dataUrl + "?ts=" + Date.now(), {{credentials:"same-origin"}});
      payload = await r.json();
      entrySel.innerHTML = "";
      for (const e of (payload.entries || [])) {{
        const opt = document.createElement("option");
        opt.value = e.entry_id;
        opt.textContent = e.title;
        entrySel.appendChild(opt);
      }}
      if (prevEntry) entrySel.value = prevEntry;
      renderCollections(prevCollection);
      renderMatteOptions();
      render();
    }}

    function renderMatteOptions() {{
      matteSel.innerHTML = "";
      const selected = (payload.entries || []).find((e) => e.entry_id === entrySel.value) || (payload.entries || [])[0];
      const options = selected?.matte_options || [];
      const auto = document.createElement("option");
      auto.value = "";
      auto.textContent = "Auto";
      matteSel.appendChild(auto);
      for (const v of options) {{
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        matteSel.appendChild(opt);
      }}
      matteSel.disabled = !borderToggle.checked;
    }}

    function renderCollections(preferred) {{
      const current = preferred || collectionSel.value;
      collectionSel.innerHTML = "";
      const favOpt = document.createElement("option");
      favOpt.value = "_favorites";
      favOpt.textContent = "Favorites";
      collectionSel.appendChild(favOpt);
      for (const c of (payload.internet || [])) {{
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.title;
        collectionSel.appendChild(opt);
      }}
      let target = current;
      if (!target) target = payload.internet?.[0]?.id || "_favorites";
      if ([...collectionSel.options].some((o) => o.value === target)) {{
        collectionSel.value = target;
      }}
    }}

    function makeCard(item, onClick) {{
      const card = document.createElement("div");
      card.className = "card";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = item.url || "";
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = item.title;
      const row = document.createElement("div");
      row.className = "meta-row";
      const btn = document.createElement("button");
      btn.textContent = "Set on TV";
      btn.onclick = onClick;
      const fav = document.createElement("button");
      fav.className = "fav-btn";
      fav.textContent = item.is_favorite ? "★" : "☆";
      fav.title = item.is_favorite ? "Remove favorite" : "Add favorite";
      fav.onclick = async () => {{
        const r = await fetch(favoriteUrl, {{
          method: "POST",
          credentials: "same-origin",
          headers: {{"Content-Type":"application/json"}},
          body: JSON.stringify({{
            source: item.source,
            item_id: item.id,
            title: item.title
          }})
        }});
        const res = await r.json();
        if (!res.ok) {{
          setStatus("Fout bij favoriet: " + (res.error || "Onbekend"));
          return;
        }}
        await loadData();
      }};
      row.appendChild(btn);
      row.appendChild(fav);
      meta.appendChild(row);
      card.appendChild(img);
      card.appendChild(meta);
      return card;
    }}

    async function setItem(source, itemId) {{
      const entryId = entrySel.value;
      if (!entryId) {{
        setStatus("Geen Frame TV entry geselecteerd.");
        return;
      }}
      setStatus("Applying artwork...");
      const r = await fetch(setUrl, {{
        method: "POST",
        credentials: "same-origin",
        headers: {{"Content-Type":"application/json"}},
        body: JSON.stringify({{
          entry_id: entryId,
          source,
          item_id: itemId,
          use_border: borderToggle.checked,
          matte: matteSel.value || null
        }})
      }});
      const res = await r.json();
      if (res.ok) setStatus("Artwork gezet op TV.");
      else setStatus("Fout: " + (res.error || "Onbekend"));
    }}

    function render() {{
      if (!payload) return;
      grid.innerHTML = "";
      const source = sourceSel.value;
      if (source === "local") {{
        collectionSel.style.display = "none";
        for (const it of (payload.local || [])) {{
          const card = makeCard(it, () => setItem("local", it.id));
          grid.appendChild(card);
        }}
      }} else {{
        collectionSel.style.display = "";
        const id = collectionSel.value || "_favorites";
        let items = [];
        if (id === "_favorites") {{
          items = payload.favorites || [];
        }} else {{
          const col = (payload.internet || []).find((x) => x.id === id);
          items = col?.items || [];
        }}
        for (const it of items) {{
          const src = it.source || "internet";
          const card = makeCard(it, () => setItem(src, it.id));
          grid.appendChild(card);
        }}
      }}
    }}

    sourceSel.onchange = () => {{ if (sourceSel.value === "internet") renderCollections(collectionSel.value); render(); }};
    entrySel.onchange = () => {{ renderMatteOptions(); render(); }};
    borderToggle.onchange = () => {{ matteSel.disabled = !borderToggle.checked; }};
    collectionSel.onchange = render;
    document.getElementById("reload").onclick = loadData;
    loadData();
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")


class FrameTVPanelThumbView(HomeAssistantView):
    url = FRAME_PANEL_THUMB_PATH
    name = "api:samsung_smartthings:frame_tv_panel_thumb"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        source = str(request.query.get("source", "internet") or "internet").strip().lower()
        item = str(request.query.get("item", "") or "").strip()
        if source == "local":
            base = Path(self.hass.config.path("FrameTV")).resolve()
            target = (base / item).resolve()
            if str(target).startswith(str(base)) and target.is_file():
                return web.FileResponse(path=target, headers={"Cache-Control": "public, max-age=300"})
            return web.Response(
                text=_svg_placeholder(item or "Local artwork"),
                content_type="image/svg+xml",
                headers={"Cache-Control": "no-cache"},
            )

        slug, _, idx_raw = item.partition(":")
        try:
            idx = int(idx_raw)
        except Exception:
            idx = 0
        title = _INTERNET_COLLECTIONS.get(slug, {}).get("title", "Artwork")
        try:
            body = await _download_internet_image(self.hass, slug, idx)
            return web.Response(
                body=body,
                content_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=300"},
            )
        except Exception as err:
            _LOGGER.debug("Frame panel thumb fetch failed for %s:%s: %s", slug, idx, err)
            return web.Response(
                text=_svg_placeholder(title),
                content_type="image/svg+xml",
                headers={"Cache-Control": "no-cache"},
            )


class FrameTVPanelFavoriteView(HomeAssistantView):
    url = FRAME_PANEL_FAVORITE_PATH
    name = "api:samsung_smartthings:frame_tv_panel_favorite"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        data = await request.json()
        source = str(data.get("source", "") or "").strip().lower()
        item_id = str(data.get("item_id", "") or "").strip()
        title = str(data.get("title", "") or "").strip() or item_id
        if source not in ("local", "internet") or not item_id:
            return self.json({"ok": False, "error": "source and item_id are required"}, status_code=400)
        fav_id = _favorite_item_id(source, item_id)
        favorites = await _load_panel_favorites(self.hass)
        if fav_id in favorites:
            favorites.pop(fav_id, None)
            await _save_panel_favorites(self.hass, favorites)
            return self.json({"ok": True, "favorite": False, "favorite_id": fav_id})
        favorites[fav_id] = {"source": source, "item_id": item_id, "title": title}
        await _save_panel_favorites(self.hass, favorites)
        return self.json({"ok": True, "favorite": True, "favorite_id": fav_id})


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the latest version."""
    # v4 introduced OAuth2 support and renamed PAT storage key.
    if entry.version < 4:
        data = dict(entry.data)
        # Old PAT entries stored the token under "token" (string). OAuth2 entries will
        # store a dict under the OAuth2 token key ("token").
        old = data.get(OAUTH2_TOKEN_KEY)
        if isinstance(old, str) and old and CONF_PAT_TOKEN not in data:
            data.pop(OAUTH2_TOKEN_KEY, None)
            data[CONF_PAT_TOKEN] = old
        hass.config_entries.async_update_entry(entry, data=data, version=4)
    return True


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _is_samsung(d: dict) -> bool:
    try:
        return str(d.get("manufacturerName") or "").lower().startswith("samsung")
    except Exception:
        return False


async def _get_hub_id(api: SmartThingsApi, token: str) -> str:
    """Return a stable hub id for device registry nesting."""
    # SmartThings has strict rate limits; avoid extra calls during setup.
    # Token-hash is stable for this config entry and avoids leaking secrets.
    return f"token_{_token_key(token)}"


async def _ensure_discovery_task(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start a background discovery loop for this hub entry."""
    if entry.pref_disable_new_entities:
        return

    dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    api: SmartThingsApi | None = dom.get("api")
    hub_id = dom.get("hub_id")
    if not isinstance(hub_id, str) or not hub_id:
        hub_id = f"entry_{entry.entry_id[:8]}"
    if api is None:
        return

    if not isinstance(hub_id, str) or not hub_id:
        hub_id = f"entry_{entry.entry_id[:8]}"

    opts = entry.options or {}
    discovery_interval = int(opts.get(CONF_DISCOVERY_INTERVAL, entry.data.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL)))
    include_non_samsung = bool(opts.get(CONF_INCLUDE_NON_SAMSUNG, entry.data.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG)))

    async def _loop() -> None:
        await asyncio.sleep(10)
        while True:
            try:
                devices = await api.list_devices()
                if not include_non_samsung:
                    devices = [d for d in devices if isinstance(d, dict) and _is_samsung(d)]

                current: set[str] = set()
                dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
                for it in dom.get("items") or []:
                    dev = it.get("device")
                    if dev and getattr(dev, "device_id", None):
                        current.add(dev.device_id)

                latest: set[str] = set()
                for d in devices:
                    did = d.get("deviceId")
                    if isinstance(did, str) and did:
                        latest.add(did)

                if latest - current:
                    _LOGGER.info("[%s] New devices discovered for %s; reloading entry", DOMAIN, hub_id)
                    await hass.config_entries.async_reload(entry.entry_id)
            except Exception:
                # Token may be revoked; avoid log spam.
                _LOGGER.debug("[%s] discovery scan failed for entry %s", DOMAIN, entry.entry_id, exc_info=True)

            await asyncio.sleep(max(60, discovery_interval))

    # IMPORTANT: don't block HA startup. Use a background task API if available.
    if hasattr(hass, "async_create_background_task"):
        task = hass.async_create_background_task(_loop(), name=f"{DOMAIN}_discovery_{entry.entry_id}")
    else:
        task = asyncio.create_task(_loop())

    def _cancel(_event) -> None:
        task.cancel()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _cancel)
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})["_discovery_task"] = task


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    Path(hass.config.path("FrameTV")).mkdir(parents=True, exist_ok=True)
    if not hass.data[DOMAIN].get("_frame_panel_registered"):
        hass.http.register_view(FrameTVPanelDataView(hass))
        hass.http.register_view(FrameTVPanelSetView(hass))
        hass.http.register_view(FrameTVPanelFavoriteView(hass))
        hass.http.register_view(FrameTVPanelUIView())
        hass.http.register_view(FrameTVPanelThumbView(hass))
        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="FrameTV",
            sidebar_icon="mdi:image-frame",
            frontend_url_path=FRAME_PANEL_URL_PATH,
            config={"url": "/api/samsung_smartthings/frame_tv_panel/ui"},
            require_admin=False,
        )
        hass.data[DOMAIN]["_frame_panel_registered"] = True

    # Services remain: they are useful for advanced cases and debugging.
    async def _resolve_device(call):
        # Resolve device from device_id or entity_id.
        device_id = call.data.get("device_id")
        if isinstance(device_id, str) and device_id:
            for e in hass.data.get(DOMAIN, {}).values():
                for it in e.get("items") or []:
                    dev = it.get("device")
                    if dev and dev.device_id == device_id:
                        return dev, it["coordinator"]
            raise ValueError(f"Unknown device_id: {device_id}")

        entity_ids = []
        if isinstance(call.data.get("entity_id"), str):
            entity_ids = [call.data["entity_id"]]
        elif isinstance(call.data.get("entity_id"), list):
            entity_ids = [x for x in call.data["entity_id"] if isinstance(x, str)]
        elif call.target and call.target.entity_ids:
            entity_ids = list(call.target.entity_ids)
        if not entity_ids:
            raise ValueError("Provide device_id or entity_id")

        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(hass)
        ent = reg.async_get(entity_ids[0])
        if not ent or not ent.config_entry_id:
            raise ValueError(f"Entity not found or not linked to config entry: {entity_ids[0]}")
        match = hass.data.get(DOMAIN, {}).get(ent.config_entry_id)
        if not match:
            raise ValueError(f"Config entry not loaded: {ent.config_entry_id}")
        items = match.get("items") or []
        if not items:
            raise ValueError(f"Config entry has no devices: {ent.config_entry_id}")

        # Try to match entity's HA device to a SmartThings device_id.
        if ent.device_id:
            from homeassistant.helpers import device_registry as dr

            dev_reg = dr.async_get(hass)
            ha_dev = dev_reg.async_get(ent.device_id)
            if ha_dev:
                for item in items:
                    st_did = item["device"].device_id
                    if (DOMAIN, st_did) in ha_dev.identifiers:
                        return item["device"], item["coordinator"]
        return items[0]["device"], items[0]["coordinator"]

    async def _resolve_frame_local(call):
        # Prefer explicit frame_entity_id, then generic entity_id target.
        target_entity = call.data.get("frame_entity_id") or call.data.get("entity_id")
        if isinstance(target_entity, list):
            target_entity = target_entity[0] if target_entity else None
        if not target_entity and call.target and call.target.entity_ids:
            target_entity = list(call.target.entity_ids)[0]
        if not isinstance(target_entity, str) or not target_entity:
            raise ValueError("Provide frame_entity_id or entity_id for a Frame Local entity")

        from homeassistant.helpers import entity_registry as er

        ent = er.async_get(hass).async_get(target_entity)
        if not ent or not ent.config_entry_id:
            raise ValueError(f"Entity not found: {target_entity}")
        dom = hass.data.get(DOMAIN, {}).get(ent.config_entry_id)
        if not dom or dom.get("type") != ENTRY_TYPE_FRAME_LOCAL:
            raise ValueError("Service only supports Frame Local entries")
        return ent.config_entry_id, dom["frame"], dom["coordinator"]

    async def _raw_command(call) -> None:
        dev, coordinator = await _resolve_device(call)
        component = str(call.data.get("component", "main"))
        capability = str(call.data["capability"])
        command = str(call.data["command"])
        args_json = str(call.data.get("args_json", "") or "")
        await dev.raw_command_json(component, capability, command, args_json)
        await coordinator.async_request_refresh()

    async def _play_track(call) -> None:
        dev, coordinator = await _resolve_device(call)
        uri = str(call.data["uri"])
        level = call.data.get("level")
        args = [uri] + ([int(level)] if level is not None else [])
        await dev.send_command("audioNotification", "playTrack", arguments=args)
        await coordinator.async_request_refresh()

    async def _play_track_and_restore(call) -> None:
        dev, coordinator = await _resolve_device(call)
        uri = str(call.data["uri"])
        level = call.data.get("level")
        args = [uri] + ([int(level)] if level is not None else [])
        await dev.send_command("audioNotification", "playTrackAndRestore", arguments=args)
        await coordinator.async_request_refresh()

    async def _play_track_and_resume(call) -> None:
        dev, coordinator = await _resolve_device(call)
        uri = str(call.data["uri"])
        level = call.data.get("level")
        args = [uri] + ([int(level)] if level is not None else [])
        await dev.send_command("audioNotification", "playTrackAndResume", arguments=args)
        await coordinator.async_request_refresh()

    async def _launch_app(call) -> None:
        dev, coordinator = await _resolve_device(call)
        app_id = call.data.get("app_id")
        app_name = call.data.get("app_name")
        # SmartThings command signature is (appId?, appName?).
        # If only app_name is provided, we must send [None, app_name] to avoid
        # SmartThings interpreting the single argument as appId.
        args = None
        if app_id and app_name:
            args = [str(app_id), str(app_name)]
        elif app_id:
            args = [str(app_id)]
        elif app_name:
            args = [None, str(app_name)]
        await dev.send_command("custom.launchapp", "launchApp", arguments=args if args else None)
        await coordinator.async_request_refresh()

    async def _set_art_mode(call) -> None:
        dev, coordinator = await _resolve_device(call)
        on = call.data.get("on", True)
        # only 'on' is currently supported; off is best-effort.
        if on in (True, "true", "on", 1, "1"):
            await dev.set_art_mode()
        else:
            await dev.exit_art_mode()
        await coordinator.async_request_refresh()

    async def _set_ambient_content(call) -> None:
        dev, coordinator = await _resolve_device(call)
        data_json = str(call.data["data_json"])
        await dev.raw_command_json("main", "samsungvd.ambientContent", "setAmbientContent", data_json)
        await coordinator.async_request_refresh()

    async def _set_night_mode(call) -> None:
        entity_id = call.data.get("entity_id")
        night = bool(call.data.get("night", True))
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("entity_id is required")

        from homeassistant.helpers import entity_registry as er

        ent = er.async_get(hass).async_get(entity_id)
        if not ent or not ent.config_entry_id:
            raise ValueError(f"Entity not found: {entity_id}")

        dom = hass.data.get(DOMAIN, {}).get(ent.config_entry_id)
        if not dom or dom.get("type") != ENTRY_TYPE_SOUNDBAR_LOCAL:
            raise ValueError("set_night_mode service only supports local soundbar entries")

        soundbar: AsyncSoundbarLocal = dom["soundbar"]
        coordinator = dom["coordinator"]
        await soundbar.set_night_mode(night)
        await coordinator.async_request_refresh()

    async def _frame_upload_artwork(call) -> None:
        entry_id, frame, coordinator = await _resolve_frame_local(call)
        path = str(call.data.get("path", "") or "").strip()
        if not path:
            raise ValueError("path is required")
        if not Path(path).is_file():
            raise ValueError(f"Invalid path: {path}")

        matte = str(call.data.get("matte", "shadowbox_polar") or "shadowbox_polar")
        portrait_matte = str(call.data.get("portrait_matte", matte) or matte)
        show_now = bool(call.data.get("show_now", True))
        use_border = bool(call.data.get("use_border", True))
        matte_id_raw = call.data.get("matte_id")
        matte_id = str(matte_id_raw).strip() if isinstance(matte_id_raw, str) and matte_id_raw.strip() else None
        content_id = await frame.upload_artwork(path, matte=matte, portrait_matte=portrait_matte)
        if show_now:
            await _set_frame_artwork_with_border(
                frame,
                coordinator,
                content_id,
                True,
                use_border=use_border,
                matte_id=matte_id,
            )
        await coordinator.async_request_refresh()

        # Update sync mapping for this single file.
        store: Store = hass.data.setdefault(DOMAIN, {}).setdefault(FRAME_SYNC_STORE_KEY, Store(hass, FRAME_SYNC_STORE_VERSION, FRAME_SYNC_STORE_KEY))
        payload = await store.async_load() or {}
        per_entry = payload.setdefault(entry_id, {})
        files = per_entry.setdefault("files", {})
        files[path] = {
            "content_id": content_id,
            "sha256": frame.file_sha256(path),
            "mtime": frame.file_mtime(path),
        }
        await store.async_save(payload)

    async def _frame_select_artwork(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        content_id = str(call.data.get("content_id", "") or "").strip()
        if not content_id:
            raise ValueError("content_id is required")
        show_now = bool(call.data.get("show_now", True))
        use_border = bool(call.data.get("use_border", True))
        matte_id_raw = call.data.get("matte_id")
        matte_id = str(matte_id_raw).strip() if isinstance(matte_id_raw, str) and matte_id_raw.strip() else None
        if show_now:
            await _set_frame_artwork_with_border(
                frame,
                coordinator,
                content_id,
                True,
                use_border=use_border,
                matte_id=matte_id,
            )
        else:
            await frame.select_artwork(content_id, False)
        await coordinator.async_request_refresh()

    def _resolve_frame_local_file(path_or_name: str) -> str:
        raw = str(path_or_name or "").strip()
        if not raw:
            raise ValueError("path is required")
        p = Path(raw)
        if p.is_absolute():
            target = p.resolve()
        else:
            base = Path(hass.config.path("FrameTV")).resolve()
            target = (base / raw).resolve()
            if not str(target).startswith(str(base)):
                raise ValueError("Relative path must stay inside FrameTV folder")
        if not target.is_file():
            raise ValueError(f"Invalid path: {target}")
        return str(target)

    async def _frame_set_local_file(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        path = _resolve_frame_local_file(str(call.data.get("path", "") or ""))
        show_now = bool(call.data.get("show_now", True))
        use_border = bool(call.data.get("use_border", True))
        matte_id_raw = call.data.get("matte_id")
        matte_id = str(matte_id_raw).strip() if isinstance(matte_id_raw, str) and matte_id_raw.strip() else None
        if use_border:
            content_id = await frame.upload_artwork(path)
        else:
            content_id = await frame.upload_artwork(path, matte="none", portrait_matte="none")
        await _set_frame_artwork_with_border(
            frame,
            coordinator,
            content_id,
            show_now,
            use_border=use_border,
            matte_id=matte_id,
        )
        await coordinator.async_request_refresh()

    async def _frame_set_internet_artwork(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        collection = str(call.data.get("collection", "museums") or "museums").strip().lower()
        if collection not in _INTERNET_COLLECTIONS:
            raise ValueError(f"Unknown collection: {collection}")
        random_pick = bool(call.data.get("random", True))
        if random_pick:
            idx = random.randint(1, _INTERNET_ITEMS_PER_COLLECTION)
        else:
            idx = int(call.data.get("index", 1))
            if idx < 1 or idx > _INTERNET_ITEMS_PER_COLLECTION:
                raise ValueError(f"index must be 1..{_INTERNET_ITEMS_PER_COLLECTION}")
        use_border = bool(call.data.get("use_border", True))
        matte_id_raw = call.data.get("matte_id")
        matte_id = str(matte_id_raw).strip() if isinstance(matte_id_raw, str) and matte_id_raw.strip() else None
        body = await _download_internet_image(hass, collection, idx)
        with tempfile.NamedTemporaryFile(prefix="frame_service_", suffix=".jpg", delete=False) as tmp:
            tmp.write(body)
            tmp_path = tmp.name
        try:
            if use_border:
                content_id = await frame.upload_artwork(tmp_path)
            else:
                content_id = await frame.upload_artwork(tmp_path, matte="none", portrait_matte="none")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        await _set_frame_artwork_with_border(
            frame,
            coordinator,
            content_id,
            True,
            use_border=use_border,
            matte_id=matte_id,
        )
        await coordinator.async_request_refresh()

    async def _frame_set_favorite_artwork(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        favorites = await _load_panel_favorites(hass)
        favorite_entries = list(favorites.items())
        if not favorite_entries:
            raise HomeAssistantError("No FrameTV favorites found")

        favorite_id = str(call.data.get("favorite_id", "") or "").strip()
        favorite_title = str(call.data.get("favorite_title", "") or "").strip()
        random_pick = bool(call.data.get("random", False))
        selected: dict | None = None
        if random_pick or not favorite_id:
            if favorite_title:
                for _fid, cand in favorite_entries:
                    if str(cand.get("title", "") or "").strip().lower() == favorite_title.lower():
                        selected = cand
                        break
                if selected is None:
                    raise HomeAssistantError(f"Favorite title not found: {favorite_title}")
            else:
                _fid, selected = random.choice(favorite_entries)
        else:
            selected = favorites.get(favorite_id)
        if not isinstance(selected, dict):
            raise HomeAssistantError(f"Favorite not found: {favorite_id}")

        source = str(selected.get("source", "") or "").strip().lower()
        item_id = str(selected.get("item_id", "") or "").strip()
        if source not in ("local", "internet") or not item_id:
            raise HomeAssistantError("Favorite has invalid source/item_id")

        use_border = bool(call.data.get("use_border", True))
        matte_id_raw = call.data.get("matte_id")
        matte_id = str(matte_id_raw).strip() if isinstance(matte_id_raw, str) and matte_id_raw.strip() else None

        if source == "local":
            path = _resolve_frame_local_file(item_id)
            if use_border:
                content_id = await frame.upload_artwork(path)
            else:
                content_id = await frame.upload_artwork(path, matte="none", portrait_matte="none")
        else:
            slug, _, idx_raw = item_id.partition(":")
            idx = int(idx_raw)
            body = await _download_internet_image(hass, slug, idx)
            with tempfile.NamedTemporaryFile(prefix="frame_favorite_", suffix=".jpg", delete=False) as tmp:
                tmp.write(body)
                tmp_path = tmp.name
            try:
                if use_border:
                    content_id = await frame.upload_artwork(tmp_path)
                else:
                    content_id = await frame.upload_artwork(tmp_path, matte="none", portrait_matte="none")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        await _set_frame_artwork_with_border(
            frame,
            coordinator,
            content_id,
            True,
            use_border=use_border,
            matte_id=matte_id,
        )
        await coordinator.async_request_refresh()

    async def _frame_delete_artwork(call) -> None:
        entry_id, frame, coordinator = await _resolve_frame_local(call)
        content_id = str(call.data.get("content_id", "") or "").strip()
        if not content_id:
            raise ValueError("content_id is required")
        ok = await frame.delete_artwork(content_id)
        if not ok:
            raise ValueError(f"Failed to delete content_id={content_id}")
        await coordinator.async_request_refresh()

        store: Store = hass.data.setdefault(DOMAIN, {}).setdefault(FRAME_SYNC_STORE_KEY, Store(hass, FRAME_SYNC_STORE_VERSION, FRAME_SYNC_STORE_KEY))
        payload = await store.async_load() or {}
        per_entry = payload.get(entry_id, {})
        files = per_entry.get("files", {})
        for k in list(files.keys()):
            if files.get(k, {}).get("content_id") == content_id:
                files.pop(k, None)
        await store.async_save(payload)

    async def _frame_delete_artwork_list(call) -> None:
        entry_id, frame, coordinator = await _resolve_frame_local(call)
        content_ids = call.data.get("content_ids")
        if isinstance(content_ids, str):
            content_ids = [x.strip() for x in content_ids.split(",") if x.strip()]
        if not isinstance(content_ids, list) or not content_ids:
            raise ValueError("content_ids is required")
        ids = [str(x).strip() for x in content_ids if str(x).strip()]
        ok = await frame.delete_artwork_list(ids)
        if not ok:
            raise ValueError("Delete list did not complete")
        await coordinator.async_request_refresh()

        store: Store = hass.data.setdefault(DOMAIN, {}).setdefault(FRAME_SYNC_STORE_KEY, Store(hass, FRAME_SYNC_STORE_VERSION, FRAME_SYNC_STORE_KEY))
        payload = await store.async_load() or {}
        per_entry = payload.get(entry_id, {})
        files = per_entry.get("files", {})
        for k in list(files.keys()):
            if files.get(k, {}).get("content_id") in ids:
                files.pop(k, None)
        await store.async_save(payload)

    async def _frame_sync_folder(call) -> None:
        entry_id, frame, coordinator = await _resolve_frame_local(call)
        folder = str(call.data.get("folder", "") or "").strip()
        delete_orphans = bool(call.data.get("delete_orphans", False))
        recursive = bool(call.data.get("recursive", False))
        if not folder:
            raise ValueError("folder is required")
        base = Path(folder)
        if not base.is_dir():
            raise ValueError(f"Invalid folder: {folder}")

        if recursive:
            files = [p for p in base.rglob("*") if p.is_file()]
        else:
            files = [p for p in base.iterdir() if p.is_file()]
        files = [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]

        store: Store = hass.data.setdefault(DOMAIN, {}).setdefault(FRAME_SYNC_STORE_KEY, Store(hass, FRAME_SYNC_STORE_VERSION, FRAME_SYNC_STORE_KEY))
        payload = await store.async_load() or {}
        per_entry = payload.setdefault(entry_id, {})
        mapping = per_entry.setdefault("files", {})

        local_paths: set[str] = set()
        for path in files:
            p = str(path)
            local_paths.add(p)
            sha = frame.file_sha256(p)
            mtime = frame.file_mtime(p)
            prev = mapping.get(p) if isinstance(mapping, dict) else None
            if isinstance(prev, dict) and prev.get("sha256") == sha and int(prev.get("mtime", -1)) == mtime and prev.get("content_id"):
                continue
            cid = await frame.upload_artwork(p)
            mapping[p] = {"content_id": cid, "sha256": sha, "mtime": mtime}

        if delete_orphans:
            orphan_ids: list[str] = []
            for p, meta in list(mapping.items()):
                if p in local_paths:
                    continue
                if isinstance(meta, dict) and isinstance(meta.get("content_id"), str):
                    orphan_ids.append(meta["content_id"])
                mapping.pop(p, None)
            if orphan_ids:
                await frame.delete_artwork_list(orphan_ids)

        await store.async_save(payload)
        await coordinator.async_request_refresh()

    async def _frame_set_slideshow(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        minutes = int(call.data.get("minutes", 0))
        shuffled = bool(call.data.get("shuffled", True))
        category_id = call.data.get("category_id")
        try:
            await frame.set_slideshow_status(minutes, shuffled, str(category_id) if category_id else None)
        except FrameLocalUnsupportedError as err:
            _LOGGER.info("[%s] Frame slideshow unsupported on this model: %s", DOMAIN, err)
            coordinator.data = {**(coordinator.data or {}), "supports_slideshow": False}
            await coordinator.async_request_refresh()
            return
        except FrameLocalError as err:
            _LOGGER.info("[%s] Frame slideshow unavailable on this model/firmware: %s", DOMAIN, err)
            coordinator.data = {**(coordinator.data or {}), "supports_slideshow": False}
            await coordinator.async_request_refresh()
            return
        await coordinator.async_request_refresh()

    async def _frame_set_motion_timer(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        try:
            await frame.set_motion_timer(str(call.data.get("value", "off")))
        except FrameLocalUnsupportedError as err:
            _LOGGER.info("[%s] Frame motion timer unsupported on this model: %s", DOMAIN, err)
            coordinator.data = {**(coordinator.data or {}), "supports_motion_timer": False}
            await coordinator.async_request_refresh()
            return
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set motion timer on Frame TV: {err}") from err
        await coordinator.async_request_refresh()

    async def _frame_set_motion_sensitivity(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        try:
            await frame.set_motion_sensitivity(str(call.data.get("value", "2")))
        except FrameLocalUnsupportedError as err:
            _LOGGER.info("[%s] Frame motion sensitivity unsupported on this model: %s", DOMAIN, err)
            coordinator.data = {**(coordinator.data or {}), "supports_motion_sensitivity": False}
            await coordinator.async_request_refresh()
            return
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set motion sensitivity on Frame TV: {err}") from err
        await coordinator.async_request_refresh()

    async def _frame_set_brightness_sensor(call) -> None:
        _entry_id, frame, coordinator = await _resolve_frame_local(call)
        try:
            await frame.set_brightness_sensor(bool(call.data.get("enabled", True)))
        except FrameLocalUnsupportedError as err:
            _LOGGER.info("[%s] Frame brightness sensor toggle unsupported on this model: %s", DOMAIN, err)
            coordinator.data = {**(coordinator.data or {}), "supports_brightness_sensor": False}
            await coordinator.async_request_refresh()
            return
        except FrameLocalError as err:
            raise HomeAssistantError(f"Failed to set brightness sensor on Frame TV: {err}") from err
        await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, "raw_command"):
        hass.services.async_register(DOMAIN, "raw_command", _raw_command)
    if not hass.services.has_service(DOMAIN, "play_track"):
        hass.services.async_register(DOMAIN, "play_track", _play_track)
    if not hass.services.has_service(DOMAIN, "play_track_and_restore"):
        hass.services.async_register(DOMAIN, "play_track_and_restore", _play_track_and_restore)
    if not hass.services.has_service(DOMAIN, "play_track_and_resume"):
        hass.services.async_register(DOMAIN, "play_track_and_resume", _play_track_and_resume)
    if not hass.services.has_service(DOMAIN, "launch_app"):
        hass.services.async_register(DOMAIN, "launch_app", _launch_app)
    if not hass.services.has_service(DOMAIN, "set_art_mode"):
        hass.services.async_register(DOMAIN, "set_art_mode", _set_art_mode)
    if not hass.services.has_service(DOMAIN, "set_ambient_content"):
        hass.services.async_register(DOMAIN, "set_ambient_content", _set_ambient_content)
    if not hass.services.has_service(DOMAIN, "set_night_mode"):
        hass.services.async_register(DOMAIN, "set_night_mode", _set_night_mode)
    if not hass.services.has_service(DOMAIN, "frame_upload_artwork"):
        hass.services.async_register(DOMAIN, "frame_upload_artwork", _frame_upload_artwork)
    if not hass.services.has_service(DOMAIN, "frame_select_artwork"):
        hass.services.async_register(DOMAIN, "frame_select_artwork", _frame_select_artwork)
    if not hass.services.has_service(DOMAIN, "frame_delete_artwork"):
        hass.services.async_register(DOMAIN, "frame_delete_artwork", _frame_delete_artwork)
    if not hass.services.has_service(DOMAIN, "frame_delete_artwork_list"):
        hass.services.async_register(DOMAIN, "frame_delete_artwork_list", _frame_delete_artwork_list)
    if not hass.services.has_service(DOMAIN, "frame_sync_folder"):
        hass.services.async_register(DOMAIN, "frame_sync_folder", _frame_sync_folder)
    if not hass.services.has_service(DOMAIN, "frame_set_slideshow"):
        hass.services.async_register(DOMAIN, "frame_set_slideshow", _frame_set_slideshow)
    if not hass.services.has_service(DOMAIN, "frame_set_motion_timer"):
        hass.services.async_register(DOMAIN, "frame_set_motion_timer", _frame_set_motion_timer)
    if not hass.services.has_service(DOMAIN, "frame_set_motion_sensitivity"):
        hass.services.async_register(DOMAIN, "frame_set_motion_sensitivity", _frame_set_motion_sensitivity)
    if not hass.services.has_service(DOMAIN, "frame_set_brightness_sensor"):
        hass.services.async_register(DOMAIN, "frame_set_brightness_sensor", _frame_set_brightness_sensor)
    if not hass.services.has_service(DOMAIN, "frame_set_local_file"):
        hass.services.async_register(DOMAIN, "frame_set_local_file", _frame_set_local_file)
    if not hass.services.has_service(DOMAIN, "frame_set_internet_artwork"):
        hass.services.async_register(DOMAIN, "frame_set_internet_artwork", _frame_set_internet_artwork)
    if not hass.services.has_service(DOMAIN, "frame_set_favorite_artwork"):
        hass.services.async_register(DOMAIN, "frame_set_favorite_artwork", _frame_set_favorite_artwork)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_ENTRY_TYPE)

    # ---- Frame TV Local (LAN Art API) entry type ----
    if entry_type == ENTRY_TYPE_FRAME_LOCAL:
        host = entry.data.get(CONF_HOST_LOCAL) or entry.data.get(CONF_HOST)
        if not isinstance(host, str) or not host:
            raise ConfigEntryNotReady("Missing host for local Frame entry")

        ws_port = int(entry.data.get(CONF_WS_PORT, DEFAULT_FRAME_WS_PORT))
        try:
            timeout = int(entry.data.get(CONF_TIMEOUT, DEFAULT_FRAME_TIMEOUT))
        except Exception:
            timeout = DEFAULT_FRAME_TIMEOUT
        # Keep local Frame websocket timeout sane even for legacy entries that
        # may carry an old too-low slider value.
        timeout = max(DEFAULT_FRAME_TIMEOUT, timeout)
        ws_name = str(entry.data.get(CONF_WS_NAME, DEFAULT_FRAME_WS_NAME) or DEFAULT_FRAME_WS_NAME).strip() or DEFAULT_FRAME_WS_NAME
        token_dir = Path(hass.config.path(".storage", f"{DOMAIN}_frame_tokens"))
        token_dir.mkdir(parents=True, exist_ok=True)
        token_file = str(token_dir / f"{entry.entry_id}.token")

        frame = AsyncFrameLocal(
            hass,
            host=host,
            ws_port=ws_port,
            timeout=timeout,
            ws_name=ws_name,
            token_file=token_file,
        )

        async def _update() -> dict:
            try:
                return await frame.get_state()
            except FrameLocalError as err:
                raise UpdateFailed(err) from err
            except Exception as err:
                raise UpdateFailed(str(err)) from err

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_frame_local_{host}",
            update_method=_update,
            update_interval=timedelta(seconds=DEFAULT_LOCAL_FRAME_POLL_INTERVAL),
        )
        coordinator.data = {"online": False, "last_errors": ["not_initialized"]}
        try:
            await coordinator.async_refresh()
        except Exception:
            _LOGGER.debug("[%s] frame local first refresh failed for %s", DOMAIN, host, exc_info=True)

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "type": ENTRY_TYPE_FRAME_LOCAL,
            "host": host,
            "frame": frame,
            "coordinator": coordinator,
        }

        await hass.config_entries.async_forward_entry_setups(entry, ["media_player", "switch", "select", "number", "sensor"])
        return True

    # ---- Soundbar Local (LAN) entry type ----
    is_legacy_soundbar_entry = (
        entry_type is None
        and isinstance(entry.data.get(CONF_HOST_LOCAL) or entry.data.get(CONF_HOST), str)
        and CONF_VERIFY_SSL in entry.data
    )
    if entry_type == ENTRY_TYPE_SOUNDBAR_LOCAL or is_legacy_soundbar_entry:
        host = entry.data.get(CONF_HOST_LOCAL) or entry.data.get(CONF_HOST)
        if not isinstance(host, str) or not host:
            raise ConfigEntryNotReady("Missing host for local soundbar entry")

        verify_ssl = bool(entry.data.get(CONF_VERIFY_SSL, False))
        session = aiohttp_client.async_create_clientsession(hass, verify_ssl=verify_ssl)
        soundbar = AsyncSoundbarLocal(host=host, session=session, verify_ssl=verify_ssl)

        async def _update() -> dict:
            try:
                return await soundbar.status()
            except SoundbarLocalError as err:
                raise UpdateFailed(err) from err

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_soundbar_local_{host}",
            update_method=_update,
            update_interval=timedelta(seconds=DEFAULT_LOCAL_SOUNDBAR_POLL_INTERVAL),
        )
        await coordinator.async_config_entry_first_refresh()

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "type": ENTRY_TYPE_SOUNDBAR_LOCAL,
            "host": host,
            "soundbar": soundbar,
            "coordinator": coordinator,
        }

        # Local mode supports a curated subset of entities on top of media_player.
        await hass.config_entries.async_forward_entry_setups(entry, ["media_player", "sensor", "switch", "select", "button"])
        return True

    st_entry_id = entry.data.get(CONF_SMARTTHINGS_ENTRY_ID)
    pat_token = entry.data.get(CONF_PAT_TOKEN)
    oauth_token = entry.data.get(OAUTH2_TOKEN_KEY)
    if not isinstance(st_entry_id, str) or not st_entry_id:
        st_entry_id = None
    if not isinstance(pat_token, str) or not pat_token:
        pat_token = None
    if not isinstance(oauth_token, dict):
        oauth_token = None

    if not st_entry_id and not pat_token and not oauth_token:
        _LOGGER.error("[%s] Missing auth in config entry %s", DOMAIN, entry.entry_id)
        return False

    # Migrate settings from entry.data into entry.options; keep entry.data auth-only.
    settings_keys = (
        CONF_EXPOSE_ALL,
        CONF_SCAN_INTERVAL,
        CONF_DISCOVERY_INTERVAL,
        CONF_INCLUDE_NON_SAMSUNG,
        CONF_MANAGE_DIAGNOSTICS,
    )
    new_opts = dict(entry.options or {})
    moved = False
    for k in settings_keys:
        if k in entry.data and k not in new_opts:
            new_opts[k] = entry.data.get(k)
            moved = True
    new_data = dict(entry.data)
    for k in settings_keys:
        new_data.pop(k, None)
    if CONF_ENTRY_TYPE not in new_data:
        new_data[CONF_ENTRY_TYPE] = ENTRY_TYPE_CLOUD
    if moved or new_data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=new_data, options=new_opts)

    opts = new_opts or entry.options or {}
    expose_all = bool(opts.get(CONF_EXPOSE_ALL, DEFAULT_EXPOSE_ALL))
    raw_scan_interval = int(opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    # SmartThings cloud rate-limits aggressively; keep a safe floor.
    scan_interval = max(30, raw_scan_interval)
    if scan_interval != raw_scan_interval:
        new_opts = dict(opts)
        new_opts[CONF_SCAN_INTERVAL] = scan_interval
        hass.config_entries.async_update_entry(entry, options=new_opts)
    include_non_samsung = bool(opts.get(CONF_INCLUDE_NON_SAMSUNG, DEFAULT_INCLUDE_NON_SAMSUNG))
    manage_diagnostics = bool(opts.get(CONF_MANAGE_DIAGNOSTICS, DEFAULT_MANAGE_DIAGNOSTICS))
    cloud_soundmodes_raw = str(opts.get(CONF_CLOUD_SOUNDMODES, DEFAULT_CLOUD_SOUNDMODES) or "").strip()
    cloud_soundmodes = [s.strip() for s in cloud_soundmodes_raw.split(",") if s.strip()]

    oauth_session = None
    auth_is_oauth = False
    if st_entry_id is not None:
        # Recommended mode: reuse the built-in Home Assistant SmartThings integration's OAuth session.
        st_entry = hass.config_entries.async_get_entry(st_entry_id)
        if st_entry is None:
            _LOGGER.error("[%s] Linked SmartThings config entry not found: %s", DOMAIN, st_entry_id)
            return False

        impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, st_entry)
        oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, st_entry, impl)
        api = SmartThingsApi(hass, oauth_session=oauth_session, lock_key=st_entry.entry_id)
        token = st_entry.data.get(OAUTH2_TOKEN_KEY)
        installed_app_id = token.get("installed_app_id") if isinstance(token, dict) else None
        hub_id = (
            f"oauth_{installed_app_id}"
            if isinstance(installed_app_id, str) and installed_app_id
            else f"oauth_{st_entry.entry_id[:8]}"
        )
        auth_is_oauth = True
    elif oauth_token is not None:
        # Advanced mode: OAuth2 with user-provided Application Credentials.
        impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
        oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, impl)
        api = SmartThingsApi(hass, oauth_session=oauth_session, lock_key=entry.entry_id)
        installed_app_id = oauth_token.get("installed_app_id")
        hub_id = (
            f"oauth_{installed_app_id}"
            if isinstance(installed_app_id, str) and installed_app_id
            else f"oauth_{entry.entry_id[:8]}"
        )
        auth_is_oauth = True
    else:
        # Fallback: PAT token.
        api = SmartThingsApi(hass, pat_token=pat_token, lock_key=entry.entry_id)
        hub_id = await _get_hub_id(api, pat_token)

    # Create a hub device to nest all SmartThings devices under it.
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_id)},
        name=f"Samsung SmartThings (Cloud) ({hub_id.split('_', 1)[-1][:8]})",
        manufacturer="Samsung",
        model="SmartThings Cloud",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    try:
        devices = await api.list_devices()
    except ClientResponseError as exc:
        if exc.status == 401:
            if auth_is_oauth:
                raise ConfigEntryAuthFailed(
                    "SmartThings authorization expired. Re-authenticate the Home Assistant SmartThings integration."
                ) from exc
            raise ConfigEntryAuthFailed("Invalid SmartThings PAT token") from exc
        raise ConfigEntryNotReady(f"SmartThings API error {exc.status}") from exc
    except Exception as exc:
        raise ConfigEntryNotReady("SmartThings API not reachable") from exc
    if not include_non_samsung:
        devices = [d for d in devices if isinstance(d, dict) and _is_samsung(d)]

    # Keep deterministic order (name then deviceId), so entity_id churn is minimized.
    def _sort_key(d: dict) -> tuple[str, str]:
        label = d.get("label") or d.get("name") or ""
        did = d.get("deviceId") or ""
        return (str(label).lower(), str(did))

    devices = [d for d in devices if isinstance(d, dict) and isinstance(d.get("deviceId"), str)]
    devices.sort(key=_sort_key)

    items: list[dict] = []
    for d in devices:
        did = d.get("deviceId")
        if not isinstance(did, str) or not did:
            continue
        # Use the already-fetched device payload to avoid extra per-device API calls.
        dev = SmartThingsDevice(
            api,
            did,
            expose_all=expose_all,
            device=d,
            cloud_soundmodes=cloud_soundmodes,
        )
        await dev.async_init()

        coordinator = SmartThingsCoordinator(hass, dev, hub_id=hub_id, scan_interval=scan_interval)
        # Don't block setup on initial refresh (SmartThings rate-limits hard).
        if hasattr(hass, "async_create_background_task"):
            hass.async_create_background_task(
                coordinator.async_config_entry_first_refresh(),
                name=f"{DOMAIN}_first_refresh_{did}",
            )
        else:
            hass.async_create_task(coordinator.async_config_entry_first_refresh())
        items.append({"device": dev, "coordinator": coordinator})

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"type": "cloud", "api": api, "hub_id": hub_id, "items": items}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Auto-discovery: reload entry if new devices appear.
    # Defer until HA is started so this doesn't get treated as a startup task.
    async def _start_discovery(_ev) -> None:
        await _ensure_discovery_task(hass, entry)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_discovery)

    # One-shot cleanup to reduce clutter: hide/disable diagnostic entities by default.
    if manage_diagnostics:
        hass.async_create_task(_hide_disable_diagnostics(hass, entry))

    # Reload on options changes.
    entry.async_on_unload(entry.add_update_listener(_update_listener))

    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_type = entry.data.get(CONF_ENTRY_TYPE)
    is_legacy_soundbar_entry = (
        entry_type is None
        and isinstance(entry.data.get(CONF_HOST_LOCAL) or entry.data.get(CONF_HOST), str)
        and CONF_VERIFY_SSL in entry.data
    )
    if entry_type == ENTRY_TYPE_SOUNDBAR_LOCAL or is_legacy_soundbar_entry:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, ["media_player", "sensor", "switch", "select", "button"])
        if unload_ok:
            hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
            if not hass.data.get(DOMAIN):
                hass.data.pop(DOMAIN, None)
        return unload_ok
    if entry_type == ENTRY_TYPE_FRAME_LOCAL:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, ["media_player", "switch", "select", "number", "sensor"])
        if unload_ok:
            hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
            if not hass.data.get(DOMAIN):
                hass.data.pop(DOMAIN, None)
        return unload_ok

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    task = dom.get("_discovery_task")
    if task:
        try:
            task.cancel()
        except Exception:
            pass

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return True


async def _hide_disable_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hide/disable known-noisy entities for this entry (diagnostics + expose_all noise)."""
    # Persisted one-shot marker: after the first cleanup, never re-apply on restart.
    if bool((entry.options or {}).get("_diagnostic_cleanup_done", False)):
        return

    # Only run once per HA runtime for this entry. Otherwise a config-entry reload can
    # re-disable entities the user explicitly enabled.
    dom = hass.data.setdefault(DOMAIN, {})
    done = dom.setdefault("_diagnostic_cleanup_done", set())
    if entry.entry_id in done:
        return

    # Allow platforms to create entities first.
    await asyncio.sleep(30)
    try:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(reg, entry.entry_id)
        _LOGGER.info("[%s] Diagnostics cleanup: %s entities for entry %s", DOMAIN, len(entries), entry.entry_id)

        # Heuristic based on unique_id patterns created by this integration.
        # We keep core controls visible (power switch, media player, remote, primary selects).
        noisy_tokens = (
            "_attr_",  # generic attribute sensors
            "_cmd_",  # generic no-arg command buttons
            "_switch_sb_",  # execute-based soundbar toggles
            "_select_sb_",  # execute-based soundbar selects
            "_number_",  # diagnostic numbers (raw/advanced)
        )

        updated = 0
        for e in entries:
            if e.platform != DOMAIN:
                continue
            uid = e.unique_id or ""
            # Keep the soundbar volume slider enabled/visible by default.
            if uid.endswith("_number_volume"):
                continue
            if not any(t in uid for t in noisy_tokens):
                continue
            # Don't override explicit user choices.
            if e.disabled_by in (er.RegistryEntryDisabler.USER,):
                continue
            if e.hidden_by in (er.RegistryEntryHider.USER,):
                continue
            # Apply only when still visible/enabled.
            updates = {}
            if e.hidden_by is None:
                updates["hidden_by"] = er.RegistryEntryHider.INTEGRATION
            if e.disabled_by is None:
                updates["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
            if updates:
                reg.async_update_entity(e.entity_id, **updates)
                updated += 1

        # Remove legacy "generic command" entities (they were noisy and often 4xx).
        # These used unique_ids containing "_cmd_" in earlier versions of this integration.
        for e in list(entries):
            if e.platform != DOMAIN:
                continue
            uid = e.unique_id or ""
            if "_cmd_" not in uid:
                continue
            # Don't override explicit user choices.
            if e.disabled_by in (er.RegistryEntryDisabler.USER,):
                continue
            if e.hidden_by in (er.RegistryEntryHider.USER,):
                continue
            try:
                reg.async_remove(e.entity_id)
                updated += 1
            except Exception:
                pass

        # Frame TVs often expose Ambient/Art capabilities but SmartThings may not actually
        # support the command for a given device/account. We removed the old ambient buttons
        # and replaced them with a best-effort Art Mode button. Remove the legacy entities
        # from the registry so they don't linger forever.
        dom = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        for it in dom.get("items") or []:
            dev = it.get("device")
            if not dev:
                continue
            for suffix in ("ambient_on", "ambient18_on"):
                uid = f"{dev.device_id}_{suffix}"
                for e in entries:
                    if e.platform != DOMAIN:
                        continue
                    if (e.unique_id or "") != uid:
                        continue
                    # Don't override explicit user choices, but removing legacy entities is safe.
                    if e.disabled_by in (er.RegistryEntryDisabler.USER,):
                        continue
                    if e.hidden_by in (er.RegistryEntryHider.USER,):
                        continue
                    try:
                        reg.async_remove(e.entity_id)
                        updated += 1
                    except Exception:
                        # Fallback: at least hide+disable.
                        updates = {}
                        if e.hidden_by is None:
                            updates["hidden_by"] = er.RegistryEntryHider.INTEGRATION
                        if e.disabled_by is None:
                            updates["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
                        if updates:
                            reg.async_update_entity(e.entity_id, **updates)
                            updated += 1
        _LOGGER.info("[%s] Diagnostics cleanup complete: updated=%s", DOMAIN, updated)
        done.add(entry.entry_id)
        # Persist one-shot marker so user-enabled entities stay enabled across restarts.
        new_opts = dict(entry.options or {})
        if not bool(new_opts.get("_diagnostic_cleanup_done", False)):
            new_opts["_diagnostic_cleanup_done"] = True
            hass.config_entries.async_update_entry(entry, options=new_opts)
    except Exception:
        _LOGGER.warning("[%s] diagnostics cleanup failed for entry %s", DOMAIN, entry.entry_id, exc_info=True)
