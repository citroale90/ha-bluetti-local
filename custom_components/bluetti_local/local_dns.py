"""Source-restricted local broker DNS, with no upstream or recursive lookups."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import ipaddress
import struct

BROKER_NAMES = frozenset({
    "iot.bluettipower.com",
    "gw.bluettipower.com",
    "gwpry.bluettipower.com",
    "iotde.bluettipower.com",
})
# Keep the observed vendor destination in the device's DNS cache. The router's
# UPS-only DNAT keeps the connection local. Removing that rule then restores
# the original cloud path without leaving a cached private receiver address.
BROKER_ROUTING_ADDRESS = "47.254.169.86"
MAX_DNS_PACKET = 4096


def _name(packet, offset):
    labels, visited = [], set()
    end = None
    for _ in range(128):
        if offset >= len(packet) or offset in visited:
            raise ValueError("Invalid DNS name")
        visited.add(offset)
        size = packet[offset]
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("Truncated DNS pointer")
            if end is None:
                end = offset + 2
            offset = ((size & 0x3F) << 8) | packet[offset + 1]
            continue
        if size & 0xC0 or size > 63:
            raise ValueError("Invalid DNS label")
        offset += 1
        if size == 0:
            if sum(len(label) + 1 for label in labels) > 254:
                raise ValueError("DNS name too long")
            return ".".join(labels).lower(), end or offset
        if offset + size > len(packet):
            raise ValueError("Truncated DNS label")
        label = packet[offset:offset + size].decode("ascii")
        if any(not (ch.isalnum() or ch in "-_") for ch in label):
            raise ValueError("Unsupported DNS label")
        labels.append(label)
        offset += size
    raise ValueError("DNS name limit")


def answer(packet: bytes, address: str) -> bytes | None:
    """Return one small authoritative answer for the observed BLUETTI broker."""
    if not 12 <= len(packet) <= MAX_DNS_PACKET:
        return None
    try:
        ident, flags, count, _, _, _ = struct.unpack_from(">6H", packet)
        if flags & 0xF800 or count != 1:
            return None
        name, offset = _name(packet, 12)
        qtype, qclass = struct.unpack_from(">HH", packet, offset)
        labels = name.split(".") if name else []
        question = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\0" + struct.pack(">HH", qtype, qclass)
        allowed = name in BROKER_NAMES and qclass == 1 and qtype in (1, 28, 255)
        response_flags = 0x8000 | (flags & 0x0100) | (0x0400 if allowed else 5)
        record = b""
        if allowed and qtype in (1, 255):
            record = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 30, 4) + ipaddress.IPv4Address(address).packed
        return struct.pack(">6H", ident, response_flags, 1, bool(record), 0, 0) + question + record
    except (ValueError, UnicodeError, struct.error):
        return None


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self, owner):
        self.owner = owner
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if addr[0] != self.owner.device_ip:
            return
        response = answer(data, BROKER_ROUTING_ADDRESS)
        if response is not None:
            self.transport.sendto(response, addr)
            self.owner.answers += 1


class LocalDNS:
    def __init__(self, host, port, device_ip):
        self.host, self.port, self.device_ip = host, port, device_ip
        self._udp = None
        self._tcp = None
        self._tasks = set()
        self.answers = 0

    async def start(self):
        try:
            self._udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Datagrams(self), local_addr=(self.host, self.port)
            )
            self._tcp = await asyncio.start_server(self._client, self.host, self.port)
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        if self._udp:
            self._udp.close()
            self._udp = None
            # DatagramTransport releases its socket in a scheduled callback.
            # Give that callback a turn before a config-entry reload rebinds it.
            await asyncio.sleep(0)
        if self._tcp:
            self._tcp.close()
            await self._tcp.wait_closed()
            self._tcp = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        task = asyncio.current_task()
        try:
            if not peer or peer[0] != self.device_ip or len(self._tasks) >= 2:
                return
            self._tasks.add(task)
            for _ in range(4):
                size = struct.unpack(">H", await asyncio.wait_for(reader.readexactly(2), 3))[0]
                if not 12 <= size <= MAX_DNS_PACKET:
                    break
                data = await asyncio.wait_for(reader.readexactly(size), 3)
                response = answer(data, BROKER_ROUTING_ADDRESS)
                if response is None:
                    break
                writer.write(struct.pack(">H", len(response)) + response)
                await asyncio.wait_for(writer.drain(), 3)
                self.answers += 1
        except (asyncio.IncompleteReadError, OSError, TimeoutError):
            pass
        finally:
            self._tasks.discard(task)
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)
