"""Small Linux nftables adapter. Own table only; no shell, binaries or daemons.

Uses the stable Linux nfnetlink ABI. Every change is an atomic nftables batch.
It never changes an existing firewall table, host policy, routes or sysctls.
"""
from __future__ import annotations

import errno
import hashlib
import ipaddress
from pathlib import Path
import socket
import struct
import sys

OWNER = b"bluetti_local:v1"
NETLINK_NETFILTER = 12
SUBSYSTEM = 10
REQUEST, ACK, DUMP, CREATE, EXCL, APPEND = 1, 4, 0x300, 0x400, 0x200, 0x800
NESTED = 0x8000


class NetworkError(Exception):
    """A bounded error code suitable for the setup UI, never raw packet data."""


def aligned(size):
    return (size + 3) & ~3


def attr(kind, value):
    value = bytes(value)
    length = 4 + len(value)
    return struct.pack("=HH", length, kind) + value + b"\0" * (aligned(length) - length)


def attrs(data):
    result = {}
    offset = 0
    while offset + 4 <= len(data):
        length, kind = struct.unpack_from("=HH", data, offset)
        if length < 4 or offset + length > len(data):
            raise NetworkError("invalid_netlink_reply")
        result[kind & 0x3FFF] = data[offset + 4:offset + length]
        offset += aligned(length)
    if offset != len(data):
        raise NetworkError("invalid_netlink_reply")
    return result


def expression_signature(data):
    """Normalise the kernel's padding/defaults while preserving expression order."""
    result, offset, width = [], 0, None
    def integer(value):
        if len(value) != 4:
            raise NetworkError("invalid_netlink_reply")
        return struct.unpack(">I", value)[0]
    def exact_value(value, length):
        if len(value) < length or any(value[length:]):
            raise NetworkError("managed_table_changed")
        return value[:length]
    while offset + 4 <= len(data):
        size, _ = struct.unpack_from("=HH", data, offset)
        if size < 4 or offset + size > len(data):
            raise NetworkError("invalid_netlink_reply")
        entry = attrs(data[offset + 4:offset + size])
        name, fields = entry.get(1), attrs(entry.get(2, b""))
        if name == b"meta\0":
            values = {key: integer(value) for key, value in fields.items()}
            width = 16
        elif name == b"payload\0":
            values = {key: integer(value) for key, value in fields.items()}
            width = values[4]
        elif name == b"cmp\0":
            if width is None:
                raise NetworkError("invalid_netlink_reply")
            values = {1: integer(fields[1]), 2: integer(fields[2]),
                      3: exact_value(attrs(fields[3])[1], width)}
        elif name == b"immediate\0":
            register = integer(fields[1])
            if register not in (1, 2):
                raise NetworkError("managed_table_changed")
            values = {1: register, 2: exact_value(attrs(fields[2])[1], 4 if register == 1 else 2)}
        elif name == b"nat\0":
            values = {key: integer(value) for key, value in fields.items()}
            values.setdefault(4, values[3])
            values.setdefault(6, values[5])
            # MAP_IPS / PROTO_SPECIFIED are inferred by the kernel from registers.
            if values.get(7, 0) & ~3:
                raise NetworkError("managed_table_changed")
            values.pop(7, None)
        else:
            raise NetworkError("managed_table_changed")
        result.append((name, tuple(sorted(values.items()))))
        offset += aligned(size)
    if offset != len(data):
        raise NetworkError("invalid_netlink_reply")
    return tuple(result)


def string(kind, value):
    return attr(kind, value.encode("ascii") + b"\0")


def u32(kind, value):
    return attr(kind, struct.pack(">I", value & 0xFFFFFFFF))


def nested(kind, value):
    return attr(kind | NESTED, value)


def expression(name, data):
    return nested(1, string(1, name) + nested(2, data))


def payload(base, offset, length, register=1):
    return expression("payload", u32(1, register) + u32(2, base) + u32(3, offset) + u32(4, length))


def compare(value, register=1):
    return expression("cmp", u32(1, register) + u32(2, 0) + nested(3, attr(1, value)))


def immediate(register, value):
    return expression("immediate", u32(1, register) + nested(2, attr(1, value)))


def matches(interface, device_ip, protocol, port):
    return b"".join([
        expression("meta", u32(1, 1) + u32(2, 6)),
        compare(interface.encode("ascii") + b"\0" * (16 - len(interface))),
        payload(1, 12, 4), compare(ipaddress.IPv4Address(device_ip).packed),
        payload(1, 9, 1), compare(bytes([protocol])),
        payload(2, 2, 2), compare(struct.pack(">H", port)),
    ])


def nat_rule(table, interface, device_ip, host, protocol, source_port, destination_port, index):
    expressions = matches(interface, device_ip, protocol, source_port)
    expressions += immediate(1, ipaddress.IPv4Address(host).packed)
    expressions += immediate(2, struct.pack(">H", destination_port))
    expressions += expression("nat", u32(1, 1) + u32(2, socket.AF_INET) + u32(3, 1) + u32(5, 2))
    return (string(1, table) + string(2, "prerouting") + nested(4, expressions)
            + attr(7, OWNER + b":" + str(index).encode()))


