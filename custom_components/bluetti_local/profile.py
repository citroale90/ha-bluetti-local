"""Known EL30V2 readable registers; no writable entities or inferred meanings.

Field formats independently implemented from the documented V2 register map
(see provenance.json and NOTICE). Wire reports do not carry entity descriptions. Extending
support to unknown registers/models therefore requires a verified profile.
"""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Field:
    key: str
    code: str
    address: int
    kind: str = "uint"
    words: int = 1
    unit: str | None = None
    device_class: str | None = None
    icon: str | None = None
    diagnostic: bool = False
    legacy: bool = False
    maximum: int = 65535


FIELDS = (
    Field("battery_pct", "SOC", 102, maximum=100, legacy=True),
    Field("remaining_hours", "TimeRemaining", 104, "remaining", unit="h", device_class="duration", icon="mdi:timer-sand"),
    Field("reported_model", "ReportedModel", 110, "model", 6, diagnostic=True, icon="mdi:information-outline"),
    Field("reported_serial", "ReportedSerial", 116, "serial", 4, diagnostic=True, icon="mdi:identifier"),
    Field("dc_output_w", "DCLoadAllTotalPower", 140, maximum=6000, legacy=True),
    Field("ac_output_w", "ACLoadAllTotalPower", 142, maximum=6000, legacy=True),
    Field("dc_input_w", "PVAllTotalPower", 144, maximum=6000, legacy=True),
    Field("grid_input_w", "GridAllTotalPower", 146, maximum=6000, legacy=True),
    Field("grid_voltage_v", "GridInputVoltage", 1314, "voltage", maximum=3000, legacy=True),
    Field("ac_output_enabled", "SetCtrlAc", 2011, "bool", legacy=True),
    Field("dc_output_enabled", "SetCtrlDc", 2012, "bool", legacy=True),
    Field("eco_dc_enabled", "EcoDc", 2014, "bool", icon="mdi:leaf"),
    Field("eco_dc_hours", "EcoDcHours", 2015, "eco_hours", unit="h", device_class="duration", icon="mdi:timer-outline"),
    Field("eco_dc_threshold_w", "EcoDcThreshold", 2016, unit="W", device_class="power", icon="mdi:flash-outline"),
    Field("eco_ac_enabled", "EcoAc", 2017, "bool", icon="mdi:leaf"),
    Field("eco_ac_hours", "EcoAcHours", 2018, "eco_hours", unit="h", device_class="duration", icon="mdi:timer-outline"),
    Field("eco_ac_threshold_w", "EcoAcThreshold", 2019, unit="W", device_class="power", icon="mdi:flash-outline"),
    Field("charging_mode", "ChargingMode", 2020, "charging", device_class="enum", icon="mdi:battery-charging"),
    Field("power_lifting_enabled", "PowerLifting", 2021, "bool", icon="mdi:lightning-bolt"),
)
PROFILE = {field.key: field for field in FIELDS}
OPTIONAL_FIELDS = tuple(field for field in FIELDS if not field.legacy)
CHARGING_OPTIONS = ("standard", "silent", "turbo")


def decode_field(field, data, expected_serial):
    """Decode only a complete field, rejecting unsupported/invalid values."""
    if len(data) != field.words * 2:
        raise ValueError("incomplete field")
    if field.kind == "model":
        swapped = b"".join(data[i:i + 2][::-1] for i in range(0, len(data), 2))
        try:
            model = swapped.rstrip(b"\0").decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid model") from exc
        if model != "EL30V2":
            raise ValueError("unexpected model")
        return model
    if field.kind == "serial":
        value = sum(word << (16 * i) for i, word in enumerate(struct.unpack(">4H", data)))
        if str(value) != str(expected_serial):
            raise ValueError("unexpected serial")
        return str(value)
    raw = int.from_bytes(data, "big")
    if field.kind == "bool":
        if raw not in (0, 1):
            raise ValueError("unsupported flag")
        return bool(raw)
    if field.kind == "eco_hours":
        if raw not in (1, 2, 3, 4):
            raise ValueError("unsupported ECO duration")
        return raw
    if field.kind == "charging":
        if raw not in range(len(CHARGING_OPTIONS)):
            raise ValueError("unsupported charging mode")
        return CHARGING_OPTIONS[raw]
    if raw > field.maximum:
        raise ValueError("out of range")
    if field.kind == "remaining":
        return round(raw * 167 / 10000, 4)
    if field.kind == "voltage":
        return raw / 10
    return raw
