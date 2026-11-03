"""Local telemetry model derived from BLUETTI 1.0.5 models.py.

Copyright (c) 2025 Bluetti. Local modifications: see LICENSE and NOTICE.
The official state/device/callback model is retained for push sensor updates.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import time

from .const import MODEL, SENSORS, STALE_SECONDS
from .protocol import FIELD_MAP, OUTPUT_FIELD_MAP
from .discovery import Discovery


class BluettiState:
    """A single official-style device function/state."""

    def __init__(self, fn_code, fn_name, fn_value=None, fn_type="SENSOR", sensor_info=None):
        self.fn_code = fn_code
        self.fn_name = fn_name
        self.fn_value = fn_value
        self.fn_type = fn_type
        self.sensor_info = sensor_info or {}

    def set_value(self, value):
        self.fn_value = value


class BluettiDevice:
    """Official callback-based entity model with strictly fresh local samples."""

    def __init__(self, serial: str, name: str, monotonic: Callable = time.monotonic):
        self.device_id = serial
        self.sn = serial
        self.name = name
        self.model = MODEL
        self.manufacturer = "Bluetti"
        self.states = [BluettiState(code, title, sensor_info={"sensorType": kind})
                       for code, _, title, kind in SENSORS]
        self._callbacks: set[Callable[[], None]] = set()
        self._clock = monotonic
        self._sample_time = None
        self._connected = False
        self._announced_online = False
        self.last_successful_poll: datetime | None = None
        self.report_cycle = None
        self.valid_samples = 0
        self.output_states = {name: None for name in OUTPUT_FIELD_MAP}
        self._output_sample_time = None
        self._announced_outputs_online = False
        self.last_output_status = None
        self.output_report_cycle = None
        self.valid_output_samples = 0
        self.discovery = Discovery(self)

    def get_state(self, fn_code):
        return next((s for s in self.states if s.fn_code == fn_code), None)

    def register_callback(self, callback):
        self._callbacks.add(callback)

    def remove_callback(self, callback):
        self._callbacks.discard(callback)

    def publish_updates(self):
        self._announced_online = self.online
        self._announced_outputs_online = self.outputs_online
        self.discovery.notify()
        for callback in tuple(self._callbacks):
            callback()

    @property
    def online(self):
        if not self._connected or self._sample_time is None:
            return False
        age = self._clock() - self._sample_time
        return 0 <= age <= STALE_SECONDS

    def accept_sample(self, sample):
        values = sample.get("values", {})
        if sample.get("validated_crc") is not True or not FIELD_MAP.keys() <= values.keys():
            raise ValueError("Incomplete or unvalidated telemetry")
        values = {**values,
                  "total_output_w": values["ac_output_w"] + values["dc_output_w"],
                  "total_input_w": values["grid_input_w"] + values["dc_input_w"]}
        for code, field, _, _ in SENSORS:
            self.get_state(code).set_value(values[field])
        self.report_cycle = sample["report_cycle"]
        self._sample_time = self._clock()
        self._connected = True
        self.last_successful_poll = datetime.now(timezone.utc)
        self.valid_samples += 1
        self.publish_updates()

    @property
    def outputs_online(self):
        if not self.online or self._output_sample_time is None:
            return False
        return 0 <= self._clock() - self._output_sample_time <= STALE_SECONDS

    def accept_output_status(self, sample):
        values = sample.get("values", {})
        if (sample.get("validated_crc") is not True
                or not OUTPUT_FIELD_MAP.keys() <= values.keys()
                or any(type(values[name]) is not bool for name in OUTPUT_FIELD_MAP)):
            raise ValueError("Incomplete or unvalidated output status")
        self.output_states = {name: values[name] for name in OUTPUT_FIELD_MAP}
        self._output_sample_time = self._clock()
        self.last_output_status = datetime.now(timezone.utc)
        self.output_report_cycle = sample["report_cycle"]
        self.valid_output_samples += 1
        # These reports never renew the numeric telemetry/watchdog timestamp.
        self.publish_updates()

    def disconnect(self):
        self._connected = False
        self._output_sample_time = None
        self.discovery.disconnect()
        if self._announced_online or self._announced_outputs_online:
            self.publish_updates()

    def check_freshness(self):
        self.discovery.check_freshness()
        if (self.online != self._announced_online
                or self.outputs_online != self._announced_outputs_online):
            self.publish_updates()

    @property
    def telemetry_attributes(self):
        return {
            "transport": "wifi_local",
            "validated_crc": self.last_successful_poll is not None,
            "last_successful_poll": self.last_successful_poll.isoformat() if self.last_successful_poll else None,
            "report_cycle": self.report_cycle,
            "valid_samples": self.valid_samples,
            "stale_after_seconds": STALE_SECONDS,
            "read_only": True,
        }

    @property
    def output_status_attributes(self):
        return {
            "transport": "wifi_local",
            "validated_crc": self.last_output_status is not None,
            "last_successful_poll": self.last_output_status.isoformat() if self.last_output_status else None,
            "report_cycle": self.output_report_cycle,
            "valid_samples": self.valid_output_samples,
            "stale_after_seconds": STALE_SECONDS,
            "read_only": True,
        }