def message(kind, flags, seq, data=b"", *, batch=False):
    family = 0 if batch else socket.AF_INET
    body = struct.pack(">BBH", family, 0, SUBSYSTEM if batch else 0) + data
    return struct.pack("=IHHII", 16 + len(body), kind, flags, seq, 0) + body


class Netlink:
    def __init__(self, subsystem=SUBSYSTEM):
        if sys.platform != "linux":
            raise NetworkError("linux_required")
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_NETFILTER)
        self.sock.settimeout(3)
        self.sock.bind((0, 0))
        self.sequence = 0
        self.subsystem = subsystem

    def close(self):
        self.sock.close()

    def _seq(self):
        self.sequence += 1
        return self.sequence

    def receive(self, expected, *, dump=False):
        replies = []
        pending = set(expected)
        for _ in range(256):
            data = self.sock.recv(1024 * 1024)
            offset = 0
            while offset + 16 <= len(data):
                length, kind, flags, seq, _ = struct.unpack_from("=IHHII", data, offset)
                if length < 16 or offset + length > len(data):
                    raise NetworkError("invalid_netlink_reply")
                body = data[offset + 16:offset + length]
                offset += aligned(length)
                if seq not in pending:
                    continue
                if kind == 2:
                    code = struct.unpack_from("=i", body)[0]
                    if code:
                        raise OSError(-code, "netfilter_request_failed")
                    pending.discard(seq)
                elif kind == 3:
                    if flags & 0x10:
                        raise NetworkError("netfilter_dump_interrupted")
                    pending.discard(seq)
                elif kind >> 8 == self.subsystem:
                    replies.append(attrs(body[4:]))
            if not pending:
                return replies
        raise NetworkError("netfilter_reply_limit")

    def dump(self, operation, data=b""):
        seq = self._seq()
        self.sock.sendto(message((self.subsystem << 8) | operation, REQUEST | DUMP, seq, data), (0, 0))
        return self.receive({seq}, dump=True)

    def request(self, operation, data):
        seq = self._seq()
        self.sock.sendto(message((self.subsystem << 8) | operation, REQUEST | ACK, seq, data), (0, 0))
        return self.receive({seq})

    def batch(self, operations):
        begin = self._seq()
        packet = message(16, REQUEST, begin, batch=True)
        sequences = set()
        for operation, flags, data in operations:
            seq = self._seq()
            sequences.add(seq)
            packet += message((SUBSYSTEM << 8) | operation, REQUEST | ACK | flags, seq, data)
        packet += message(17, REQUEST, self._seq(), batch=True)
        self.sock.sendto(packet, (0, 0))
        return self.receive(sequences)


def capabilities():
    if sys.platform != "linux":
        return {"supported": False, "reason": "linux_required"}
    try:
        release = tuple(int(n) for n in __import__('platform').release().split('-')[0].split('.')[:2])
        if release < (4, 18):
            return {"supported": False, "reason": "kernel_too_old"}
        fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        allowed = bool(int(fields["CapEff"].strip(), 16) & (1 << 12))
        if not allowed:
            return {"supported": False, "reason": "net_admin_required"}
        connection = Netlink()
        try:
            connection.dump(1)
        finally:
            connection.close()
        return {"supported": True, "reason": None}
    except (OSError, ValueError, KeyError, NetworkError):
        return {"supported": False, "reason": "nftables_unavailable"}


