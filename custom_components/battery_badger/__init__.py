"""The Battery Badger integration."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

from .api import BatteryBadgerClient
from .const import (
    CARD_URL,
    CONF_API_TOKEN,
    CONF_SERVER_URL,
    CONF_SOC_ENTITY,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import BatteryBadgerCoordinator

_INITIAL_REFRESH_TIMEOUT_S = 60

_LOGGER = logging.getLogger(__name__)

_FRONTEND_REGISTERED_KEY = "battery_badger_frontend_registered"
_SERVICES_REGISTERED_KEY = "battery_badger_services_registered"
SERVICE_REFRESH_NOW = "refresh_now"


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
    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _e: coordinator.async_cancel())
    )

    # Seed the schedule rather than waiting up to 30 minutes for the next
    # reading tick. The reading POST reads the SOC / consumption / solar
    # entities live, so we can't fire it until those entities have a
    # numeric state — the `homeassistant_started` event is necessary but
    # not sufficient for MQTT-backed sensors, which take an extra moment
    # past startup to publish their first value.
    async def _initial_refresh(_event: object | None = None) -> None:
        soc_entity = coordinator._config.get(CONF_SOC_ENTITY)
        if soc_entity and not await _wait_for_numeric_state(
            hass, soc_entity, _INITIAL_REFRESH_TIMEOUT_S
        ):
            _LOGGER.debug(
                "SOC entity %s did not become numeric within %ss; deferring "
                "initial refresh to next reading tick",
                soc_entity,
                _INITIAL_REFRESH_TIMEOUT_S,
            )
            return
        try:
            await coordinator.async_refresh_now()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "initial refresh failed; next reading tick will retry: %s", exc
            )

    if hass.is_running:
        # Options-change reload (or any other mid-run setup): MQTT sensors
        # are already populated, so fire immediately as a background task.
        entry.async_create_background_task(
            hass, _initial_refresh(), name=f"{DOMAIN}-initial-refresh"
        )
    else:
        # Cold boot: wait for the started event. The listener auto-removes
        # if the entry is unloaded before HA finishes starting.
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _initial_refresh)
        )
    return True


def _is_numeric(value: str | None) -> bool:
    if value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


async def _wait_for_numeric_state(
    hass: HomeAssistant, entity_id: str, timeout: float
) -> bool:
    """Resolve once ``entity_id`` reports a numeric state, or return False on timeout."""
    state = hass.states.get(entity_id)
    if state is not None and _is_numeric(state.state):
        return True

    ready = asyncio.Event()

    @callback
    def _on_change(event) -> None:
        new_state = event.data.get("new_state")
        if new_state is not None and _is_numeric(new_state.state):
            ready.set()

    unsub = async_track_state_change_event(hass, [entity_id], _on_change)
    try:
        await asyncio.wait_for(ready.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        unsub()


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


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's domain services exactly once."""
    if hass.data.get(_SERVICES_REGISTERED_KEY):
        return

    async def _refresh_now(_call: ServiceCall) -> None:
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            return
        await asyncio.gather(
            *(c.async_refresh_now() for c in coordinators),
            return_exceptions=True,
        )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_NOW, _refresh_now)
    hass.data[_SERVICES_REGISTERED_KEY] = True


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
