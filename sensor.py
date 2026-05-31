"""Sensor platform for the NILM integration.

Two sensor types:
- NilmConfidenceSensor : one per appliance, exposing the raw confidence (0.0-1.0).
- NilmAggregatedPowerSensor : mirrors the configured aggregate power entity.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_POWER_ENTITY, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up NILM sensors from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]
    power_entity = entry.data.get(CONF_POWER_ENTITY)
    known: set[str] = set()
    hass.data[DOMAIN]["known_power_sensors"] = known
    hass.data[DOMAIN]["add_entities_sensor"] = async_add_entities

    entities: list[SensorEntity] = []

    if power_entity:
        entities.append(NilmAggregatedPowerSensor(hass, entry.entry_id, power_entity))

    entities.append(NilmPredictedPowerSensor(coordinator, entry.entry_id))

    enabled_appliances = hass.data[DOMAIN].get("enabled_appliances", [])
    initial_appliances = list(coordinator.data.keys()) if coordinator.data else enabled_appliances
    for appliance_id in initial_appliances:
        known.add(appliance_id)
        entities.append(NilmPowerSensor(coordinator, appliance_id, entry.entry_id))

    async_add_entities(entities, True)

    @callback
    def discover_new_appliances(_now=None) -> None:
        """Called once per coordinator update, add sensors for newly predicted appliances."""
        data = coordinator.data or {}
        new_ids = set(data.keys()) - known
        if not new_ids:
            return
        new_entities = []
        for appliance_id in new_ids:
            known.add(appliance_id)
            new_entities.append(NilmPowerSensor(coordinator, appliance_id, entry.entry_id))
        async_add_entities(new_entities, True)
        _LOGGER.info(f"NILM: Discovered {len(new_entities)} new appliance sensor(s).")

    coordinator.async_add_listener(discover_new_appliances)

    _LOGGER.info(f"NILM: Sensor platform initialised with {len(entities) - (1 if power_entity else 0)} sensor(s) + aggregated power.")

class NilmPowerSensor(CoordinatorEntity, SensorEntity):
    """Predicted Power consumption (W) for one appliance."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator, appliance_id: str, entry_id: str):
        super().__init__(coordinator)
        self._appliance_id = appliance_id
        self._entry_id = entry_id
        self._device_name = appliance_id.replace("_", " ").title()

        self._attr_unique_id = f"{entry_id}_nilm_{appliance_id}_power"
        self._attr_name = f"NILM {self._device_name} Power"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._appliance_id}")},
            name=f"NILM {self._device_name}",
            manufacturer="NILM Detection",
            model="Detected Appliance",
            via_device=(DOMAIN, self._entry_id),
        )


    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        info = data.get(self._appliance_id)
        if info and "power" in info:
            return round(info["power"], 3)
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

class NilmPredictedPowerSensor(CoordinatorEntity, SensorEntity):
    """Sum of all predicted appliance powers for comparaison with the aggregate power sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_icon = "mdi:flash-outline"

    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_nilm_predicted_power"
        self._attr_name = "NILM Predicted Power"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="NILM Hub",
            manufacturer="NILM",
            model="Energy Monitor",
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        if not data:
            return None
        return round(sum(info.get("power", 0.0) for info in data.values()), 3)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

class NilmAggregatedPowerSensor(SensorEntity):
    """Mirrors the configured aggregate power entity."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(self, hass, entry_id: str, power_entity_id: str):
        self._hass = hass
        self._entry_id = entry_id
        self._power_entity_id = power_entity_id
        self._listener = None

        self._attr_unique_id = f"{DOMAIN}_{entry_id}_aggregated_power"
        self._attr_name = "NILM Aggregated Power"
        self._attr_native_value = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="NILM Hub",
            manufacturer="NILM",
            model="Energy Monitor",
        )

    async def async_added_to_hass(self) -> None:
        self._listener = async_track_state_change_event(
            self._hass, self._power_entity_id, self._on_power_change
        )
        self._refresh()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._listener:
            self._listener()
            self._listener = None

    @callback
    def _on_power_change(self, event):
        self._refresh()
        self.async_write_ha_state()

    def _refresh(self):
        state = self._hass.states.get(self._power_entity_id)
        if state:
            try:
                self._attr_native_value = float(state.state)
            except (ValueError, TypeError):
                self._attr_native_value = None
        else:
            self._attr_native_value = None
