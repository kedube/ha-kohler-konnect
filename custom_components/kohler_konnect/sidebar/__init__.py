"""The Kohler Konnect sidebar panel: putting it in the sidebar, and taking it out.

The panel is the front end in `frontend/`, built into `www/kohler-konnect-panel.js`. It shows
while any enabled entry has the sidebar option on (the default), whether or not that entry
has loaded, so a reload or a setup retry never makes the panel blink.

Elsewhere in this integration "panel" means the Anthem touchscreen, hence "sidebar".
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util.hass_dict import HassKey

from ..const import CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL, DOMAIN
from .commands import async_register_commands

__all__ = ["async_setup_sidebar", "async_sync_sidebar"]

_LOGGER = logging.getLogger(__name__)

URL_PATH = "kohler-konnect"
ELEMENT = "kohler-konnect-panel"
STATIC_URL = "/kohler_konnect_static/kohler-konnect-panel.js"
BUNDLE = Path(__file__).parent.parent / "www" / "kohler-konnect-panel.js"
TITLE = "Kohler Konnect"
ICON = "mdi:shower-head"


@dataclass
class SidebarState:
    """The sidebar's own bookkeeping; never in `hass.data[DOMAIN]`, which holds coordinators."""

    # A digest of the bundle: it versions the panel's URL, so browsers fetch a new build.
    version: str | None = None
    served: bool = False
    shown: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


DATA_SIDEBAR: HassKey[SidebarState] = HassKey(f"{DOMAIN}_sidebar")


def _bundle_version() -> str | None:
    try:
        return hashlib.sha256(BUNDLE.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


async def async_setup_sidebar(hass: HomeAssistant) -> None:
    """Once per Home Assistant run: the websocket API, the bundle's URL, and the sync."""
    state = hass.data[DATA_SIDEBAR] = SidebarState()
    async_register_commands(hass)
    state.version = await hass.async_add_executor_job(_bundle_version)
    if state.version is None:
        _LOGGER.warning("The sidebar panel's bundle is missing: %s", BUNDLE)
    elif hass.http is not None:
        # Served once per run: aiohttp cannot remove a route. Cached freely, since the URL
        # changes with every build.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(BUNDLE), cache_headers=True)]
        )
        state.served = True

    @callback
    def _entry_changed(_change: Any, entry: Any) -> None:
        if entry.domain == DOMAIN:
            hass.async_create_task(async_sync_sidebar(hass))

    # For the rest of the run: entries are added, removed, disabled and re-optioned
    # while it lasts.
    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed)
    await async_sync_sidebar(hass)


def _wanted(hass: HomeAssistant) -> bool:
    return any(
        entry.disabled_by is None
        and entry.options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL)
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def async_sync_sidebar(hass: HomeAssistant) -> None:
    """Show the panel if an entry wants it, remove it if none does; else change nothing.

    Idempotent: the config-entry signal also fires for writes that change nothing here,
    such as a rotated refresh token.
    """
    state = hass.data.get(DATA_SIDEBAR)
    if state is None or not state.served:
        return
    async with state.lock:
        wanted = _wanted(hass)
        if wanted == state.shown:
            return
        if wanted:
            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=URL_PATH,
                webcomponent_name=ELEMENT,
                sidebar_title=TITLE,
                sidebar_icon=ICON,
                module_url=f"{STATIC_URL}?v={state.version}",
                config={"version": state.version},
                require_admin=False,
            )
        else:
            frontend.async_remove_panel(hass, URL_PATH, warn_if_unknown=False)
        state.shown = wanted
