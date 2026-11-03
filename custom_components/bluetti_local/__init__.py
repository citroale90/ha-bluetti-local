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
from .network import RouterManager
from .netfilter import RouterRules
from .const import CONF_NETWORK_MODE, NETWORK_AUTOMATIC, NETWORK_MANUAL

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class BluettiData:
    device: BluettiDevice
    receiver: LocalReceiver
    router: RouterManager | None = None


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
        if config.get(CONF_NETWORK_MODE, NETWORK_MANUAL) == NETWORK_AUTOMATIC:
            manager = RouterManager(hass, entry.unique_id, config, device)
            entry.runtime_data.router = manager
            await manager.start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        if entry.runtime_data.router:
            await entry.runtime_data.router.stop()
        await receiver.stop()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    manager = entry.runtime_data.router
    new = {**entry.data, **entry.options}
    if manager and (new.get(CONF_NETWORK_MODE, NETWORK_MANUAL) != NETWORK_AUTOMATIC or any(
            manager.rules.config.get(key) != new.get(key) for key in ("device_ip", "listen_host", "listen_port", "dns_port", "interface"))):
        await manager.stop(remove=True)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if entry.runtime_data.router:
            await entry.runtime_data.router.stop(remove=not hass.is_stopping)
        await entry.runtime_data.receiver.stop()
        return True
    return False


async def async_remove_entry(hass, entry):
    config = {**entry.data, **entry.options}
    if config.get(CONF_NETWORK_MODE, NETWORK_MANUAL) == NETWORK_AUTOMATIC:
        await hass.async_add_executor_job(RouterRules(entry.unique_id, config).remove)
