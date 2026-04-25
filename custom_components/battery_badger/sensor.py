"""Sensors exposing Battery Badger state."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INSTALLATION_ID, CONF_INSTALLATION_NAME, DOMAIN
from .coordinator import BatteryBadgerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BatteryBadgerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        CurrentActionSensor(coordinator, entry),
        NextChangeSensor(coordinator, entry),
        LastReadingSensor(coordinator, entry),
        ScheduleSensor(coordinator, entry),
    ])


class _Base(CoordinatorEntity[BatteryBadgerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BatteryBadgerCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry
        installation_id = entry.data[CONF_INSTALLATION_ID]
        installation_name = entry.data.get(
            CONF_INSTALLATION_NAME, f"Installation {installation_id}"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}")},
            "name": f"Battery Badger — {installation_name}",
            "manufacturer": "Battery Badger",
            "model": "Cloud integration",
            "configuration_url": f"{entry.data['server_url'].rstrip('/')}/dashboard",
        }


class CurrentActionSensor(_Base):
    _attr_name = "Current action"
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_action"

    @property
    def native_value(self):
        seg = self._current_segment()
        return seg["action"] if seg else None

    @property
    def extra_state_attributes(self):
        seg = self._current_segment()
        return {
            "segment_start": seg["start"] if seg else None,
            "segment_finish": seg["finish"] if seg else None,
            "applied_mode": self.coordinator.data.get("applied_mode"),
        }

    def _current_segment(self):
        now = datetime.now(timezone.utc)
        for seg in self.coordinator.data.get("schedule") or []:
            try:
                start = datetime.fromisoformat(seg["start"].replace("Z", "+00:00"))
                finish = datetime.fromisoformat(seg["finish"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if start <= now < finish:
                return seg
        return None


class NextChangeSensor(_Base):
    _attr_name = "Next mode change"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_change"

    @property
    def native_value(self):
        current = self._current()
        if current is None:
            return None
        schedule = self.coordinator.data.get("schedule") or []
        try:
            idx = schedule.index(current)
        except ValueError:
            return None
        upcoming = schedule[idx + 1] if idx + 1 < len(schedule) else None
        return upcoming["start"] if upcoming else current["finish"]

    @property
    def extra_state_attributes(self):
        current = self._current()
        schedule = self.coordinator.data.get("schedule") or []
        if current is None or not schedule:
            return {"next_action": None}
        try:
            idx = schedule.index(current)
        except ValueError:
            return {"next_action": None}
        upcoming = schedule[idx + 1] if idx + 1 < len(schedule) else None
        return {"next_action": upcoming["action"] if upcoming else None}

    def _current(self):
        now = datetime.now(timezone.utc)
        for seg in self.coordinator.data.get("schedule") or []:
            try:
                start = datetime.fromisoformat(seg["start"].replace("Z", "+00:00"))
                finish = datetime.fromisoformat(seg["finish"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if start <= now < finish:
                return seg
        return None


class LastReadingSensor(_Base):
    _attr_name = "Last reading"
    _attr_icon = "mdi:upload"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_reading"

    @property
    def native_value(self):
        reading = self.coordinator.data.get("last_reading") or {}
        return reading.get("taken_at")

    @property
    def extra_state_attributes(self):
        reading = self.coordinator.data.get("last_reading") or {}
        return {
            "usage_wh": reading.get("usage_wh"),
            "solar_wh": reading.get("solar_wh"),
            "soc": reading.get("soc"),
            "last_error": self.coordinator.data.get("last_error"),
        }


class ScheduleSensor(_Base):
    """Holds the 12h schedule as an attribute for the custom card."""

    _attr_name = "Schedule"
    _attr_icon = "mdi:chart-timeline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_schedule"

    @property
    def native_value(self):
        schedule = self.coordinator.data.get("schedule") or []
        return f"{len(schedule)} segments" if schedule else "empty"

    @property
    def extra_state_attributes(self):
        return {
            "segments": self.coordinator.data.get("schedule") or [],
            "applied_mode": self.coordinator.data.get("applied_mode"),
            "last_schedule_fetch": self.coordinator.data.get("last_schedule_fetch"),
            "control_switch": self.coordinator.control_switch_entity_id,
            "telemetry_switch": self.coordinator.telemetry_switch_entity_id,
            "control_enabled": self.coordinator.control_enabled,
            "telemetry_enabled": self.coordinator.telemetry_enabled,
        }
