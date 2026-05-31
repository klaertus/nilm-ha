"""Binary sensor platform for the NILM integration.

One BinarySensorEntity per appliance detected by the model.

Coordinator data format (normalized from POST /predict power floats):
    {
        "fridge":          {"power": 45.2,   "state": 1},
        "kettle":          {"power": 2100.5, "state": 1},
        "washing_machine": {"power": 0.0,    "state": 0},
    }

state = 1 if power > 0. Make sense for the user instead of a thresold watts value.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up NILM binary sensors from a config entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]
    known: set[str] = set()
    hass.data[DOMAIN]["known_binary_sensors"] = known
    # Expose the platform's add entities callback so views like
    # ApplianceAddView can spawn entities at runtime without a HA restart.
    hass.data[DOMAIN]["add_entities_binary_sensor"] = async_add_entities

    enabled_appliances = hass.data[DOMAIN].get("enabled_appliances", [])
    initial_appliances = list(coordinator.data.keys()) if coordinator.data else enabled_appliances
    entities = []
    for appliance_id in initial_appliances:
        known.add(appliance_id)
        entities.append(NilmBinarySensor(coordinator, appliance_id, entry.entry_id))

    async_add_entities(entities, True)

    @callback
    def discover_new_appliances(_now=None) -> None:
        """Add binary sensors for appliances seen in coordinator data for the first time."""
        data = coordinator.data or {}
        new_ids = set(data.keys()) - known
        if not new_ids:
            return
        new_entities = []
        for appliance_id in new_ids:
            known.add(appliance_id)
            new_entities.append(NilmBinarySensor(coordinator, appliance_id, entry.entry_id))
        async_add_entities(new_entities, True)
        _LOGGER.info(f"NILM: Discovered {len(new_entities)} new appliance binary sensor(s).")

    coordinator.async_add_listener(discover_new_appliances)

    _LOGGER.info(f"NILM: Binary sensor platform initialised with {len(entities)} appliance(s).")


# State (is_on) is derived HA-side by normalize_predictions: state = power > 0.
# The backend already zeroes OFF appliances, so no separate threshold is needed.
class NilmBinarySensor(CoordinatorEntity, BinarySensorEntity):

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, coordinator, appliance_id: str, entry_id: str):
        super().__init__(coordinator)
        self._appliance_id = appliance_id
        self._entry_id = entry_id
        self._device_name = appliance_id.replace("_", " ").title()

        self._attr_unique_id = f"{entry_id}_nilm_{appliance_id}_state"
        self._attr_name = f"NILM {self._device_name}"

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
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        info = data.get(self._appliance_id)
        if info is not None:
            return bool(info.get("state", 0))
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
