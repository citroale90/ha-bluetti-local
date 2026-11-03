# Local network setup / Configurazione della rete

[Italian README](../README.md) · [English README](../README.en.md)

## Validated topology

The working topology runs **Home Assistant Container with host networking on the same Linux router that receives the UPS's Wi-Fi traffic**. The receiver binds to that router's LAN IPv4 address. Its source-IP restriction expects the UPS's reserved address to remain visible.

The integration receives unsolicited reports. Automatic mode manages its own scoped rules on a supported Linux router; Manual mode leaves router configuration to the administrator. Assign a DHCP reservation to the UPS before creating rules. Check that TCP 18760 and UDP/TCP 25354 are free on the chosen local address.

An installation on a separate Home Assistant host can have a different return path: a reply sent directly from that host to the UPS may bypass the router's reverse NAT. It may need additional routing/SNAT and must preserve the source address expected by the receiver. The example below is not a complete recipe for that topology. Home Assistant OS router integration is also not covered by this tested example.

## Automatic mode: one integration

In the integration setup or options, choose **Automatic**, select the incoming UPS LAN interface and confirm the displayed scope. Requirements:

- Linux kernel 4.18 or later with nftables/conntrack available.
- Home Assistant runs on the actual UPS router, sharing the host network namespace.
- The existing Home Assistant process has `NET_ADMIN`; the integration does not grant it.
- The receiver IPv4 address belongs to that namespace and is reachable from the UPS.

There is no external helper or runtime script. `netfilter.py` uses nfnetlink directly, without invoking a shell or requiring `iptables`, `nft`, `pyroute2` or other packages. Each entry owns a `blt_…` IPv4 table with three source/interface-restricted DNAT rules. Its prerouting NAT priority is -110, before the usual -100 destination-NAT priority. It never rewrites existing firewall tables or the host’s INPUT/FORWARD policies.

The component starts listeners, applies an atomic ruleset and waits for two **new** complete reports. New rules are removed after 180 seconds without confirmation, on setup failure or on cancellation. An existing, unchanged table is recognised rather than duplicated. A 30-second Home Assistant task checks only this table after verification. Unexpected modifications or ownership collisions produce an error rather than overwrite someone else’s rules.

After 20 seconds without new reports, a newly configured automatic entry may expire at most four connection-tracking entries matching exactly the UPS source IPv4 and TCP destination port 18760. This allows a cached cloud connection to be re-evaluated through the local route. No global connection flush, forced Wi-Fi disconnect, power command or new background service is used.

Stop, reload, disable or remove the entry to remove its owned rules, including verified rules. Home Assistant stopping no longer preserves the redirects. No permanent startup rule is installed. The component cannot clean up after a forced process kill; inspect/remove only its verified owned tables or restart the host if this occurs. Remove the entry before uninstalling its files.

If migrating an existing manual configuration, keep its backup and remove the exact old UPS-only rules **after** automatic mode has been verified. The component deliberately does not delete external rules; otherwise switching it off would leave those external redirects active. Do not flush shared tables or stop the networking container.

Unsupported topology or missing privileges requires Manual mode. Installing this through HACS alone cannot change another router or enable permissions on the host.

## Test automatic routing without a real UPS

On a Linux machine with `ip`, root privileges and network namespaces:

```bash
sudo unshare --net python3 tools/test-linux-router.py
```

The tool refuses the initial host namespace. It uses two private namespaces and a synthetic UPS, checks DNS/MQTT DNAT and unrelated-client/rule isolation, and never accesses a production LAN or the Internet.

## Linux router example

These are **example addresses**, not a ready-made configuration for your network:

| Variable | Example | Meaning |
| --- | --- | --- |
| `BLUETTI_LAN_IF` | `br0` | Incoming UPS LAN interface |
| `BLUETTI_UPS_IP` | `192.168.1.25` | UPS DHCP reservation |
| `BLUETTI_RECEIVER_IP` | `192.168.1.1` | LAN IP of router and Home Assistant host |

The Home Assistant integration must use the same UPS and receiver addresses. Set its data/DNS ports to 18760 and 25354 for this example. After checking the values, run the following **on the router** in a maintenance window:

