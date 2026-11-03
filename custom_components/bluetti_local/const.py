"""Constants for the local edition derived from BLUETTI 1.0.5."""

DOMAIN = "bluetti_local"
INTEGRATION_NAME = "BLUETTI Local Wi-Fi"
MODEL = "EL30V2"
DEFAULT_PORT = 18760
DEFAULT_DNS_PORT = 25354
STALE_SECONDS = 90
CONF_DEVICE_IP = "device_ip"
CONF_SERIAL = "serial"
CONF_LISTEN_HOST = "listen_host"
CONF_LISTEN_PORT = "listen_port"
CONF_DNS_PORT = "dns_port"
CONF_NAME = "name"
CONF_NETWORK_MODE = "network_mode"
CONF_INTERFACE = "interface"
NETWORK_MANUAL = "manual"
NETWORK_AUTOMATIC = "automatic"

# Function codes are kept from the official entity model where applicable.
SENSORS = (
    ("SOC", "battery_pct", "Batteria", "SensorDeviceClass.BATTERY"),
    ("ACLoadAllTotalPower", "ac_output_w", "Uscita AC", "SensorDeviceClass.POWER"),
    ("DCLoadAllTotalPower", "dc_output_w", "Uscita DC", "SensorDeviceClass.POWER"),
    ("PVAllTotalPower", "dc_input_w", "Ingresso DC", "SensorDeviceClass.POWER"),
    ("GridAllTotalPower", "grid_input_w", "Ingresso rete", "SensorDeviceClass.POWER"),
    ("GridInputVoltage", "grid_voltage_v", "Tensione rete", "SensorDeviceClass.VOLTAGE"),
    ("TotalOutputPower", "total_output_w", "Uscita totale", "SensorDeviceClass.POWER"),
    ("TotalInputPower", "total_input_w", "Ingresso totale", "SensorDeviceClass.POWER"),
)
