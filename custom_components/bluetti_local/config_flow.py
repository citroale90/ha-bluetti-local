"""Local setup replacing the official BLUETTI OAuth2 config flow."""
from __future__ import annotations

import ipaddress
import re
import socket

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (DOMAIN, MODEL, DEFAULT_PORT, DEFAULT_DNS_PORT, CONF_DNS_PORT,
                    CONF_DEVICE_IP, CONF_LISTEN_HOST, CONF_LISTEN_PORT, CONF_NAME, CONF_SERIAL,
                    CONF_NETWORK_MODE, CONF_INTERFACE, NETWORK_MANUAL, NETWORK_AUTOMATIC)
from .netfilter import NetworkError, RouterRules, capabilities


def validate_config(data):
    result = dict(data)
    for key in (CONF_DEVICE_IP, CONF_LISTEN_HOST):
        address = ipaddress.IPv4Address(str(result[key]).strip())
        if not address.is_private or address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("A local unicast IPv4 address is required")
        result[key] = str(address)
    serial = str(result[CONF_SERIAL]).strip()
    if not re.fullmatch(r"[0-9]{6,24}", serial):
        raise ValueError("Invalid device serial")
    result[CONF_SERIAL] = serial
    name = str(result[CONF_NAME]).strip()
    if not name or len(name) > 80:
        raise ValueError("Invalid device name")
    result[CONF_NAME] = name
    port = int(result[CONF_LISTEN_PORT])
    if not 1024 <= port <= 65535:
        raise ValueError("Receiver port outside supported range")
    result[CONF_LISTEN_PORT] = port
    dns_port = int(result.get(CONF_DNS_PORT, DEFAULT_DNS_PORT))
    if not 1024 <= dns_port <= 65535 or dns_port == port:
        raise ValueError("Invalid or conflicting local DNS port")
    result[CONF_DNS_PORT] = dns_port
    if result.get(CONF_NETWORK_MODE, NETWORK_MANUAL) not in (NETWORK_MANUAL, NETWORK_AUTOMATIC):
        raise ValueError("Unsupported network mode")
    return result


def schema(defaults=None, include_serial=True):
    defaults = defaults or {}
    fields = {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, f"BLUETTI {MODEL}")): str,
        vol.Required(CONF_DEVICE_IP, description={"suggested_value": defaults.get(CONF_DEVICE_IP, "")}): str,
        vol.Required(CONF_LISTEN_HOST, description={"suggested_value": defaults.get(CONF_LISTEN_HOST, "")}): str,
        vol.Required(CONF_LISTEN_PORT, default=defaults.get(CONF_LISTEN_PORT, DEFAULT_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
        vol.Required(CONF_DNS_PORT, default=defaults.get(CONF_DNS_PORT, DEFAULT_DNS_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
        vol.Optional(CONF_NETWORK_MODE, default=defaults.get(CONF_NETWORK_MODE, NETWORK_MANUAL)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=[NETWORK_MANUAL, NETWORK_AUTOMATIC], translation_key="network_mode")),
    }
    if include_serial:
        fields[vol.Required(CONF_SERIAL, description={"suggested_value": defaults.get(CONF_SERIAL, "")})] = str
    return vol.Schema(fields)


class BluettiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = validate_config(user_input)
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_config"
            else:
                await self.async_set_unique_id(f"{MODEL}_{data[CONF_SERIAL]}")
                self._abort_if_unique_id_configured()
                for entry in self._async_current_entries():
                    other = {**entry.data, **entry.options}
                    if other[CONF_LISTEN_HOST] == data[CONF_LISTEN_HOST] and {other[CONF_LISTEN_PORT], other.get(CONF_DNS_PORT, DEFAULT_DNS_PORT)} & {data[CONF_LISTEN_PORT], data[CONF_DNS_PORT]}:
                        errors["base"] = "port_already_configured"
                        break
                if not errors:
                    if data.get(CONF_NETWORK_MODE) == NETWORK_AUTOMATIC:
                        self._pending = data
                        return await self.async_step_network()
                    return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)

    async def async_step_network(self, user_input=None):
        return await network_step(self, user_input, options=False)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BluettiOptionsFlow()


class BluettiOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            try:
                data = validate_config({**current, **user_input})
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_config"
            else:
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if entry.entry_id == self.config_entry.entry_id:
                        continue
                    other = {**entry.data, **entry.options}
                    if other[CONF_LISTEN_HOST] == data[CONF_LISTEN_HOST] and {other[CONF_LISTEN_PORT], other.get(CONF_DNS_PORT, DEFAULT_DNS_PORT)} & {data[CONF_LISTEN_PORT], data[CONF_DNS_PORT]}:
                        errors["base"] = "port_already_configured"
                        break
                if not errors:
                    if data.get(CONF_NETWORK_MODE) == NETWORK_AUTOMATIC:
                        self._pending = data
                        return await self.async_step_network()
                    return self.async_create_entry(title="", data={k: v for k, v in data.items() if k != CONF_SERIAL})
        return self.async_show_form(step_id="init", data_schema=schema({**current, **(user_input or {})}, include_serial=False), errors=errors)

    async def async_step_network(self, user_input=None):
        return await network_step(self, user_input, options=True)


async def network_step(flow, user_input, *, options):
    """Explicit scope/privilege check before any firewall modification."""
    status = await flow.hass.async_add_executor_job(capabilities)
    errors = {}
    if not status["supported"]:
        errors["base"] = status["reason"]
    if user_input is not None and not errors:
        if user_input.get("confirm") is not True:
            errors["base"] = "network_confirmation_required"
        else:
            data = {**flow._pending, CONF_INTERFACE: user_input.get(CONF_INTERFACE, "")}
            try:
                rules = RouterRules(f"{MODEL}_{data[CONF_SERIAL]}", data)
                await flow.hass.async_add_executor_job(rules.plan)
            except (ValueError, OSError, UnicodeError, NetworkError):
                errors["base"] = "invalid_router_network"
            else:
                if options:
                    return flow.async_create_entry(title="", data={k: v for k, v in data.items() if k != CONF_SERIAL})
                return flow.async_create_entry(title=data[CONF_NAME], data=data)
    interfaces = [name for _, name in socket.if_nameindex() if name != "lo"]
    fields = vol.Schema({
        vol.Required(CONF_INTERFACE, default=flow._pending.get(CONF_INTERFACE, interfaces[0] if interfaces else "")): selector.SelectSelector(
            selector.SelectSelectorConfig(options=interfaces, mode=selector.SelectSelectorMode.DROPDOWN)),
        vol.Required("confirm", default=False): bool,
    })
    return flow.async_show_form(step_id="network", data_schema=fields, errors=errors,
                               description_placeholders={"device_ip": flow._pending[CONF_DEVICE_IP], "receiver_ip": flow._pending[CONF_LISTEN_HOST]})
