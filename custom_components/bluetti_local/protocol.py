"""EL30V2 read-only local MQTT protocol and validated telemetry decoder.

The register framing and map were verified against this device's passive capture.
The V2 MQTT responses match an actual cloud reconnect capture byte for byte;
the local endpoint was also tested successfully with that EL30V2.
The receiver can only send transport acknowledgements, never MQTT publications
or device register/control commands. Raw identities and credentials are not logged.
"""
from __future__ import annotations

import json
import struct
import time

MAX_PACKET = 65536
FIELD_MAP = {
    'battery_pct': (102, 1),
    'dc_output_w': (140, 1),
    'ac_output_w': (142, 1),
    'dc_input_w': (144, 1),
    'grid_input_w': (146, 1),
    'grid_voltage_v': (1314, 0.1),
}
OUTPUT_FIELD_MAP = {'ac_output_enabled': 2011, 'dc_output_enabled': 2012}


def bootstrap_time():
    """Wire timestamp used by the EL30V2 bootstrap service.

    A read-only query with a synthetic identity confirmed that the vendor
    service sends Unix UTC minus eight hours. Warm MQTT reconnects skip this
    bootstrap; a full reconnect must also support the gateway DNS name.
    This offset belongs only to the wire handshake, never to HA timestamps.
    """
    return int(time.time()) - 8 * 60 * 60


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return value


def _report_registers(payload):
    if len(payload) < 19 or len(payload) > 512:
        raise ValueError('report length')
    if payload[:2] != b'\x01\xf8' or payload[10:12] != b'\x01\x10':
        raise ValueError('unsupported report type')
    start, count = struct.unpack_from('>HH', payload, 12)
    if not 1 <= count <= 127 or payload[16] != count * 2 or len(payload) != 19 + 2 * count:
        raise ValueError('register length')
    if crc16(payload[10:-2]) != int.from_bytes(payload[-2:], 'little'):
        raise ValueError('register checksum')
    return int.from_bytes(payload[2:4], 'big'), start, count


def decode_report(payload):
    cycle, start, count = _report_registers(payload)
    values = {}
    for name, (address, scale) in FIELD_MAP.items():
        if start <= address < start + count:
            raw = struct.unpack_from('>H', payload, 17 + 2 * (address - start))[0]
            values[name] = round(raw * scale, 1) if scale != 1 else raw
    if 'battery_pct' in values and not 0 <= values['battery_pct'] <= 100:
        raise ValueError('battery range')
    if 'grid_voltage_v' in values and not 0 <= values['grid_voltage_v'] <= 300:
        raise ValueError('voltage range')
    if any(v > 6000 for k, v in values.items() if k.endswith('_w')):
        raise ValueError('power range')
    return cycle, values


def decode_output_report(payload):
    """Read the EL30V2 enable flags, never infer them from output power."""
    cycle, start, count = _report_registers(payload)
    values = {}
    for name, address in OUTPUT_FIELD_MAP.items():
        if start <= address < start + count:
            raw = struct.unpack_from('>H', payload, 17 + 2 * (address - start))[0]
            if raw not in (0, 1):
                raise ValueError('unsupported output status')
            values[name] = bool(raw)
    return cycle, values


class Samples:
    """Required fields must share one fresh, checksum-validated report cycle."""
    def __init__(self, *, fields=FIELD_MAP, decoder=decode_report):
        self.fields = tuple(fields)
        self.decoder = decoder
        self.cycle = None
        self.since = None
        self.values = {}
        self.last = None
        self.last_time = None
        self.invalid_reports = 0

    def receive(self, payload, now=None):
        now = time.monotonic() if now is None else now
        try:
            cycle, values = self.decoder(payload)
        except ValueError:
            self.invalid_reports += 1
            return None
        if not values:
            return None
        if self.cycle is not None and cycle != self.cycle:
            if (cycle - self.cycle) % 65536 > 32767:
                return None  # An old report cannot become a new fresh sample.
        if cycle != self.cycle or self.since is None or now - self.since > 5:
            self.cycle, self.since, self.values = cycle, now, {}
        self.values.update(values)
        if not self.values.keys() >= set(self.fields):
            return None
        sample = {k: self.values[k] for k in self.fields}
        if not self.last or self.last['report_cycle'] != cycle:
            self.last = {'transport': 'wifi-local-experimental', 'report_cycle': cycle,
                         'values': sample, 'validated_crc': True}
            self.last_time = now
            return self.last
        return None

    def current(self, now=None):
        now = time.monotonic() if now is None else now
        if self.last_time is None or now - self.last_time > 90 or now < self.last_time:
            return None
        return self.last


def remaining_length(value):
    data = bytearray()
    while True:
        byte = value % 128
        value //= 128
        data.append(byte | (128 if value else 0))
        if not value:
            return bytes(data)


