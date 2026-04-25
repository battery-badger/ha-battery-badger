"""Switches that gate telemetry POSTing and inverter-mode control."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
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
        ControlEnabledSwitch(coordinator, entry),
        TelemetryEnabledSwitch(coordinator, entry),
    ])


class _BaseSwitch(
    CoordinatorEntity[BatteryBadgerCoordinator], SwitchEntity, RestoreEntity
):
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


class ControlEnabledSwitch(_BaseSwitch):
    _attr_name = "Battery control"
    _attr_icon = "mdi:battery-sync"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_control_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        initial = True if last is None else last.state == "on"
        self.coordinator.set_control_enabled(initial)
        self.coordinator.control_switch_entity_id = self.entity_id

    @property
    def is_on(self) -> bool:
        return self.coordinator.control_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.set_control_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.set_control_enabled(False)
        self.async_write_ha_state()


class TelemetryEnabledSwitch(_BaseSwitch):
    _attr_name = "Telemetry"
    _attr_icon = "mdi:cloud-upload"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_telemetry_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        initial = True if last is None else last.state == "on"
        self.coordinator.set_telemetry_enabled(initial)
        self.coordinator.telemetry_switch_entity_id = self.entity_id

    @property
    def is_on(self) -> bool:
        return self.coordinator.telemetry_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.set_telemetry_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.set_telemetry_enabled(False)
        self.async_write_ha_state()
