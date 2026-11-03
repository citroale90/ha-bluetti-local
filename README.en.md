# BLUETTI Local Wi-Fi for Home Assistant

[Documentazione italiana](README.md) · [Network setup](docs/network.md)

Read-only, local Wi-Fi telemetry for the **BLUETTI Elite 30 V2 (EL30V2)**. No cloud account or Bluetooth connection is required by this integration. It is derived in part from the official BLUETTI integration, with a separate `bluetti_local` domain and an independently implemented local transport.

**Preview release 0.3.1.** Tested with EL30V2 IoT firmware **8024.14** and Home Assistant **2026.10.4**. Other models, firmware versions and regions have not been validated. Long-term reliability over days or weeks has not yet been established. This is an independent project, not an official BLUETTI product.

## What it provides

- Battery percentage, AC/DC output power, grid/DC input power and grid voltage.
- Read-only AC/DC output enable indicators from registers 2011 and 2012.
- Total input/output power calculated from the same complete report.
- A last-valid-sample timestamp and a local telemetry connection sensor.
- Availability based on a live connection and a complete, CRC-checked report no older than 90 seconds.
- Bundled brand icons served locally by Home Assistant.

The output indicators report explicit 0/1 register values, independently of wattage. Enabled at zero watts remains enabled. Each complete status report has its own CRC, cycle and 90-second freshness; it cannot renew numeric telemetry or watchdog freshness. Missing/stale reports and a reconnect without a new report leave those indicators unavailable. Unsupported flag values are ignored without renewing freshness.

The register map is cross-checked with [the EL30V2 reference](https://github.com/Patrick762/bluetti-bt-lib/blob/main/bluetti_bt_lib/devices/el30v2.py), recorded Wi-Fi reports and all four synthetic flag combinations. Version 0.3.1 passes 95 isolated tests. Physical validation reads existing states without toggling either output.

There are no writable AC/DC output switches, firmware actions, reboot commands or automatic shutdown services. The component only emits bootstrap and MQTT transport acknowledgements. It does not forward cloud commands.

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

## Network setup inside the integration

Choose **Automatic** if Home Assistant runs on the Linux router used by the UPS, shares its network namespace, and already has `NET_ADMIN`. Select the incoming LAN interface and confirm the displayed UPS-only scope. The component then manages its own three routing rules directly through the Linux kernel. **No router helper, external script, systemd service, MQTT broker, SSH account or additional package is installed.**

The receiver starts before the rules. Two new CRC-checked reports must arrive within three minutes; otherwise newly created rules are removed. If an existing cloud socket prevents reconnection, the component may expire only the UPS’s TCP/18760 connection-tracking entries. It never flushes connection tracking globally. Confirmed rules are rechecked every 30 seconds and restored after an external firewall reload, using Home Assistant’s scheduler.

The setup flow checks privileges and the local listening address before applying anything. HACS cannot grant kernel privileges or configure a separate router. Automatic mode is therefore **not universal plug-and-play**: on unsupported installations choose **Manual**, which preserves the previous behavior and uses rules configured on your router. See the [network guide](docs/network.md).

Stopping, reloading, disabling or removing the integration deletes its owned rules, including verified rules. Cancelling the background task cleans up its policy too. No startup firewall block is installed. Automatic mode retains the original vendor address in DNS answers, allowing the UPS to attempt its original route after redirects are removed. A forced process termination that cannot run cleanup may leave rules in the kernel until explicit cleanup or host restart. External manual rules remain independent.

The validated topology runs Home Assistant Container with host networking on a Linux router. A separate Home Assistant host or Docker bridge network may require additional routing or SNAT; these topologies have not been validated. Do not apply the example unchanged to them.

A data socket is also closed after 90 seconds without a new complete report, even if MQTT keepalives continue. Only the data session reconnects; no UPS output or power action is performed.

## Direct DNS with a stock router

Choose **Direct DNS** to receive MQTT on a separate Home Assistant host without router DNAT or NET_ADMIN. Reserve the UPS/receiver addresses; the receiver must be reachable on TCP **18760**.

- **External DNS:** configure an existing local resolver to rewrite the four observed BLUETTI gateway/broker names below to the Home Assistant IPv4 address. Ensure these names do not retain cloud AAAA answers. No built-in DNS socket is opened. Scope rewrites to this UPS where supported; global rewrites may affect other BLUETTI units.
- **Built-in DNS:** the component listens on UDP/TCP **53** and returns its local receiver address. The port must be free, bindable and reachable from the LAN. Assign this DNS **only to the UPS**, using a per-device DHCP setting. It only serves that source address and those four names: do not assign it as the whole home's DNS or put a router DNS proxy in front of it.

The integration does not reconfigure an arbitrary router's DHCP/DNS. Confirm those settings in its guided flow, then verify two fresh complete reports. No external MQTT broker is needed. Home Assistant OS/container networking and DNS-port exposure still require checking; HACS cannot grant privileges or publish ports.

External DNS rewrites survive unloading the integration. Cloud fallback while HA is off depends on those settings and the UPS cache, and is not guaranteed by Direct DNS. This integration does not impose a general Internet block.

A separate-host, direct-DNS topology passed real DNS/MQTT and reconnection checks in isolated Linux namespaces. Physical EL30V2 validation uses Automatic mode; Direct DNS on the physical UPS has not yet been validated.

## Reconnection and offline operation

The local DNS receiver supports the observed gateway/broker names `gw.bluettipower.com`, `gwpry.bluettipower.com`, `iotde.bluettipower.com` and `iot.bluettipower.com`, without an upstream DNS dependency. Supporting the historical generic broker alone is insufficient after a full reconnect.

The returned routing address is `47.254.169.86`; the UPS-only router DNAT keeps the connection local. Keeping the original routing address also avoids leaving a private receiver address cached in the UPS when reverting to its original network path.

The bootstrap response matches the observed vendor timestamp format, UTC minus eight hours. This affects only the wire handshake. Home Assistant telemetry timestamps remain normal UTC and are displayed in Home Assistant's configured timezone.

While local routing is enabled, the BLUETTI cloud app may show the device offline. Reverting requires removing the dedicated data and DNS rules and closing/reloading the local connection. No UPS power interruption is required for this network change.

## Validated behavior and limits

The original transport passed 53 isolated integration tests and real telemetry checks. Version 0.3.0 passes **82 isolated integration tests**, plus a real Linux namespace test of UDP/TCP DNS, MQTT routing, client isolation, idempotence, scoped connection expiry and rule removal. Live validation on 3 November confirmed automatic mode, fresh real UPS readings after removal of the old manual rules, and successful integration reload. The tests cover framing, CRC, incomplete/replayed/stale reports, socket reconnection, local DNS and Home Assistant entity lifecycle. No UPS output change or power-loss test was performed.

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