class Protocol:
    """Vendor prelude plus the transport subset needed for unsolicited reports."""
    def __init__(self, on_sample, epoch=None, expected_iot=None, expected_serial=None, *, on_output_status=None):
        self.buffer = bytearray()
        self.samples = Samples()
        self.on_sample = on_sample
        self.on_output_status = on_output_status
        self.output_samples = Samples(fields=OUTPUT_FIELD_MAP, decoder=decode_output_report)
        self.epoch = epoch or bootstrap_time
        self.expected_iot = expected_iot
        self.expected_serial = expected_serial
        self.iot_seen = False
        self.mqtt_seen = False
        self.model_seen = False
        self.publish_count = 0

    def feed(self, incoming):
        self.buffer.extend(incoming)
        if len(self.buffer) > MAX_PACKET:
            raise ValueError('buffer limit')
        replies = []
        while self.buffer:
            if self.buffer[0] == 0:
                if len(self.buffer) < 4:
                    break
                kind, size = struct.unpack_from('>HH', self.buffer)
                if size > 256:
                    raise ValueError('vendor prelude length')
                if len(self.buffer) < 4 + size:
                    break
                body = bytes(self.buffer[4:4 + size])
                del self.buffer[:4 + size]
                if kind == 1:
                    if self.expected_iot is not None and body != self.expected_iot.encode():
                        raise ValueError('unexpected communication module')
                    self.iot_seen = True
                    replies.append(b'\x00\x01\x00\x04' + self.epoch().to_bytes(4, 'big'))
                elif kind == 2 and self.mqtt_seen:
                    name = body.decode('ascii')
                    if not name.startswith('EL30V2&'):
                        raise ValueError('unexpected model')
                    if self.expected_serial is not None and name.split('&', 1)[1] != self.expected_serial:
                        raise ValueError('unexpected device')
                    self.model_seen = True
                    replies.append(b'\x00\x02\x00\x01\x01')
                else:
                    raise ValueError('vendor prelude order')
                continue
            if len(self.buffer) < 2:
                break
            length = 0
            multiplier = 1
            header = None
            for i in range(1, min(5, len(self.buffer))):
                byte = self.buffer[i]
                length += (byte & 127) * multiplier
                if not byte & 128:
                    header = i + 1
                    break
                multiplier *= 128
            if header is None:
                if len(self.buffer) >= 5:
                    raise ValueError('MQTT length encoding')
                break
            if length > MAX_PACKET - 5:
                raise ValueError('MQTT packet limit')
            if len(self.buffer) < header + length:
                break
            kind = self.buffer[0] >> 4
            flags = self.buffer[0] & 15
            body = bytes(self.buffer[header:header + length])
            del self.buffer[:header + length]
            if kind == 1:
                # The actual EL30V2 reconnects with standard MQTT directly.
                # The older AC300 vendor prelude is optional, not a prerequisite.
                if self.mqtt_seen or flags != 0 or len(body) < 12 or body[:7] != b'\x00\x04MQTT\x04':
                    raise ValueError('unsupported MQTT connection')
                connection_flags = body[7]
                if connection_flags & 0x3D or connection_flags & 0x40 and not connection_flags & 0x80:
                    raise ValueError('unsupported MQTT connect flags')
                position = 10
                identity_fields = []
                for _ in range(1 + bool(connection_flags & 0x80) + bool(connection_flags & 0x40)):
                    if position + 2 > len(body):
                        raise ValueError('truncated MQTT identity')
                    size = int.from_bytes(body[position:position + 2], 'big')
                    position += 2
                    if not size or position + size > len(body):
                        raise ValueError('invalid MQTT identity length')
                    identity_fields.append(body[position:position + size])
                    position += size
                if position != len(body):
                    raise ValueError('unexpected MQTT identity fields')
                if self.expected_iot is not None and identity_fields[0] != self.expected_iot.encode():
                    raise ValueError('unexpected MQTT communication module')
                self.mqtt_seen = True
                replies.append(b'\x20\x02\x00\x00')
            elif kind == 8:
                if not self.mqtt_seen or flags != 2 or len(body) < 5:
                    raise ValueError('invalid subscribe')
                position = 2
                grants = bytearray()
                while position < len(body):
                    size = int.from_bytes(body[position:position + 2], 'big')
                    position += 2
                    if not size or position + size + 1 > len(body):
                        raise ValueError('invalid subscription length')
                    topic = body[position:position + size]
                    position += size
                    if self.expected_serial is not None and topic == ('SUB/EL30V2/' + self.expected_serial).encode():
                        self.model_seen = True
                    qos = body[position]
                    position += 1
                    if qos > 2:
                        raise ValueError('invalid QoS')
                    grants.append(0)
                replies.append(b'\x90' + remaining_length(2 + len(grants)) + body[:2] + grants)
            elif kind == 3:
                if not self.mqtt_seen or len(body) < 2:
                    raise ValueError('report before identification')
                topic_size = int.from_bytes(body[:2], 'big')
                if not topic_size or topic_size > 256 or 2 + topic_size > len(body):
                    raise ValueError('topic size')
                topic = body[2:2 + topic_size]
                if self.expected_serial is not None:
                    expected = ('PUB/EL30V2/' + self.expected_serial).encode()
                    if topic != expected:
                        raise ValueError('unexpected report topic')
                    self.model_seen = True
                elif not self.model_seen:
                    raise ValueError('unidentified report model')
                position = 2 + topic_size
                qos = (flags >> 1) & 3
                if qos > 1:
                    raise ValueError('unsupported report QoS')
                if qos == 1:
                    if position + 2 > len(body):
                        raise ValueError('missing report id')
                    packet_id = body[position:position + 2]
                    position += 2
                    replies.append(b'\x40\x02' + packet_id)
                self.publish_count += 1
                sample = self.samples.receive(body[position:])
                if sample is not None:
                    self.on_sample(sample)
                if self.on_output_status is not None:
                    status = self.output_samples.receive(body[position:])
                    if status is not None:
                        self.on_output_status(status)
            elif kind == 12 and flags == 0 and not body and self.mqtt_seen:
                replies.append(b'\xd0\x00')
            elif kind == 14 and flags == 0 and not body:
                raise EOFError('client disconnected')
            else:
                raise ValueError('unsupported MQTT transport message')
        return replies
