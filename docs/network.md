# Local network setup / Configurazione della rete

[Italian README](../README.md) · [English README](../README.en.md)

## Validated topology

The working topology runs **Home Assistant Container with host networking on the same Linux router that receives the UPS's Wi-Fi traffic**. The receiver binds to that router's LAN IPv4 address. Its source-IP restriction expects the UPS's reserved address to remain visible.

The integration receives unsolicited reports; it does not discover or configure router rules. Assign a DHCP reservation to the UPS before creating rules. Check that TCP 18760 and UDP/TCP 25354 are free on the chosen local address.

An installation on a separate Home Assistant host can have a different return path: a reply sent directly from that host to the UPS may bypass the router's reverse NAT. It may need additional routing/SNAT and must preserve the source address expected by the receiver. The example below is not a complete recipe for that topology. Home Assistant OS router integration is also not covered by this tested example.

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
