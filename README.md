# Battery Badger — Home Assistant integration

[![hacs][hacs-badge]][hacs]
[![Validate](https://github.com/battery-badger/ha-battery-badger/actions/workflows/validate.yml/badge.svg)](https://github.com/battery-badger/ha-battery-badger/actions/workflows/validate.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Home Assistant custom integration for [Battery Badger](https://batterybadger.com) — a cloud service that trains a policy to charge and discharge your home battery against time-of-use tariffs (Octopus Agile today, more to come).

The integration closes the loop between your inverter and the Battery Badger server:

1. Every half hour it POSTs a **reading** — state of charge, household consumption, and solar generation — from the Home Assistant entities you pick.
2. It fetches a **12-hour action schedule** back from the server (CHARGE / HOLD / DISCHARGE / EXPORT segments on half-hour boundaries).
3. On each `:00` / `:30` boundary it drives an **inverter control entity** (a `select` or `input_select`) to the right mode for the current segment.
4. It ships a **Lovelace card** (`battery-badger-card`) that renders the coloured schedule bar and the current mode, auto-registered as a Lovelace resource on first setup.

Everything is driven from a single config flow — no YAML.

## Prerequisites

- A Battery Badger account at <https://batterybadger.com> with at least one **installation** configured.
- A long-lived **API token** generated at `/account/tokens` on the server.
- Home Assistant **2024.7** or newer.
- An inverter control entity in Home Assistant — a `select` or `input_select` whose options include `CHARGE`, `HOLD`, and `DISCHARGE`. If your inverter only exposes a `switch`, wire up an `input_select` and script the switch off it.

## Installation

### HACS (recommended)

Until this integration is added to the default HACS catalogue, add it as a **custom repository**:

1. In HACS → Integrations → ⋯ → **Custom repositories**, add `https://github.com/battery-badger/ha-battery-badger` with category **Integration**.
2. Search for *Battery Badger* in HACS and install it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/battery_badger/` into your Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Configuration

Go to **Settings → Devices & Services → Add Integration** and pick *Battery Badger*. The config flow has four steps:

1. **Server** — server URL (default `https://batterybadger.com`) and the API token from `/account/tokens`.
2. **Installation** — pick which installation on your account this Home Assistant should drive. One Home Assistant config entry per installation.
3. **Entities** — the entities to read from and the one to drive:
   - **State of charge (%)** — a single `sensor` with `device_class: battery` and unit `%`. Auto-detected when available.
   - **Household consumption** — one or more cumulative energy sensors (kWh or Wh). Multiple entries are summed. Defaults are pulled from the Energy dashboard's *grid → flow from* sources.
   - **Solar generation** — cumulative energy sensors for PV. Defaults pulled from the Energy dashboard's *solar* sources.
   - **Inverter control entity** — a `select` or `input_select` with `CHARGE` / `HOLD` / `DISCHARGE` options.
4. **Done.** A device appears with four sensors (below) and the Lovelace card is registered automatically.

You can re-pick entities later via **Configure** on the integration — no re-auth needed.

## Entities

| Entity | What it reports |
| --- | --- |
| `sensor.battery_badger_..._current_action` | The action for the current half-hour (`CHARGE` / `HOLD` / `DISCHARGE` / `EXPORT`). Attributes: segment bounds + the mode currently applied to your inverter. |
| `sensor.battery_badger_..._next_mode_change` | Timestamp (ISO-8601) when the action is scheduled to change. Attribute: the next action. |
| `sensor.battery_badger_..._last_reading` | When the last reading was posted. Attributes: `usage_wh`, `solar_wh`, `soc`, `last_error`. |
| `sensor.battery_badger_..._schedule` | The whole 12-hour schedule as the `segments` attribute — this is what the Lovelace card reads. |

## Lovelace card

On first setup the integration serves `battery-badger-card.js` from `/battery_badger_static/` and registers it as a Lovelace module resource. No manual *Add Resource* step.

Add a card like this to any dashboard (replace the entity with the `schedule` sensor the integration created for you):

```yaml
type: custom:battery-badger-card
entity: sensor.battery_badger_my_installation_schedule
title: Battery Badger
```

The card draws a segmented bar coloured by action (blue CHARGE / grey HOLD / orange DISCHARGE / purple EXPORT), outlines the current segment, and shows the mode currently applied to your inverter.

## Scheduling

Two independent wake-up chains run inside the integration:

- **Reading chain** — fires at **HH:25** and **HH:55** plus a deterministic `sha256(installation_id) % 300` seconds of jitter, so not every installation in a timezone hits the server at the same second. On each tick it posts a reading and then fetches the fresh 12-hour schedule.
- **Mode-apply chain** — fires at exactly **HH:00** and **HH:30** and calls `select_option` on your inverter control entity with the action for the current half-hour.

This means the server always sees readings ~5 minutes before it's asked to produce a schedule, and inverter mode changes land on half-hour boundaries aligned with the tariff windows.

## `EXPORT → HOLD` remapping

The server's action schedule can return `EXPORT` segments. Most inverters in the field today can't export on command, so the integration maps `EXPORT → HOLD` before calling `select_option`. When your hardware gains an `EXPORT` option, flip `MODE_MAP["EXPORT"]` in [`custom_components/battery_badger/const.py`](custom_components/battery_badger/const.py) to `"EXPORT"`.

## Troubleshooting

- **Invalid API token.** Tokens are of the form `bb_<prefix>_<secret>` and are shown **once** at creation time. Generate a fresh one at `/account/tokens` on the server.
- **No installations found.** Create one on the Battery Badger web dashboard first, then re-run the config flow.
- **SOC entity unavailable / non-numeric.** The integration needs a float-parseable state on the SOC sensor. A temporarily `unavailable` sensor is reported in the `last_reading` sensor's `last_error` attribute; readings resume when it returns.
- **Server URL changed.** Remove the config entry and re-add — URL isn't editable via the options flow yet. Your API token stays valid.
- **Integration setup failed after a backend deploy.** Check the server's `/api/v1/auth/me/` manually; any 5xx there will surface as `cannot_connect` in the config flow.

Detailed logs: add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.battery_badger: debug
```

## Development

The integration targets Home Assistant 2024.7+ and has no third-party runtime dependencies — it uses `aiohttp` and the standard HA helpers only.

Repository layout:

```
custom_components/battery_badger/
├── __init__.py          # setup_entry, frontend registration, static path
├── api.py               # async aiohttp client (Token auth)
├── config_flow.py       # 4-step flow + options flow
├── const.py             # DOMAIN, config keys, MODE_MAP, colours
├── coordinator.py       # DataUpdateCoordinator — reading + mode-apply chains
├── sensor.py            # current_action, next_change, last_reading, schedule
├── manifest.json
├── strings.json
├── translations/
│   └── en.json
└── www/
    └── battery-badger-card.js   # Lit custom card (no build step)
```

Hassfest and HACS validation run on every push via [.github/workflows/validate.yml](.github/workflows/validate.yml).

## License

Apache License 2.0 — see [LICENSE](LICENSE). This matches Home Assistant core.

## Related

- Battery Badger website & dashboard: <https://batterybadger.com>
- Backend + simulation code (private) — the REST endpoints this integration calls are documented in the backend repo's `docs/api.md`.

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
