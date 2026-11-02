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
from .local_dns import LocalDNS
from .const import DEFAULT_DNS_PORT

_LOGGER = logging.getLogger(__name__)


class LocalReceiver:
    def __init__(self, hass, device: BluettiDevice, listen_host: str, port: int, device_ip: str, dns_port: int = DEFAULT_DNS_PORT):
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
        self.dns = LocalDNS(listen_host, dns_port, device_ip)

    async def start(self):
        self._server = await asyncio.start_server(
            self._client, self.listen_host, self.port, limit=MAX_PACKET
        )
        try:
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
        await self.dns.stop()
        if self._remove_freshness_check:
            self._remove_freshness_check()
            self._remove_freshness_check = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._writer:
            self._writer.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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
        try:
            while not self._stopping:
                timeout = 90
                if not protocol.model_seen:
                    timeout = handshake_deadline - asyncio.get_running_loop().time()
                    if timeout <= 0:
                        raise TimeoutError
                data = await asyncio.wait_for(reader.read(4096), timeout)
                if not data:
                    break
                for response in protocol.feed(data):
                    writer.write(response)
                await asyncio.wait_for(writer.drain(), 5)
        except asyncio.CancelledError:
            raise
        except (ValueError, OSError, EOFError, TimeoutError):
            # MQTT CONNECT contains credentials. Never log payloads, exceptions
            # containing packet contents, identities or topics, even at DEBUG.
            self.protocol_errors += 1
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
            "rejected_connections": self.rejected_connections,
            "read_only": True,
            "local_dns_answers": self.dns.answers,
        }