```bash
BLUETTI_LAN_IF=br0
BLUETTI_UPS_IP=192.168.1.25
BLUETTI_RECEIVER_IP=192.168.1.1

sudo iptables -t nat -N BLUETTI_LOCAL
sudo iptables -t nat -A BLUETTI_LOCAL -p tcp --dport 18760 -j DNAT --to-destination "${BLUETTI_RECEIVER_IP}:18760"
sudo iptables -t nat -A BLUETTI_LOCAL -p udp --dport 53 -j DNAT --to-destination "${BLUETTI_RECEIVER_IP}:25354"
sudo iptables -t nat -A BLUETTI_LOCAL -p tcp --dport 53 -j DNAT --to-destination "${BLUETTI_RECEIVER_IP}:25354"
sudo iptables -t nat -I PREROUTING 1 -i "$BLUETTI_LAN_IF" -s "${BLUETTI_UPS_IP}/32" -j BLUETTI_LOCAL
```

The dedicated chain name must be unused. This is a one-time example: do not run it repeatedly or flush the router's tables. The narrow PREROUTING jump limits these rules to one source address on one incoming interface. Any host INPUT firewall must also allow that source to reach the local listeners; the example does not change the host's access policy.

This does not immediately replace an already established cloud TCP connection. Allow the UPS to reconnect, or use the router's supported method to disconnect **only that client's data session**. Do not reset the UPS or its power outputs to test routing. Existing DNS cache/connection state may delay the switch.

`iptables` commands alone are not persistent across reboot or firewall reload. Integrate equivalent, source-restricted rules into your router's existing firewall manager or maintained startup script. Do not create a competing rules-restoration service. Routers managed by nftables, OpenWrt or another firewall system need equivalent rules in that system rather than blindly mixing examples.

## Check the result

On the router:

```bash
sudo iptables -t nat -vnL BLUETTI_LOCAL
sudo ss -lntup '( sport = :18760 or sport = :25354 )'
```

In Home Assistant:

1. Wait for at least two complete reports, normally around one minute.
2. Check battery percentage, grid voltage, power readings and the last-valid-sample timestamp.
3. Confirm `transport: wifi_local`, `validated_crc: true`, increasing `valid_samples` and a recent `last_successful_poll`.
4. Reload only the local integration and check that new reports resume. Missing values during reconnection must remain unavailable rather than become zero.
5. Inspect the integration's redacted diagnostics if the connection fails. Check both gateway and broker DNS names, not only the historical generic name.

## Remove only these example rules

Use the **same variable values** as during installation:

```bash
sudo iptables -t nat -D PREROUTING -i "$BLUETTI_LAN_IF" -s "${BLUETTI_UPS_IP}/32" -j BLUETTI_LOCAL
sudo iptables -t nat -F BLUETTI_LOCAL
sudo iptables -t nat -X BLUETTI_LOCAL
```

Only the newly created, dedicated example chain is flushed. Do not flush PREROUTING, INPUT, FORWARD or the NAT table. Remove the equivalent persistent rules from your normal firewall configuration as well, then unload/reload the integration so its current local socket closes. The UPS can resume its original route; no cached private receiver address is left by the local DNS service.

The receiver exposes no AC/DC or power-control commands. Host shutdown automation and UPS output control are outside this repository.

## Direct DNS instead of router redirects

See the Direct DNS instructions in both READMEs. Existing resolvers can rewrite the four names to the HA host. Alternatively, built-in DNS binds UDP/TCP 53 and is assigned only to the UPS via a dedicated DHCP setting. Never use that restricted listener as the whole LAN resolver. Direct mode uses fixed TCP port 18760 and requires neither DNAT nor NET_ADMIN. DNS rewrites alone do not block unrelated Internet traffic.

`sudo unshare --net python3 tools/test-linux-offline.py` tests a synthetic UPS, a separate HA host, another LAN client and an isolated WAN. It checks actual direct DNS/MQTT, reconnection, unrelated-client access and ordinary IPv4/IPv6 UDP/TCP traffic remaining available. No physical UPS, production LAN or Internet is accessed.
