"""Config flow for the Battery Badger integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BatteryBadgerApiError,
    BatteryBadgerAuthError,
    BatteryBadgerClient,
)
from .const import (
    CONF_API_TOKEN,
    CONF_CONSUMPTION_ENTITIES,
    CONF_INSTALLATION_ID,
    CONF_INSTALLATION_NAME,
    CONF_INVERTER_CONTROL_ENTITY,
    CONF_SERVER_URL,
    CONF_SOC_ENTITY,
    CONF_SOLAR_ENTITIES,
    DEFAULT_SERVER_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _energy_defaults(hass) -> dict[str, Any]:
    """Pull default entity ids out of the Energy dashboard config if present.

    Silent no-op if the energy component isn't configured — we don't want to
    error out on fresh installs that haven't set up the energy dashboard yet.
    Also searches the entity registry for a battery-SOC sensor so the SOC
    picker isn't left blank when energy is configured (the Energy dashboard
    tracks battery energy in/out, not SOC).
    """
    defaults: dict[str, Any] = {
        CONF_SOC_ENTITY: None,
        CONF_CONSUMPTION_ENTITIES: [],
        CONF_SOLAR_ENTITIES: [],
    }
    consumption: list[str] = []
    solar: list[str] = []
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
        prefs = getattr(manager, "data", None) or {}
        for src in prefs.get("energy_sources", []):
            src_type = src.get("type")
            if src_type == "grid":
                for flow in src.get("flow_from", []) or []:
                    stat = flow.get("stat_energy_from")
                    if stat:
                        consumption.append(stat)
                # Grid config can also carry a single stat_energy_from on
                # older schemas — grab that too if present.
                stat = src.get("stat_energy_from")
                if stat and stat not in consumption:
                    consumption.append(stat)
            elif src_type == "solar":
                stat = src.get("stat_energy_from")
                if stat:
                    solar.append(stat)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("energy dashboard probe failed: %s", exc)

    # First battery-device-class sensor wins.
    soc_entity = None
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") == "battery" and (
            state.attributes.get("unit_of_measurement") in ("%", "percent")
        ):
            soc_entity = state.entity_id
            break

    defaults[CONF_CONSUMPTION_ENTITIES] = consumption
    defaults[CONF_SOLAR_ENTITIES] = solar
    defaults[CONF_SOC_ENTITY] = soc_entity
    return defaults


class BatteryBadgerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Battery Badger."""

    VERSION = 1

    def __init__(self) -> None:
        self._server_url: str | None = None
        self._api_token: str | None = None
        self._installations: list[dict] = []
        self._installation_id: int | None = None
        self._installation_name: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = BatteryBadgerClient(
                session=session,
                server_url=user_input[CONF_SERVER_URL],
                api_token=user_input[CONF_API_TOKEN],
            )
            try:
                await client.whoami()
                installations = await client.list_installations()
            except BatteryBadgerAuthError:
                errors["base"] = "invalid_auth"
            except BatteryBadgerApiError as exc:
                _LOGGER.warning("Battery Badger setup failed: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                if not installations:
                    errors["base"] = "no_installations"
                else:
                    self._server_url = user_input[CONF_SERVER_URL]
                    self._api_token = user_input[CONF_API_TOKEN]
                    self._installations = installations
                    return await self.async_step_installation()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_SERVER_URL, default=DEFAULT_SERVER_URL): str,
                vol.Required(CONF_API_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }),
            errors=errors,
        )

    async def async_step_installation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._installation_id = int(user_input[CONF_INSTALLATION_ID])
            self._installation_name = next(
                (i["name"] for i in self._installations if i["id"] == self._installation_id),
                f"Installation {self._installation_id}",
            )
            # Prevent adding the same installation twice.
            await self.async_set_unique_id(f"{self._server_url}:{self._installation_id}")
            self._abort_if_unique_id_configured()
            return await self.async_step_entities()

        options = [
            {"value": str(i["id"]), "label": i["name"]}
            for i in self._installations
        ]
        return self.async_show_form(
            step_id="installation",
            data_schema=vol.Schema({
                vol.Required(CONF_INSTALLATION_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            data = {
                CONF_SERVER_URL: self._server_url,
                CONF_API_TOKEN: self._api_token,
                CONF_INSTALLATION_ID: self._installation_id,
                CONF_INSTALLATION_NAME: self._installation_name,
                CONF_SOC_ENTITY: user_input[CONF_SOC_ENTITY],
                CONF_CONSUMPTION_ENTITIES: user_input[CONF_CONSUMPTION_ENTITIES],
                CONF_SOLAR_ENTITIES: user_input[CONF_SOLAR_ENTITIES],
                CONF_INVERTER_CONTROL_ENTITY: user_input[CONF_INVERTER_CONTROL_ENTITY],
            }
            return self.async_create_entry(
                title=f"Battery Badger — {self._installation_name}",
                data=data,
            )

        defaults = await _energy_defaults(self.hass)
        return self.async_show_form(
            step_id="entities",
            data_schema=_entities_schema(defaults),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> "OptionsFlow":
        return OptionsFlow(entry)


def _entities_schema(defaults: dict[str, Any]) -> vol.Schema:
    soc_default = defaults.get(CONF_SOC_ENTITY)
    consumption_default = defaults.get(CONF_CONSUMPTION_ENTITIES, [])
    solar_default = defaults.get(CONF_SOLAR_ENTITIES, [])

    fields: dict[Any, Any] = {}
    if soc_default:
        fields[vol.Required(CONF_SOC_ENTITY, default=soc_default)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
    else:
        fields[vol.Required(CONF_SOC_ENTITY)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )

    fields[vol.Required(CONF_CONSUMPTION_ENTITIES, default=consumption_default)] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", multiple=True)
    )
    fields[vol.Required(CONF_SOLAR_ENTITIES, default=solar_default)] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", multiple=True)
    )
    fields[vol.Required(CONF_INVERTER_CONTROL_ENTITY)] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["select", "input_select"])
    )
    return vol.Schema(fields)


class OptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Use current values as defaults.
        current = {**self._entry.data, **(self._entry.options or {})}
        defaults = {
            CONF_SOC_ENTITY: current.get(CONF_SOC_ENTITY),
            CONF_CONSUMPTION_ENTITIES: current.get(CONF_CONSUMPTION_ENTITIES, []),
            CONF_SOLAR_ENTITIES: current.get(CONF_SOLAR_ENTITIES, []),
        }
        schema = _entities_schema(defaults)
        # Also allow re-picking the inverter control entity with its existing value.
        schema = schema.extend({
            vol.Required(
                CONF_INVERTER_CONTROL_ENTITY,
                default=current.get(CONF_INVERTER_CONTROL_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "input_select"])
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
