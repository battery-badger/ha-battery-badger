"""Coordinator that drives the reading/schedule/mode-apply lifecycle."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event as ha_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import (
    BatteryBadgerApiError,
    BatteryBadgerAuthError,
    BatteryBadgerClient,
    BatteryBadgerConflictError,
)
from .const import (
    CONF_CONSUMPTION_ENTITIES,
    CONF_INSTALLATION_ID,
    CONF_INVERTER_CONTROL_ENTITY,
    CONF_MODE_MAP,
    CONF_SOC_ENTITY,
    CONF_SOLAR_ENTITIES,
    DOMAIN,
)
from .modes import (
    ModeMap,
    clean_options,
    control_off_option,
    resolve_mode_map,
    supported_actions,
    target_for,
)

_LOGGER = logging.getLogger(__name__)


def jitter_seconds(installation_id: int) -> int:
    """Deterministic 0–299s offset keyed on the installation id.

    Each installation picks a stable moment in the 5-minute window — spreads
    load across the server so every user in the same timezone doesn't POST
    at exactly :25:00 and :55:00.
    """
    digest = hashlib.sha256(str(installation_id).encode("utf-8")).hexdigest()
    return int(digest, 16) % 300


def next_reading_at(now: datetime, installation_id: int) -> datetime:
    """Return the next HH:25+jitter or HH:55+jitter after ``now``."""
    j = jitter_seconds(installation_id)
    candidates: list[datetime] = []
    for hour_offset in (0, 1):
        base = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset)
        for minute in (25, 55):
            candidates.append(base.replace(minute=minute) + timedelta(seconds=j))
    return min(c for c in candidates if c > now)


def next_half_hour_boundary(now: datetime) -> datetime:
    """Return the next :00 or :30 boundary strictly after ``now``."""
    floor = now.replace(second=0, microsecond=0)
    if floor.minute < 30:
        return floor.replace(minute=30)
    return (floor + timedelta(hours=1)).replace(minute=0)


def _state_value(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return None


def _kwh_to_wh(value: float, unit: str | None) -> int:
    """Normalise an energy sensor's value to integer Wh.

    Only converts units we positively recognise. Guessing kWh for a sensor
    that actually reports Wh but has no unit_of_measurement attribute would
    inflate every reading 1000× straight into the usage profile the trading
    model plans against — a loud ValueError (surfaced via last_error) is the
    only safe behaviour.
    """
    if unit is None:
        raise ValueError("missing unit_of_measurement")
    normalised = unit.strip().lower()
    if normalised == "wh":
        return int(round(value))
    if normalised == "kwh":
        return int(round(value * 1000))
    if normalised == "mwh":
        return int(round(value * 1_000_000))
    raise ValueError(f"unrecognised energy unit '{unit}'")


def _sum_energy_entities(
    hass: HomeAssistant, entity_ids: list[str], *, kind: str
) -> int:
    """Sum cumulative-energy entities into Wh; raise if any isn't numeric.

    ``kind`` ("consumption" / "solar") shows up in the error message so the
    last_reading sensor's last_error attribute pinpoints which sensor the
    integration is waiting on.
    """
    total = 0
    for eid in entity_ids:
        st = hass.states.get(eid)
        if st is None:
            raise BatteryBadgerApiError(f"{kind} entity {eid} unavailable")
        try:
            v = float(st.state)
        except (ValueError, TypeError) as exc:
            raise BatteryBadgerApiError(
                f"{kind} entity {eid} has non-numeric state '{st.state}'"
            ) from exc
        try:
            total += _kwh_to_wh(v, st.attributes.get("unit_of_measurement"))
        except ValueError as exc:
            raise BatteryBadgerApiError(f"{kind} entity {eid}: {exc}") from exc
    return total


class BatteryBadgerCoordinator(DataUpdateCoordinator):
    """Coordinator: posts readings + fetches schedule + drives inverter mode."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: BatteryBadgerClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.entry_id}",
        )
        self._entry = entry
        self._client = client
        self._installation_id = int(entry.data[CONF_INSTALLATION_ID])
        self._reading_unsub = None
        self._apply_unsub = None
        self.control_enabled: bool = True
        self.telemetry_enabled: bool = True
        self.control_switch_entity_id: str | None = None
        self.telemetry_switch_entity_id: str | None = None
        self.data = {
            "schedule": [],
            "applied_mode": None,
            "last_reading": None,
            "last_schedule_fetch": None,
            "last_error": None,
        }

    @property
    def _config(self) -> dict[str, Any]:
        # Options-flow edits are persisted into entry.options; merge so the
        # latest user-picked entities win over the originals captured during
        # the initial config flow.
        return {**self._entry.data, **(self._entry.options or {})}

    @property
    def mode_map(self) -> ModeMap:
        """Effective action -> inverter option mapping.

        Stored mapping if the user confirmed one, else defaults for the
        control entity's current options, else the legacy map when the entity
        is missing/unavailable (re-evaluated on every call, so it upgrades by
        itself once the options appear).
        """
        cfg = self._config
        options = None
        entity_id = cfg.get(CONF_INVERTER_CONTROL_ENTITY)
        if entity_id:
            state = self.hass.states.get(entity_id)
            if state is not None:
                options = clean_options(state.attributes.get("options"))
        return resolve_mode_map(cfg.get(CONF_MODE_MAP), options)

    @property
    def supported_actions(self) -> list[str]:
        return supported_actions(self.mode_map)

    def restore_control_enabled(self, value: bool) -> None:
        """Set the control state from restored HA state, without side effects.

        Deliberately bypasses the on->off / off->on transition handling in
        ``set_control_enabled``: at startup, reload or restore nothing has
        changed, so neither the Control-off option nor an immediate mode apply
        may fire.
        """
        self.control_enabled = bool(value)
        self.async_set_updated_data(self.data)

    def set_control_enabled(self, value: bool) -> None:
        new_value = bool(value)
        was_enabled = self.control_enabled
        self.control_enabled = new_value
        if new_value and not was_enabled:
            # Don't wait for the next HH:00 / HH:30 tick — apply the active
            # segment now. Clear applied_mode first so the idempotent check
            # in _apply_current_mode can't short-circuit on a stale value
            # (e.g. user manually changed the inverter while control was off).
            self.data["applied_mode"] = None
            self.hass.async_create_task(self._apply_current_mode_logged(dt_util.utcnow()))
        elif was_enabled and not new_value:
            # Hand the battery back to the inverter's own controller — but
            # only on a real on->off transition made while we were running.
            # It must never fire at startup, on restore of an already-off
            # switch, on reload, or when control was never on this session
            # (those go through restore_control_enabled): selecting a mode
            # then would yank a battery that someone else is controlling.
            self.hass.async_create_task(self._apply_control_off())
        self.async_set_updated_data(self.data)

    def set_telemetry_enabled(self, value: bool) -> None:
        self.telemetry_enabled = bool(value)
        self.async_set_updated_data(self.data)

    async def async_start(self) -> None:
        """Schedule the first reading and mode-apply wakeups."""
        self._schedule_next_reading()
        self._schedule_next_apply()

    @callback
    def async_cancel(self) -> None:
        if self._reading_unsub is not None:
            self._reading_unsub()
            self._reading_unsub = None
        if self._apply_unsub is not None:
            self._apply_unsub()
            self._apply_unsub = None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    def _schedule_next_reading(self) -> None:
        when = next_reading_at(dt_util.utcnow(), self._installation_id)
        _LOGGER.debug("next reading POST at %s", when.isoformat())
        self._reading_unsub = ha_event.async_track_point_in_utc_time(
            self.hass, self._on_reading_tick, when
        )

    def _schedule_next_apply(self) -> None:
        when = next_half_hour_boundary(dt_util.utcnow())
        _LOGGER.debug("next mode-apply tick at %s", when.isoformat())
        self._apply_unsub = ha_event.async_track_point_in_utc_time(
            self.hass, self._on_apply_tick, when
        )

    # ------------------------------------------------------------------
    # Reading / schedule fetch
    # ------------------------------------------------------------------
    async def _on_reading_tick(self, now: datetime) -> None:
        self._reading_unsub = None
        try:
            if not self.telemetry_enabled:
                _LOGGER.debug("telemetry disabled; skipping reading POST + schedule fetch")
            else:
                await self._post_reading_and_fetch_schedule()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("reading/schedule tick failed: %s", exc)
            self.data["last_error"] = str(exc)
        finally:
            self._schedule_next_reading()
            self.async_set_updated_data(self.data)

    async def _post_reading_and_fetch_schedule(self) -> None:
        data = self._config

        soc_state = self.hass.states.get(data[CONF_SOC_ENTITY])
        if soc_state is None:
            raise BatteryBadgerApiError(f"SOC entity {data[CONF_SOC_ENTITY]} unavailable")
        try:
            soc = float(soc_state.state)
        except (ValueError, TypeError) as exc:
            raise BatteryBadgerApiError(
                f"SOC entity {data[CONF_SOC_ENTITY]} has non-numeric state '{soc_state.state}'"
            ) from exc
        soc = max(0.0, min(100.0, soc))

        # Consumption / solar are cumulative counters — silently treating an
        # unavailable sensor as zero produces a fake "drop to zero" reading
        # followed by a huge spike when the real value comes back. Skip the
        # whole post if any configured sensor isn't ready, like SOC does.
        usage_wh = _sum_energy_entities(
            self.hass, data.get(CONF_CONSUMPTION_ENTITIES, []), kind="consumption"
        )
        solar_wh = _sum_energy_entities(
            self.hass, data.get(CONF_SOLAR_ENTITIES, []), kind="solar"
        )

        # taken_at is the raw wall-clock at sample time. The server stores it
        # as-is (microsecond precision); consumers (schedule, forecaster,
        # dashboard chart, usage profile) bucket via half_hour_bucket() and
        # pick MAX(taken_at) per bucket. Server enforces ±10 min skew.
        taken_at = dt_util.utcnow()

        try:
            await self._client.post_reading(
                installation_id=self._installation_id,
                taken_at=taken_at,
                usage_wh=usage_wh,
                solar_wh=solar_wh,
                battery_soc_percent=soc,
            )
        except BatteryBadgerAuthError:
            self._entry.async_start_reauth(self.hass)
            raise

        self.data["last_reading"] = {
            "taken_at": taken_at.isoformat(),
            "usage_wh": usage_wh,
            "solar_wh": solar_wh,
            "soc": soc,
        }

        try:
            segments = await self._client.get_action_schedule(
                self._installation_id, self.supported_actions
            )
        except BatteryBadgerConflictError as exc:
            _LOGGER.warning("schedule not yet available: %s", exc)
            return
        except BatteryBadgerAuthError:
            self._entry.async_start_reauth(self.hass)
            raise

        self.data["schedule"] = segments
        self.data["last_schedule_fetch"] = dt_util.utcnow().isoformat()
        self.data["last_error"] = None

    # ------------------------------------------------------------------
    # Mode apply
    # ------------------------------------------------------------------
    async def _on_apply_tick(self, now: datetime) -> None:
        self._apply_unsub = None
        try:
            await self._apply_current_mode_logged(now)
        finally:
            self._schedule_next_apply()
            self.async_set_updated_data(self.data)

    async def _apply_current_mode_logged(self, now: datetime) -> None:
        """Apply the current mode, logging (never raising) any failure.

        Shared by the half-hourly tick and the off->on toggle task, so a
        failing ``select_option`` is always visible in the log.
        """
        try:
            await self._apply_current_mode(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("mode apply failed: %s", exc)

    async def _apply_current_mode(self, now: datetime) -> None:
        if not self.control_enabled:
            _LOGGER.debug("battery control disabled; skipping mode apply")
            return
        segment = self._segment_at(now)
        if segment is None:
            _LOGGER.debug("no schedule segment covers %s; skipping mode apply", now)
            return

        action = segment["action"]
        target_mode = target_for(action, self.mode_map)
        applied = self.data.get("applied_mode")

        if target_mode == applied:
            _LOGGER.debug("mode already %s — no action", target_mode)
            return

        if not await self._select_option(target_mode):
            return

        self.data["applied_mode"] = target_mode
        _LOGGER.info(
            "set %s to %s (schedule said %s)",
            self._config[CONF_INVERTER_CONTROL_ENTITY],
            target_mode,
            action,
        )

    async def _apply_control_off(self) -> None:
        """Select the Control-off option once, if one is mapped."""
        option = control_off_option(self.mode_map)
        if option is None:
            _LOGGER.debug("no control-off option mapped; leaving the inverter as is")
            return
        try:
            ok = await self._select_option(option, retried=False)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("failed to select control-off option %s: %s", option, exc)
            return
        if ok:
            self.data["applied_mode"] = option
            _LOGGER.info("battery control off: set inverter to %s", option)
            self.async_set_updated_data(self.data)

    async def _select_option(self, option: str, *, retried: bool = True) -> bool:
        control_entity = self._config[CONF_INVERTER_CONTROL_ENTITY]
        domain = control_entity.split(".", 1)[0]
        if domain not in ("select", "input_select"):
            _LOGGER.error(
                "unsupported control entity domain %s for %s", domain, control_entity
            )
            return False
        state = self.hass.states.get(control_entity)
        if state is None or state.state == STATE_UNAVAILABLE:
            # HA only warns when selecting on a stateless entity and the call
            # "succeeds"; don't record a mode that was never applied.
            _LOGGER.warning(
                "control entity %s is missing or unavailable; cannot select %s "
                "%s",
                control_entity,
                option,
                "(will retry on the next tick)" if retried else "(not retried)",
            )
            return False
        await self.hass.services.async_call(
            domain,
            "select_option",
            {"entity_id": control_entity, "option": option},
            blocking=True,
        )
        return True

    def _segment_at(self, now: datetime) -> dict | None:
        for seg in self.data.get("schedule") or []:
            try:
                start = _parse_iso(seg["start"])
                finish = _parse_iso(seg["finish"])
            except (KeyError, ValueError):
                continue
            if start <= now < finish:
                return seg
        return None

    # ------------------------------------------------------------------
    # User-facing helpers
    # ------------------------------------------------------------------
    async def async_refresh_now(self) -> None:
        """Manual refresh, invoked by a service call."""
        await self._post_reading_and_fetch_schedule()
        self.async_set_updated_data(self.data)


def _parse_iso(value: str) -> datetime:
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
