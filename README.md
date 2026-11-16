<p align="center">
  <img src="custom_components/battery_badger/brand/icon.svg" alt="Battery Badger" width="128" height="128" />
</p>

# Battery Badger — Home Assistant integration

[![hacs][hacs-badge]][hacs]
[![Validate](https://github.com/battery-badger/ha-battery-badger/actions/workflows/validate.yml/badge.svg)](https://github.com/battery-badger/ha-battery-badger/actions/workflows/validate.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Home Assistant custom integration for [Battery Badger](https://batterybadger.com) — a cloud service that trains a policy to charge and discharge your home battery against time-of-use tariffs (Octopus Agile today, more to come).

The integration closes the loop between your inverter and the Battery Badger server:

1. Every half hour it POSTs a **reading** — state of charge, household consumption, and solar generation — from the Home Assistant entities you pick.
2. It fetches a **12-hour action schedule** back from the server (CHARGE / HOLD / DISCHARGE / EXPORT segments on half-hour boundaries). Each fetch also tells the server which of those actions your inverter supports.
3. On each `:00` / `:30` boundary it drives an **inverter control entity** (a `select` or `input_select`) to the option you mapped to the current segment's action.
4. It ships a **Lovelace card** (`battery-badger-card`) that renders the coloured schedule bar and the current mode, auto-registered as a Lovelace resource on first setup.

Everything is driven from a single config flow — no YAML.

## Prerequisites

- A Battery Badger account at <https://batterybadger.com> with at least one **installation** configured.
- A long-lived **API token** generated at `/account/tokens` on the server.
- Home Assistant **2024.7** or newer.
- An inverter control entity in Home Assistant — a `select` or `input_select` whose options can express the actions below (you map them to your inverter's own option names during setup). If your inverter only exposes a `switch`, wire up an `input_select` and script the switch off it.

## Installation

### HACS (recommended)

Until this integration is added to the default HACS catalogue, add it as a **custom repository**:

1. In HACS → Integrations → ⋯ → **Custom repositories**, add `https://github.com/battery-badger/ha-battery-badger` with category **Integration**.
2. Search for *Battery Badger* in HACS and install it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/battery_badger/` into your Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Configuration

Go to **Settings → Devices & Services → Add Integration** and pick *Battery Badger*. The config flow has five steps:

1. **Server** — server URL (default `https://batterybadger.com`) and the API token from `/account/tokens`.
2. **Installation** — pick which installation on your account this Home Assistant should drive. One Home Assistant config entry per installation.
3. **Entities** — the entities to read from and the one to drive:
   - **State of charge (%)** — a single `sensor` with `device_class: battery` and unit `%`. Auto-detected when available.
   - **Household consumption** — one or more cumulative energy sensors (kWh or Wh). Multiple entries are summed. Defaults are pulled from the Energy dashboard's *grid → flow from* sources.
   - **Solar generation** — cumulative energy sensors for PV. Defaults pulled from the Energy dashboard's *solar* sources.
   - **Inverter control entity** — a `select` or `input_select` that sets the inverter mode.
4. **Inverter modes** — confirm which option of the control entity is used for each action (see [Inverter modes](#inverter-modes)).
5. **Done.** A device appears with four sensors (below) and the Lovelace card is registered automatically.

You can re-pick entities and re-confirm the inverter modes later via **Configure** on the integration — no re-auth needed.

## Entities

| Entity | What it reports |
| --- | --- |
| `sensor.battery_badger_..._current_action` | The action for the current half-hour (`CHARGE` / `HOLD` / `DISCHARGE` / `EXPORT`). Attributes: segment bounds, the option currently applied to your inverter (`applied_mode`), the effective `mode_map` and the `supported_actions`. |
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

The card draws a segmented bar coloured by action (green charge / grey hold / amber power the house / purple export, with the definitions below as tooltips), outlines the current segment, and shows the mode currently applied to your inverter.

## Scheduling

Two independent wake-up chains run inside the integration:

- **Reading chain** — fires at **HH:25** and **HH:55** plus a deterministic `sha256(installation_id) % 300` seconds of jitter, so not every installation in a timezone hits the server at the same second. On each tick it posts a reading and then fetches the fresh 12-hour schedule.
- **Mode-apply chain** — fires at exactly **HH:00** and **HH:30** and calls `select_option` on your inverter control entity with the action for the current half-hour.

This means the server always sees readings ~5 minutes before it's asked to produce a schedule, and inverter mode changes land on half-hour boundaries aligned with the tariff windows.

## Inverter modes

Inverters name their modes differently, so after picking the control entity the flow shows an **Inverter modes** step: one dropdown per action plus *Control off*, each listing the control entity's current options. Defaults match options with the same name (case-insensitive). If the entity has no option named like an action (for example `Force charge` instead of `CHARGE`), that dropdown starts on a *Choose an option…* placeholder, and submitting with it still selected re-shows the form with an error on that field (your other selections are kept) until you pick one of the entity's real options. Apart from the placeholder, only the options the entity actually has are offered. If the entity's options can't be read (missing or unavailable), the step shows free-text fields instead of failing; clear Export or Control off to leave them unsupported / do nothing. When you change the control entity under **Configure**, the modes step starts from fresh defaults for the new entity rather than carrying the old mapping across. Entries with no confirmed mapping keep the 0.1.x behaviour (each action's own name) until you confirm one.

| Action | Label | What it means |
| --- | --- | --- |
| `CHARGE` | Charge from grid | Battery charges from the grid at full power. |
| `HOLD` | Hold (keep solar) | Battery neither discharges nor charges from the grid; surplus solar is stored, then exported once full. |
| `DISCHARGE` | Power the house | Battery covers the household load; surplus solar is stored, then exported once full. |
| `EXPORT` | Export to grid | Battery discharges at full power to the house and the grid. |
| *Control off* | Control off | The option to select when Battery Badger control is switched off, handing the battery back to the inverter's own controller (typically an option called `AUTO`). |

`CHARGE`, `HOLD` and `DISCHARGE` must be mapped. `EXPORT` may be set to **Not supported** (the default when the entity has no `EXPORT` option) and *Control off* to **Do nothing** (the default when it has no `AUTO` option).

**Reserved names.** These option names are not sent by Battery Badger today but are reserved for future use, so avoid repurposing them for something else: `IDLE` (battery does nothing; surplus solar is exported), `SOLAR_CHARGE` (all solar goes into the battery; the house runs from the grid) and `AUTO` (the inverter's own controller).

**Supported actions.** With every schedule fetch the integration sends the server the actions that have an option mapped (`{"supported_actions": ["CHARGE", "HOLD", "DISCHARGE"]}`, plus `EXPORT` when mapped), so the schedule is planned for what your inverter can do. Older servers ignore this. If a schedule still contains an unsupported action, `EXPORT` falls back to `DISCHARGE` (covering the house beats idling) and anything unknown falls back to `HOLD`.

**Control off.** When you switch **Battery control** off (on to off, while Home Assistant is running), the integration selects the *Control off* option once, if one is mapped. It never does this at startup, on reload, when an already-off switch is restored, or if control was never on this session, because that would take over a battery someone else is controlling. Switching control back on applies the current segment immediately.

### Upgrading from 0.1.x

Existing installs keep working with no action: until you confirm the mapping under **Configure**, the integration uses defaults computed from the control entity's current options. For an entity whose options are `AUTO, CHARGE, SOLAR_CHARGE, HOLD, IDLE, DISCHARGE, EXPORT` that means:

- An entity with an `EXPORT` option now receives `EXPORT` for export segments (before, it received `HOLD`).
- An unsupported `EXPORT` now falls back to `DISCHARGE` instead of `HOLD`.
- Switching control off now selects `AUTO` when the entity has that option (before, nothing was sent).

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
├── config_flow.py       # 5-step flow + options flow (entities, inverter modes)
├── const.py             # DOMAIN, config keys, colours
├── modes.py             # pure action -> inverter option mapping logic (unit-tested)
├── coordinator.py       # DataUpdateCoordinator — reading + mode-apply chains
├── sensor.py            # current_action, next_change, last_reading, schedule
├── manifest.json
├── strings.json
├── translations/
│   └── en.json
└── www/
    └── battery-badger-card.js   # Lit custom card (no build step)
```

Unit tests for the pure `modes.py` live in `tests/` (`python3 -m pytest tests -q`; they need only pytest, not Home Assistant). Hassfest and HACS validation run on every push via [.github/workflows/validate.yml](.github/workflows/validate.yml).

## License

Apache License 2.0 — see [LICENSE](LICENSE). This matches Home Assistant core.

## Related

- Battery Badger website & dashboard: <https://batterybadger.com>
- Backend + simulation code (private) — the REST endpoints this integration calls are documented in the backend repo's `docs/api.md`.

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
