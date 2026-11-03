"""Sensors adapted from the official BLUETTI 1.0.5 sensor.py.

Copyright (c) 2025 Bluetti. Local modifications: see LICENSE and NOTICE.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfElectricPotential, UnitOfPower

from .discovery_entity import DiscoveredEntity, setup_discovered_entities
from .profile import CHARGING_OPTIONS
from .const import DOMAIN
from .icon_config import get_icon_for_fn_code

SENSOR_MAP = {
    "SensorDeviceClass.BATTERY": {"device_class": SensorDeviceClass.BATTERY,
                                 "state_class": SensorStateClass.MEASUREMENT, "unit": PERCENTAGE},
    "SensorDeviceClass.POWER": {"device_class": SensorDeviceClass.POWER,
                               "state_class": SensorStateClass.MEASUREMENT, "unit": UnitOfPower.WATT},
    "SensorDeviceClass.VOLTAGE": {"device_class": SensorDeviceClass.VOLTAGE,
                                 "state_class": SensorStateClass.MEASUREMENT, "unit": UnitOfElectricPotential.VOLT},
}


async def async_setup_entry(hass, entry, async_add_entities):
    device = entry.runtime_data.device
    async_add_entities([BluettiSensor(device, state, SENSOR_MAP[state.sensor_info["sensorType"]])
                        for state in device.states] + [LastSampleSensor(device)])
    setup_discovered_entities(hass, entry, async_add_entities, DiscoveredSensor, binary=False)


class BluettiSensor(SensorEntity):
    """Official state/device callback pattern, backed by validated local reports."""
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device, state, meta):
        self._device = device
        self._state_obj = state
        self._attr_unique_id = f"{device.device_id}_{state.fn_code}"
        self._attr_name = state.fn_name
        self._attr_device_class = meta["device_class"]
        self._attr_state_class = meta["state_class"]
        self._attr_native_unit_of_measurement = meta["unit"]
        self._attr_icon = get_icon_for_fn_code(state.fn_code)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": device.name, "manufacturer": device.manufacturer, "model": device.model,
        }

    @property
    def native_value(self):
        return self._state_obj.fn_value

    @property
    def available(self):
        return self._device.online and self._state_obj.fn_value is not None

    @property
    def extra_state_attributes(self):
        return self._device.telemetry_attributes

    async def async_added_to_hass(self):
        self._device.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        self._device.remove_callback(self.async_write_ha_state)


class LastSampleSensor(BluettiSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device):
        from .models import BluettiState
        super().__init__(device, BluettiState("last_valid_sample", "Ultima lettura valida"),
                         {"device_class": SensorDeviceClass.TIMESTAMP, "state_class": None, "unit": None})
        self._attr_icon = "mdi:clock-check-outline"

    @property
    def native_value(self):
        return self._device.last_successful_poll

    @property
    def available(self):
        return True


class DiscoveredSensor(DiscoveredEntity, SensorEntity):
    """A sensor added when its known register has been confirmed on the wire."""
    def __init__(self, device, field):
        super().__init__(device, field)
        self._attr_device_class = field.device_class
        self._attr_native_unit_of_measurement = field.unit
        if field.kind == "remaining":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if field.kind == "charging":
            self._attr_options = list(CHARGING_OPTIONS)
            self._attr_translation_key = "charging_mode"

    @property
    def native_value(self):
        return self._device.discovery.observations[self._field.key].value
