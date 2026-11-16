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
    CONF_MODE_MAP,
    CONF_SERVER_URL,
    CONF_SOC_ENTITY,
    CONF_SOLAR_ENTITIES,
    DEFAULT_SERVER_URL,
    DOMAIN,
)
from .modes import (
    ACTIONS,
    CHOOSE,
    CONTROL_OFF,
    clean_options,
    form_mode_map,
    mode_map_from_form,
    unchosen_actions,
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
        self._entry_data: dict[str, Any] = {}

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
            description_placeholders={
                "example_url": DEFAULT_SERVER_URL,
                "tokens_path": "/account/tokens",
            },
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
            self._entry_data = {
                CONF_SERVER_URL: self._server_url,
                CONF_API_TOKEN: self._api_token,
                CONF_INSTALLATION_ID: self._installation_id,
                CONF_INSTALLATION_NAME: self._installation_name,
                CONF_SOC_ENTITY: user_input[CONF_SOC_ENTITY],
                CONF_CONSUMPTION_ENTITIES: user_input[CONF_CONSUMPTION_ENTITIES],
                CONF_SOLAR_ENTITIES: user_input[CONF_SOLAR_ENTITIES],
                CONF_INVERTER_CONTROL_ENTITY: user_input[CONF_INVERTER_CONTROL_ENTITY],
            }
            return await self.async_step_modes()

        defaults = await _energy_defaults(self.hass)
        return self.async_show_form(
            step_id="entities",
            data_schema=_entities_schema(defaults),
        )

    async def async_step_modes(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        control_entity = self._entry_data[CONF_INVERTER_CONTROL_ENTITY]
        if user_input is not None:
            errors = _choice_errors(self.hass, control_entity, user_input)
            if errors:
                return _show_modes_form(
                    self, control_entity, _reshow_map(user_input), errors
                )
            # Stored in the entry data here: the entry doesn't exist yet, and
            # ``options=`` on async_create_entry isn't available on every
            # supported HA version. The coordinator merges data + options and
            # later edits land in options, which win.
            data = {
                **self._entry_data,
                CONF_MODE_MAP: _mode_map_from_input(user_input),
            }
            return self.async_create_entry(
                title=f"Battery Badger — {self._installation_name}",
                data=data,
            )
        return _show_modes_form(self, control_entity, None)

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


NOT_SUPPORTED = "__not_supported__"
DO_NOTHING = "__do_nothing__"


def _control_entity_options(hass, entity_id: str | None) -> list[str] | None:
    """The control entity's current options, or None if they can't be read."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return clean_options(state.attributes.get("options"))


def _mode_map_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    return mode_map_from_form(
        user_input, not_supported=NOT_SUPPORTED, do_nothing=DO_NOTHING
    )


def _reshow_map(user_input: dict[str, Any]) -> dict[str, Any]:
    """The user's submitted selections as a mapping to pre-fill a re-shown form.

    Placeholders and sentinels become ``None`` (unchosen / not supported / do
    nothing); real picks are kept.
    """
    result: dict[str, Any] = {}
    for key in (*ACTIONS, CONTROL_OFF):
        raw = user_input.get(key.lower())
        if isinstance(raw, str):
            raw = raw.strip()
        result[key] = None if raw in (None, "", CHOOSE, NOT_SUPPORTED, DO_NOTHING) else raw
    return result


def _choice_errors(hass, control_entity: str, user_input: dict[str, Any]) -> dict[str, str]:
    """Per-field errors for required actions still on the placeholder."""
    if _control_entity_options(hass, control_entity) is None:
        return {}  # free-text form: no placeholder
    return {a.lower(): "choose_option" for a in unchosen_actions(user_input)}


def _select(
    choices: list[str],
    extra: tuple[str, str] | None,
    leading: tuple[str, str] | None = None,
) -> selector.SelectSelector:
    select_options = [{"value": o, "label": o} for o in choices]
    if leading is not None:
        select_options.insert(0, {"value": leading[0], "label": leading[1]})
    if extra is not None:
        select_options.append({"value": extra[0], "label": extra[1]})
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=select_options,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _modes_schema(
    options: list[str] | None, mode_map: dict[str, Any]
) -> vol.Schema:
    """One field per action plus Control off.

    With a readable option list each field is a dropdown of the entity's
    options (plus "Not supported" / "Do nothing" where allowed); without one
    the fields are free text so the step never blocks the user.
    """
    fields: dict[Any, Any] = {}
    # (form key, mapping key, sentinel choice or None if a value is required)
    rows = [
        (a.lower(), a, (NOT_SUPPORTED, "Not supported") if a == "EXPORT" else None)
        for a in ACTIONS
    ]
    rows.append((CONTROL_OFF.lower(), CONTROL_OFF, (DO_NOTHING, "Do nothing")))
    for key, map_key, extra in rows:
        current = mode_map.get(map_key)
        if options is None:
            if extra is not None:
                # Optional free text: the frontend omits a cleared field, so
                # use a suggested value (not a default) -- omitted means blank.
                suggested = {"suggested_value": current} if current else None
                fields[vol.Optional(key, description=suggested)] = selector.TextSelector()
            else:
                fields[vol.Required(key, default=current or "")] = selector.TextSelector()
            continue
        # Only the entity's real options are offered. A required action with
        # no same-named option gets a leading placeholder as its default: a
        # required dropdown with no default is rendered with its first option
        # pre-selected, which would silently map the action to it.
        choices = list(options)
        leading = None
        if current is not None and current in choices:
            default: Any = current
        elif current is None and extra is not None:
            default = extra[0]
        else:
            leading = (CHOOSE, "Choose an option\u2026")
            default = CHOOSE
        fields[vol.Required(key, default=default)] = _select(choices, extra, leading)
    return vol.Schema(fields)


def _show_modes_form(
    flow,
    control_entity: str,
    stored: dict[str, Any] | None,
    errors: dict[str, str] | None = None,
) -> FlowResult:
    options = _control_entity_options(flow.hass, control_entity)
    if options is None:
        note = (
            "Home Assistant could not read the option list of this entity "
            "(it is missing or unavailable), so type the option names "
            "exactly as the inverter expects them. Leave Export blank if the "
            "inverter cannot export, and Control off blank to do nothing."
        )
    else:
        note = ""
    # Stored values that are not among the entity's options are re-defaulted;
    # with no readable options the stored/legacy values are shown as-is.
    mode_map = form_mode_map(stored, options)
    return flow.async_show_form(
        step_id="modes",
        data_schema=_modes_schema(options, mode_map),
        description_placeholders={"entity": control_entity, "note": note},
        errors=errors or {},
    )


class OptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._pending: dict[str, Any] = {}

    async def async_step_modes(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        current = {**self._entry.data, **(self._entry.options or {})}
        # The control entity picked in the previous step, which may differ
        # from the stored one: list ITS options (stale stored values are
        # re-defaulted by form_mode_map).
        control_entity = self._pending.get(
            CONF_INVERTER_CONTROL_ENTITY
        ) or current.get(CONF_INVERTER_CONTROL_ENTITY)
        if user_input is not None:
            errors = _choice_errors(self.hass, control_entity, user_input)
            if errors:
                return _show_modes_form(
                    self, control_entity, _reshow_map(user_input), errors
                )
            return self.async_create_entry(
                title="",
                data={**self._pending, CONF_MODE_MAP: _mode_map_from_input(user_input)},
            )
        # A different control entity means the stored mapping (including an
        # explicit "Not supported" / "Do nothing") belongs to the old one:
        # start from fresh defaults for the new entity.
        stored = current.get(CONF_MODE_MAP)
        if control_entity != current.get(CONF_INVERTER_CONTROL_ENTITY):
            stored = None
        return _show_modes_form(self, control_entity, stored)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            # Hold the entity picks and continue to the inverter modes step;
            # the entry is only written once that step is submitted.
            self._pending = dict(user_input)
            return await self.async_step_modes()

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