class RouterRules:
    def __init__(self, identifier, config, connection_factory=Netlink):
        self.config = dict(config)
        self.table = "blt_" + hashlib.sha256(identifier.encode()).hexdigest()[:16]
        self.marker = OWNER + b":" + self.table.encode()
        self.connection_factory = connection_factory
        interface = self.config["interface"]
        if not interface or len(interface.encode("ascii")) > 15 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-" for c in interface):
            raise NetworkError("invalid_interface")
        for key in ("device_ip", "listen_host"):
            address = ipaddress.IPv4Address(self.config[key])
            if not address.is_private or address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
                raise NetworkError("local_ipv4_required")
        if self.config["device_ip"] == self.config["listen_host"]:
            raise NetworkError("different_device_receiver_required")
        for key in ("listen_port", "dns_port"):
            if type(self.config[key]) is not int or not 1024 <= self.config[key] <= 65535:
                raise NetworkError("invalid_port")

    def _state(self, netlink):
        tables = [t for t in netlink.dump(1) if t.get(1, b"").rstrip(b"\0").decode() == self.table]
        if not tables:
            return False
        if len(tables) != 1 or tables[0].get(6) != self.marker:
            raise NetworkError("table_owned_by_someone_else")
        chains = [c for c in netlink.dump(4, string(1, self.table)) if c.get(1, b"").rstrip(b"\0").decode() == self.table]
        if len(chains) != 1 or chains[0].get(3) != b"prerouting\0":
            raise NetworkError("managed_table_changed")
        hook = attrs(chains[0].get(4, b""))
        if (chains[0].get(7) != b"nat\0" or chains[0].get(5) != struct.pack('>I', 1)
                or hook.get(1) != struct.pack('>I', 0) or hook.get(2) != struct.pack('>I', (-110) & 0xFFFFFFFF)):
            raise NetworkError("managed_table_changed")
        rules = [r for r in netlink.dump(7, string(1, self.table)) if r.get(1) == self.table.encode() + b"\0"]
        expected = {OWNER + b":" + str(i).encode() for i in range(3)}
        if len(rules) != 3 or {r.get(7) for r in rules} != expected:
            raise NetworkError("managed_table_changed")
        c = self.config
        generated = {}
        for i, (proto, source, target) in enumerate([(6, 18760, c['listen_port']), (17, 53, c['dns_port']), (6, 53, c['dns_port'])]):
            rule = attrs(nat_rule(self.table, c['interface'], c['device_ip'], c['listen_host'], proto, source, target, i))
            generated[rule[7]] = expression_signature(rule[4])
        if any(expression_signature(rule[4]) != generated[rule[7]] for rule in rules):
            raise NetworkError("managed_table_changed")
        return True

    def plan(self):
        socket.if_nametoindex(self.config["interface"])
        # The receiver must belong to this namespace: no remote router assumptions.
        with socket.socket() as check:
            check.bind((self.config["listen_host"], 0))
        return {"table": self.table, "rules": 3, "interface": self.config["interface"],
                "device_ip": self.config["device_ip"], "receiver_ip": self.config["listen_host"],
                "data_port": self.config["listen_port"], "dns_port": self.config["dns_port"]}

    def reconnect_data(self):
        """Expire only this UPS's TCP/18760 conntrack entries, never a full flush.

        Existing cloud sockets otherwise bypass newly installed NAT rules. Their
        next packet is re-evaluated locally, allowing the device to reconnect.
        No raw TCP packets or commands are sent to the UPS.
        """
        connection = Netlink(subsystem=1)
        try:
            selected = []
            for record in connection.dump(1):
                original = record.get(1)
                if not original:
                    continue
                fields = attrs(original)
                ips, protocol = attrs(fields.get(1, b"")), attrs(fields.get(2, b""))
                if (ips.get(1) == ipaddress.IPv4Address(self.config['device_ip']).packed
                        and protocol.get(1) == b'\x06' and protocol.get(3) == struct.pack('>H', 18760)):
                    selected.append(nested(1, original) + (attr(18, record[18]) if 18 in record else b""))
            if len(selected) > 4:
                raise NetworkError("unexpected_ups_connections")
            for original in selected:
                try:
                    connection.request(2, original)
                except OSError as exc:
                    if exc.errno != errno.ENOENT:
                        raise
            return len(selected)
        finally:
            connection.close()

    def apply(self):
        self.plan()
        netlink = self.connection_factory()
        try:
            if self._state(netlink):
                return {"changed": False, "table": self.table}
            hook = u32(1, 0) + u32(2, -110)
            operations = [
                (0, CREATE | EXCL, string(1, self.table) + attr(6, self.marker)),
                (3, CREATE | EXCL, string(1, self.table) + string(3, "prerouting")
                 + nested(4, hook) + u32(5, 1) + string(7, "nat")),
            ]
            c = self.config
            for i, (protocol, src, dst) in enumerate([(6, 18760, c["listen_port"]), (17, 53, c["dns_port"]), (6, 53, c["dns_port"])]):
                operations.append((6, CREATE | APPEND, nat_rule(self.table, c["interface"], c["device_ip"], c["listen_host"], protocol, src, dst, i)))
            netlink.batch(operations)
            try:
                if not self._state(netlink):
                    raise NetworkError("rules_not_verified")
            except (OSError, NetworkError):
                # Only this just-created table, and only when all its ownership
                # markers still match. Never clean up a concurrent foreign edit.
                tables = [t for t in netlink.dump(1) if t.get(1) == self.table.encode() + b'\0']
                rules = [r for r in netlink.dump(7, string(1, self.table)) if r.get(1) == self.table.encode() + b'\0']
                chains = [c for c in netlink.dump(4, string(1, self.table)) if c.get(1) == self.table.encode() + b'\0']
                if (len(tables) == 1 and tables[0].get(6) == self.marker and len(chains) == 1
                        and chains[0].get(3) == b'prerouting\0' and len(rules) == 3
                        and {r.get(7) for r in rules} == {OWNER + b':' + str(i).encode() for i in range(3)}):
                    netlink.batch([(2, 0, string(1, self.table))])
                raise
            return {"changed": True, "table": self.table}
        finally:
            netlink.close()

    def remove(self):
        netlink = self.connection_factory()
        try:
            if self._state(netlink):
                netlink.batch([(2, 0, string(1, self.table))])
                return True
            return False
        finally:
            netlink.close()
