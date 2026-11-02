# Changelog

## 0.1.1 — 2026-11-03 (preview)

First public preview for EL30V2 with IoT firmware 8024.14.

- Read-only local MQTT telemetry, CRC validation and coherent report cycles.
- Eight measurement sensors, last-valid-sample timestamp and connection sensor.
- Freshness expiry and reconnect handling, including replacement of half-open sockets.
- Local UDP/TCP DNS for the bootstrap gateway and observed European broker names.
- Vendor bootstrap timestamp format, independently checked with a synthetic identifier.
- Separate integration domain, Italian/English configuration strings and bundled brand icons.
- HACS custom-repository metadata, setup instructions and reproducible isolated tests.

Validated with Home Assistant 2026.10.4: 53 isolated integration tests and real readings after restart and config-entry reload. Other device models, firmware versions, regions and long-term operation remain unverified.
