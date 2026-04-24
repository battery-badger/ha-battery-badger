"""The Battery Badger integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BatteryBadgerClient
from .const import CARD_URL, CONF_API_TOKEN, CONF_SERVER_URL, DOMAIN, PLATFORMS
from .coordinator import BatteryBadgerCoordinator

_LOGGER = logging.getLogger(__name__)

_FRONTEND_REGISTERED_KEY = "battery_badger_frontend_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Badger from a config entry."""
    session = async_get_clientsession(hass)
    client = BatteryBadgerClient(
        session=session,
        server_url=entry.data[CONF_SERVER_URL],
        api_token=entry.data[CONF_API_TOKEN],
    )

    coordinator = BatteryBadgerCoordinator(hass, entry, client)
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _async_register_frontend(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _e: coordinator.async_cancel())
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: BatteryBadgerCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator:
        coordinator.async_cancel()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register the static path for the Lovelace card exactly once."""
    if hass.data.get(_FRONTEND_REGISTERED_KEY):
        return
    card_dir = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig("/battery_badger_static", str(card_dir), cache_headers=False)]
    )
    # Auto-register the card as a Lovelace resource so users don't have to
    # add it manually under Resources.
    try:
        lovelace = hass.data.get("lovelace")
        if lovelace and hasattr(lovelace, "resources"):
            resources = lovelace.resources
            await resources.async_load()
            if not any(r.get("url") == CARD_URL for r in resources.async_items()):
                await resources.async_create_item(
                    {"res_type": "module", "url": CARD_URL}
                )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Skipping Lovelace resource auto-register: %s", exc)
    hass.data[_FRONTEND_REGISTERED_KEY] = True
