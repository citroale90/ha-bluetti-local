# Changelog

## 0.3.0 — 2026-11-03 (preview)

- Close accepted MQTT/DNS clients before waiting for listeners, preventing reload/shutdown stalls with an active connection.
- Direct DNS mode for a separate HA host, using an existing resolver or an UPS-only built-in DNS listener on port 53.
- Guided DNS setup without router privileges; MQTT remains embedded in the integration.
- No additional Internet filter or permanent firewall/DNS changes.
- Runtime-only lifecycle: stop/reload/disable/remove cleans up routing tables, with serialized removal and cancellation handling. No startup rules or external runtime service.
- 82 isolated tests and real kernel tests of separate-host DNS/MQTT and scoped router redirects; physical-device Direct DNS validation remains pending.


## 0.2.1 — 2026-11-03 (preview)

- Explicit Home Assistant stop cleanup also covers setup cancelled before its background verification starts.
- Direct cancellation rolls back newly created, unverified rules. Confirmed rules still survive a full Home Assistant restart.
- 75 isolated tests, including actual Home Assistant stop-event handling.


## 0.2.0 — 2026-11-03 (preview)

- Optional automatic network setup inside the integration for a supported Linux router.
- Native configuration/options flow with capability checks, interface selection and explicit scope confirmation.
- Embedded nfnetlink routing: no external helper, command, service, broker or new package.
- Dedicated, owned UPS-only rules with atomic creation, idempotence, ownership checks and removal.
- Verification of two new reports, rollback of unverified changes and scoped data-connection recovery.
- Native 30-second rule reconciliation and redacted routing diagnostics.
- Close and reconnect stale data sessions even when MQTT keepalives continue; valid new reports renew the deadline.
- Existing entries retain Manual mode and unchanged telemetry identifiers.
- 73 isolated tests plus real synthetic DNS/MQTT routing checks in isolated Linux namespaces.
- Live automatic-mode migration verified after removal of the previous manual rules and integration reload; existing monitoring entity identifiers retained.


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
