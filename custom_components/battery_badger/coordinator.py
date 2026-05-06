"""Coordinator that drives the reading/schedule/mode-apply lifecycle."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
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
    CONF_SOC_ENTITY,
    CONF_SOLAR_ENTITIES,
    DOMAIN,
    MODE_MAP,
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

    HA energy sensors usually report kWh; a few report Wh directly. We trust
    the unit_of_measurement attribute when present and fall back to kWh
    (the Energy dashboard's canonical unit).
    """
    if unit and unit.lower() == "wh":
        return int(round(value))
    # default: treat as kWh
    return int(round(value * 1000))


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
        total += _kwh_to_wh(v, st.attributes.get("unit_of_measurement"))
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

    def set_control_enabled(self, value: bool) -> None:
        self.control_enabled = bool(value)
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

        # Snap taken_at to the expected half-hour slot (:25/:55 window → round
        # down to :00 or :30) so the server sees deterministic timestamps.
        now = dt_util.utcnow().replace(second=0, microsecond=0)
        minute = 0 if now.minute < 30 else 30
        taken_at = now.replace(minute=minute)

        try:
            await self._client.post_reading(
                installation_id=self._installation_id,
                taken_at=taken_at,
                usage_wh=usage_wh,
                solar_wh=solar_wh,
                battery_soc_percent=soc,
            )
        except BatteryBadgerConflictError:
            _LOGGER.debug("duplicate reading for %s — skipping", taken_at.isoformat())
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
            segments = await self._client.get_action_schedule(self._installation_id)
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
            await self._apply_current_mode(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("mode-apply tick failed: %s", exc)
        finally:
            self._schedule_next_apply()
            self.async_set_updated_data(self.data)

    async def _apply_current_mode(self, now: datetime) -> None:
        if not self.control_enabled:
            _LOGGER.debug("battery control disabled; skipping mode apply")
            return
        segment = self._segment_at(now)
        if segment is None:
            _LOGGER.debug("no schedule segment covers %s; skipping mode apply", now)
            return

        action = segment["action"]
        target_mode = MODE_MAP.get(action, "HOLD")
        applied = self.data.get("applied_mode")

        if target_mode == applied:
            _LOGGER.debug("mode already %s — no action", target_mode)
            return

        control_entity = self._config[CONF_INVERTER_CONTROL_ENTITY]
        domain = control_entity.split(".", 1)[0]

        if domain in ("select", "input_select"):
            service = "select_option"
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": control_entity, "option": target_mode},
                blocking=True,
            )
        else:
            _LOGGER.error(
                "unsupported control entity domain %s for %s", domain, control_entity
            )
            return

        self.data["applied_mode"] = target_mode
        _LOGGER.info(
            "set %s to %s (schedule said %s)", control_entity, target_mode, action
        )

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
