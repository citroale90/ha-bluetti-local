"""HA entity registry persistence and continuous addition of discovered fields."""
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.const import EntityCategory

from .const import DOMAIN
from .profile import OPTIONAL_FIELDS


def setup_discovered_entities(hass, entry, async_add_entities, entity_class, *, binary):
    device = entry.runtime_data.device
    discovery = device.discovery
    fields = [field for field in OPTIONAL_FIELDS if (field.kind == "bool") == binary]
    existing = {row.unique_id for row in er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id) if row.platform == DOMAIN}
    discovery.restore(field.key for field in fields if f"{device.device_id}_{field.code}" in existing)
    added = set()

    @callback
    def add_confirmed():
        ready = [field for field in fields if field.key in discovery.confirmed and field.key not in added]
        added.update(field.key for field in ready)
        if ready:
            async_add_entities([entity_class(device, field) for field in ready])

    entry.async_on_unload(discovery.subscribe(add_confirmed, discovery=True))
    add_confirmed()


class DiscoveredEntity:
    """Shared identity/availability; all controls represented as read-only state."""
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device, field):
        self._device = device
        self._field = field
        self._unsubscribe = None
        self._attr_unique_id = f"{device.device_id}_{field.code}"
        self._attr_translation_key = field.key
        self._attr_icon = field.icon
        self._attr_entity_category = EntityCategory.DIAGNOSTIC if field.diagnostic else None
        self._attr_device_info = {"identifiers": {(DOMAIN, device.device_id)}, "name": device.name,
                                  "manufacturer": device.manufacturer, "model": device.model}

    @property
    def available(self):
        return self._device.discovery.available(self._field.key)

    @property
    def extra_state_attributes(self):
        return self._device.discovery.attributes(self._field.key)

    async def async_added_to_hass(self):
        self._unsubscribe = self._device.discovery.subscribe(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
