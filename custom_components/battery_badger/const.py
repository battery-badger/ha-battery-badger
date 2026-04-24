"""Constants for the Battery Badger Home Assistant integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "battery_badger"

# Config / options keys
CONF_SERVER_URL: Final = "server_url"
CONF_API_TOKEN: Final = "api_token"
CONF_INSTALLATION_ID: Final = "installation_id"
CONF_INSTALLATION_NAME: Final = "installation_name"
CONF_SOC_ENTITY: Final = "soc_entity"
CONF_CONSUMPTION_ENTITIES: Final = "consumption_entities"
CONF_SOLAR_ENTITIES: Final = "solar_entities"
CONF_INVERTER_CONTROL_ENTITY: Final = "inverter_control_entity"

DEFAULT_SERVER_URL: Final = "http://localhost:3000"

# Hex values mirror ui/components/calculator.js ACTION_CONFIG.
ACTION_COLORS: Final = {
    "CHARGE": "#1e6deb",
    "HOLD": "#7a869f",
    "DISCHARGE": "#e16914",
    "EXPORT": "#662ced",
}

# Server schedules can return EXPORT but most inverters in the field can't
# export. The plugin translates EXPORT -> HOLD before sending to the user's
# inverter-control entity. Once export-capable inverters ship, flip this
# to EXPORT or expose per-mode service templating.
MODE_MAP: Final = {
    "CHARGE": "CHARGE",
    "HOLD": "HOLD",
    "DISCHARGE": "DISCHARGE",
    "EXPORT": "HOLD",
}

PLATFORMS: Final = ["sensor"]

# URL path for the Lovelace card the integration ships with.
CARD_URL: Final = "/battery_badger_static/battery-badger-card.js"
