# BLUETTI Local Wi-Fi for Home Assistant

[Documentazione italiana](README.md) · [Network setup](docs/network.md)

Read-only, local Wi-Fi telemetry for the **BLUETTI Elite 30 V2 (EL30V2)**. No cloud account or Bluetooth connection is required by this integration. It is derived in part from the official BLUETTI integration, with a separate `bluetti_local` domain and an independently implemented local transport.

**Preview release 0.1.1.** Tested with EL30V2 IoT firmware **8024.14** and Home Assistant **2026.10.4**. Other models, firmware versions and regions have not been validated. Long-term reliability over days or weeks has not yet been established. This is an independent project, not an official BLUETTI product.

## What it provides

- Battery percentage, AC/DC output power, grid/DC input power and grid voltage.
- Total input/output power calculated from the same complete report.
- A last-valid-sample timestamp and a local telemetry connection sensor.
- Availability based on a live connection and a complete, CRC-checked report no older than 90 seconds.
- Bundled brand icons served locally by Home Assistant.

There are no AC/DC output switches, firmware actions, reboot commands or automatic shutdown services. The component only emits bootstrap and MQTT transport acknowledgements. It does not forward cloud commands.

## Install

In **HACS → Custom repositories**, add:

```text
https://github.com/citroale90/ha-bluetti-local
```

Select **Integration**, download **BLUETTI Local Wi-Fi**, enable preview versions if necessary, and restart Home Assistant. This is a custom repository, not a claim of inclusion in the default HACS catalog.

For a manual installation, copy `custom_components/bluetti_local` into your Home Assistant configuration's `custom_components` directory and restart Home Assistant.

Then open **Settings → Devices & services → Add integration → BLUETTI Local Wi-Fi**. Enter a device name, the numeric device serial without the `EL30V2` prefix, the UPS's reserved IPv4 address, a local IPv4 address belonging to the Home Assistant host, and the receiver ports:

| Setting | Default |
| --- | --- |
| Local data receiver | TCP 18760 |
| UPS-only DNS receiver | UDP/TCP 25354 |

**Network configuration is required.** The UPS normally opens outbound connections to BLUETTI. Your router must redirect only this UPS's data and DNS traffic to the local receivers. Installing the component does not change your router. See the [network guide](docs/network.md).

The validated topology runs Home Assistant Container with host networking on a Linux router. A separate Home Assistant host or Docker bridge network may require additional routing or SNAT; these topologies have not been validated. Do not apply the example unchanged to them.

## Reconnection and offline operation

The local DNS receiver supports the observed gateway/broker names `gw.bluettipower.com`, `gwpry.bluettipower.com`, `iotde.bluettipower.com` and `iot.bluettipower.com`, without an upstream DNS dependency. Supporting the historical generic broker alone is insufficient after a full reconnect.

The returned routing address is `47.254.169.86`; the UPS-only router DNAT keeps the connection local. Keeping the original routing address also avoids leaving a private receiver address cached in the UPS when reverting to its original network path.

The bootstrap response matches the observed vendor timestamp format, UTC minus eight hours. This affects only the wire handshake. Home Assistant telemetry timestamps remain normal UTC and are displayed in Home Assistant's configured timezone.

While local routing is enabled, the BLUETTI cloud app may show the device offline. Reverting requires removing the dedicated data and DNS rules and closing/reloading the local connection. No UPS power interruption is required for this network change.

## Validated behavior and limits

The initial validation includes **53 isolated integration tests** and real, coherent readings after both a Home Assistant restart and an integration reload. The tests cover framing, CRC, incomplete/replayed/stale reports, socket reconnection, local DNS and Home Assistant entity lifecycle. No UPS output change or power-loss test was performed.

Existing automations must use the new entity identifiers or be migrated explicitly. Missing/stale readings are unavailable, not zero battery or proof of a blackout. This repository does not include a host shutdown watchdog or the separate Bluetooth library fixes.

The observed MQTT transport is unencrypted and intended for a trusted LAN. Source-IP filtering and report CRC are not cryptographic authentication. Do not expose the listeners to the internet. MQTT CONNECT credentials are handled only in memory; raw payloads and credentials are not logged. Component diagnostics redact identifiers and addresses.

## Run the tests

On a Linux host with a Home Assistant image already present:

```bash
bash tools/test-in-homeassistant.sh
```

Or select an existing local image explicitly:

```bash
HA_IMAGE=ghcr.io/home-assistant/home-assistant:2026.10.4 bash tools/test-in-homeassistant.sh
```

The test container has no network access, no devices, a read-only root filesystem and no mount of your running Home Assistant configuration. The script does not pull images.

## License and attribution

Code is MIT licensed. Original BLUETTI copyright notices are retained; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [provenance.json](provenance.json). Brand images remain the property of their respective owners; their source is recorded in [brand/README.md](custom_components/bluetti_local/brand/README.md).
