"""Local edition of the official BLUETTI integration's push sensor model.

Copyright (c) 2025 Bluetti. Local modifications: see LICENSE and NOTICE.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, CONF_NAME, CONF_SERIAL, CONF_DEVICE_IP, CONF_LISTEN_HOST, CONF_LISTEN_PORT, CONF_DNS_PORT, DEFAULT_DNS_PORT
from .models import BluettiDevice
from .server import LocalReceiver

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class BluettiData:
    device: BluettiDevice
    receiver: LocalReceiver


async def async_setup_entry(hass, entry: ConfigEntry) -> bool:
    config = {**entry.data, **entry.options}
    device = BluettiDevice(config[CONF_SERIAL], config[CONF_NAME])
    receiver = LocalReceiver(hass, device, config[CONF_LISTEN_HOST], config[CONF_LISTEN_PORT], config[CONF_DEVICE_IP], config.get(CONF_DNS_PORT, DEFAULT_DNS_PORT))
    try:
        await receiver.start()
    except OSError as exc:
        raise ConfigEntryNotReady("Local receiver address or port unavailable") from exc
    entry.runtime_data = BluettiData(device, receiver)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await receiver.stop()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.receiver.stop()
        return True
    return False
