"""Connectivity sensor adapted from the official BLUETTI entity model."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BluettiBinarySensor(entry.runtime_data.device)])


class BluettiBinarySensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Telemetria locale"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-connect"

    def __init__(self, device):
        self._device = device
        self._attr_unique_id = f"{device.device_id}_local_connected"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)}, "name": device.name,
            "manufacturer": device.manufacturer, "model": device.model,
        }

    @property
    def is_on(self):
        return self._device.online

    @property
    def extra_state_attributes(self):
        return self._device.telemetry_attributes

    async def async_added_to_hass(self):
        self._device.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        self._device.remove_callback(self.async_write_ha_state)
