"""Connectivity sensor adapted from the official BLUETTI entity model."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .discovery_entity import DiscoveredEntity, setup_discovered_entities
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    device = entry.runtime_data.device
    async_add_entities([
        BluettiBinarySensor(device),
        BluettiOutputSensor(device, "SetCtrlAc", "ac_output_enabled", "mdi:power-socket-eu"),
        BluettiOutputSensor(device, "SetCtrlDc", "dc_output_enabled", "mdi:current-dc"),
    ])
    setup_discovered_entities(hass, entry, async_add_entities, DiscoveredBinarySensor, binary=True)


class BluettiBinarySensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "local_connected"
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


class BluettiOutputSensor(BluettiBinarySensor):
    """Read-only output enable state from its own fresh register report."""
    _attr_device_class = None
    _attr_entity_category = None

    def __init__(self, device, code, field, icon):
        super().__init__(device)
        self._field = field
        self._attr_unique_id = f"{device.device_id}_{code}"
        self._attr_translation_key = field
        self._attr_icon = icon

    @property
    def is_on(self):
        return self._device.output_states[self._field]

    @property
    def available(self):
        return self._device.outputs_online

    @property
    def extra_state_attributes(self):
        return self._device.output_status_attributes


class DiscoveredBinarySensor(DiscoveredEntity, BinarySensorEntity):
    """Observed ECO/Power Lifting flags; never an output switch."""
    @property
    def is_on(self):
        return self._device.discovery.observations[self._field.key].value
