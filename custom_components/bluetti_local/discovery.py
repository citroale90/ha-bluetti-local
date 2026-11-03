"""Continuous, read-only discovery from validated unsolicited register reports."""
from dataclasses import dataclass
from datetime import datetime, timezone

from .const import STALE_SECONDS
from .profile import FIELDS, PROFILE, decode_field
from .protocol import _report_registers


@dataclass
class Observation:
    value: object = None
    cycle: int | None = None
    seen_at: float | None = None
    timestamp: datetime | None = None
    count: int = 0


class Discovery:
    """Two distinct cycles confirm fields; each field has its own expiry.

    Confirmed keys persist through HA's native entity registry, never cached
    values. Optional reports cannot renew core telemetry or watchdog freshness.
    """
    def __init__(self, device):
        self.device = device
        self.observations = {field.key: Observation() for field in FIELDS}
        self.confirmed = set()
        self._callbacks = set()
        self._discovery_callbacks = set()
        self._availability = set()
        self.invalid_reports = 0
        self.unsupported_reports = 0
        self.invalid_fields = 0

    def subscribe(self, callback, *, discovery=False):
        callbacks = self._discovery_callbacks if discovery else self._callbacks
        callbacks.add(callback)
        return lambda: callbacks.discard(callback)

    def notify(self):
        self._availability = {key for key in self.confirmed if self.available(key)}
        for callback in tuple(self._callbacks):
            callback()

    def restore(self, keys):
        self.confirmed.update(key for key in keys if key in PROFILE)

    def receive(self, payload):
        # The UPS also publishes service/status envelopes (e.g. function 0x49).
        # They carry no register measurements and are not corrupt sensor data.
        if len(payload) >= 12 and payload[:2] == b"\x01\xf8" and payload[10:12] != b"\x01\x10":
            self.unsupported_reports += 1
            return
        try:
            cycle, start, count = _report_registers(payload)
        except ValueError:
            self.invalid_reports += 1
            return
        now = self.device._clock()
        changed = False
        discovered = False
        for field in FIELDS:
            # Do not join fragments across reports/cycles for string identities.
            if not (start <= field.address and field.address + field.words <= start + count):
                continue
            observation = self.observations[field.key]
            if observation.cycle is not None:
                delta = (cycle - observation.cycle) % 65536
                if not 0 < delta <= 32767:
                    continue
            offset = 17 + (field.address - start) * 2
            try:
                value = decode_field(field, payload[offset:offset + field.words * 2], self.device.sn)
            except ValueError:
                self.invalid_fields += 1
                continue
            # Two recent, distinct valid cycles are required on first discovery.
            if observation.seen_at is None or not 0 <= now - observation.seen_at <= STALE_SECONDS:
                observation.count = 0
            observation.value, observation.cycle = value, cycle
            observation.seen_at = now
            observation.timestamp = datetime.now(timezone.utc)
            observation.count += 1
            changed = True
            if observation.count >= 2 and field.key not in self.confirmed:
                self.confirmed.add(field.key)
                discovered = True
        if discovered:
            for callback in tuple(self._discovery_callbacks):
                callback()
        if changed:
            self.notify()

    def available(self, key):
        observation = self.observations[key]
        return (self.device.online and observation.seen_at is not None
                and 0 <= self.device._clock() - observation.seen_at <= STALE_SECONDS)

    def disconnect(self):
        for observation in self.observations.values():
            observation.seen_at = None
            observation.cycle = None
            observation.count = 0
        self.notify()

    def check_freshness(self):
        current = {key for key in self.confirmed if self.available(key)}
        if current != self._availability:
            self.notify()

    def attributes(self, key):
        observation = self.observations[key]
        return {"transport": "wifi_local", "read_only": True,
                "validated_crc": observation.timestamp is not None,
                "last_successful_poll": observation.timestamp.isoformat() if observation.timestamp else None,
                "report_cycle": observation.cycle, "stale_after_seconds": STALE_SECONDS,
                "discovered_automatically": True}

    @property
    def diagnostics(self):
        # Keys/counts only: never include serial, raw payloads or field values.
        return {"profile": "EL30V2", "known_readable_fields": len(FIELDS),
                "observed_fields": sorted(key for key, obs in self.observations.items() if obs.timestamp),
                "confirmed_fields": sorted(self.confirmed),
                "fresh_fields": sorted(key for key in self.confirmed if self.available(key)),
                "invalid_reports": self.invalid_reports, "invalid_fields": self.invalid_fields,
                "unsupported_service_reports": self.unsupported_reports}
