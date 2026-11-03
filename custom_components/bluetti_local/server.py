"""Local EL30V2 data receiver; emits transport acknowledgements only."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import logging

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback

from .models import BluettiDevice
from .protocol import Protocol, MAX_PACKET
from .local_dns import LocalDNS, BROKER_ROUTING_ADDRESS
from .const import DEFAULT_DNS_PORT, STALE_SECONDS

_LOGGER = logging.getLogger(__name__)
SAMPLE_TIMEOUT_SECONDS = STALE_SECONDS


class LocalReceiver:
    def __init__(self, hass, device: BluettiDevice, listen_host: str, port: int, device_ip: str, dns_port: int = DEFAULT_DNS_PORT, *, direct_dns=False, external_dns=False):
        self.hass = hass
        self.device = device
        self.listen_host = listen_host
        self.port = port
        self.device_ip = device_ip
        self._server = None
        self._writer = None
        self._tasks = set()
        self._remove_freshness_check = None
        self._stopping = False
        self.rejected_connections = 0
        self.protocol_errors = 0
        self.telemetry_reconnects = 0
        self.dns = None if external_dns else LocalDNS(listen_host, dns_port, device_ip, listen_host if direct_dns else BROKER_ROUTING_ADDRESS)
        self.direct_dns = direct_dns

    async def start(self):
        self._server = await asyncio.start_server(
            self._client, self.listen_host, self.port, limit=MAX_PACKET
        )
        try:
            if self.dns:
                await self.dns.start()
        except Exception:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            raise
        # A five-second local check changes state only at the freshness boundary.
        # It sends no traffic to the UPS and does not poll the network.
        self._remove_freshness_check = async_track_time_interval(
            self.hass, self._check_freshness, timedelta(seconds=5)
        )

    @callback
    def _check_freshness(self, now):
        """Publish availability from the Home Assistant event loop."""
        self.device.check_freshness()

    async def stop(self):
        self._stopping = True
        if self.dns:
            await self.dns.stop()
        if self._remove_freshness_check:
            self._remove_freshness_check()
            self._remove_freshness_check = None
        if self._server:
            self._server.close()
        if self._writer:
            self._writer.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._server:
            # Python 3.13+ waits for accepted transports too: close clients
            # before waiting, otherwise a live UPS can stall reload/shutdown.
            await self._server.wait_closed()
            self._server = None
        self.device.disconnect()

    async def _client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        if self._stopping or not peer or peer[0] != self.device_ip:
            self.rejected_connections += 1
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)
            return
        task = asyncio.current_task()
        # After a Wi-Fi drop the UPS can open a new socket while the old TCP
        # session still appears alive. Accept its replacement immediately;
        # an old handler must not invalidate a newer session on exit.
        if self._writer is not None:
            self._writer.close()
            for previous in tuple(self._tasks):
                previous.cancel()
            self.device.disconnect()
        self._tasks.add(task)
        self._writer = writer
        protocol = Protocol(self.device.accept_sample, expected_serial=self.device.sn)
        handshake_deadline = asyncio.get_running_loop().time() + 15
        sample_deadline = asyncio.get_running_loop().time() + SAMPLE_TIMEOUT_SECONDS
        try:
            while not self._stopping:
                # MQTT keepalives or incomplete/duplicate reports must not keep
                # a stalled telemetry socket open indefinitely. Close only the
                # data session so the UPS can perform its normal reconnect.
                timeout = sample_deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    self.telemetry_reconnects += 1
                    break
                if not protocol.model_seen:
                    timeout = min(timeout, handshake_deadline - asyncio.get_running_loop().time())
                    if timeout <= 0:
                        raise TimeoutError
                samples_before = self.device.valid_samples
                data = await asyncio.wait_for(reader.read(4096), timeout)
                if not data:
                    break
                for response in protocol.feed(data):
                    writer.write(response)
                if self.device.valid_samples > samples_before:
                    sample_deadline = asyncio.get_running_loop().time() + SAMPLE_TIMEOUT_SECONDS
                await asyncio.wait_for(writer.drain(), 5)
        except asyncio.CancelledError:
            raise
        except (ValueError, OSError, EOFError, TimeoutError):
            # MQTT CONNECT contains credentials. Never log payloads, exceptions
            # containing packet contents, identities or topics, even at DEBUG.
            self.protocol_errors += 1
            if asyncio.get_running_loop().time() >= sample_deadline:
                self.telemetry_reconnects += 1
            _LOGGER.debug("Local UPS data connection ended; waiting for reconnection")
        finally:
            if self._writer is writer:
                self._writer = None
                self.device.disconnect()
            self._tasks.discard(task)
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)

    @property
    def diagnostics(self):
        return {
            "listening": self._server is not None,
            "transport_connected": self._writer is not None,
            "fresh_sample_available": self.device.online,
            "valid_samples": self.device.valid_samples,
            "protocol_errors": self.protocol_errors,
            "telemetry_reconnects": self.telemetry_reconnects,
            "rejected_connections": self.rejected_connections,
            "read_only": True,
            "local_dns_answers": self.dns.answers if self.dns else 0,
            "dns_source": "builtin" if self.dns else "external",
            "dns_answer_mode": "receiver_address" if self.direct_dns else "router_redirect",
        }
