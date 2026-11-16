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
# Per-installation action -> inverter option mapping, see modes.py.
CONF_MODE_MAP: Final = "mode_map"

DEFAULT_SERVER_URL: Final = "https://batterybadger.com"

# Hex values mirror ui/components/calculator.js ACTION_CONFIG.
ACTION_COLORS: Final = {
    "CHARGE": "#34d399",
    "HOLD": "#a1a1aa",
    "DISCHARGE": "#fbbf24",
    "EXPORT": "#c084fc",
}

PLATFORMS: Final = ["sensor", "switch"]

# URL path for the Lovelace card the integration ships with.
CARD_URL: Final = "/battery_badger_static/battery-badger-card.js"
