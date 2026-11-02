"""Diagnostics intentionally omit credentials, serials, addresses and topics."""
from homeassistant.components.diagnostics import async_redact_data


async def async_get_config_entry_diagnostics(hass, entry):
    return {
        "integration_version": "0.1.1",
        "model": "EL30V2",
        "connection": "wifi_local",
        "config": async_redact_data({**entry.data, **entry.options},
                                    {"serial", "device_ip", "listen_host", "name"}),
        "receiver": entry.runtime_data.receiver.diagnostics,
    }
