import unittest

from custom_components.bluetti_local.models import BluettiDevice
from custom_components.bluetti_local.sensor import BluettiSensor, SENSOR_MAP
from custom_components.bluetti_local.binary_sensor import BluettiBinarySensor
from custom_components.bluetti_local.config_flow import validate_config


def sample(cycle=1):
    return {"report_cycle": cycle, "validated_crc": True,
            "values": {"battery_pct": 99, "ac_output_w": 75, "dc_output_w": 25,
                       "grid_input_w": 75, "dc_input_w": 0, "grid_voltage_v": 238.1}}


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.device=BluettiDevice("123456789", "Test UPS", lambda:self.now)
        self.updates=[]
        self.device.register_callback(lambda:self.updates.append(self.device.online))

    def test_starting_sensors_are_unavailable_instead_of_zero(self):
        self.assertFalse(self.device.online)
        self.assertIsNone(self.device.get_state("SOC").fn_value)

    def test_complete_sample_updates_original_function_codes(self):
        self.device.accept_sample(sample())
        self.assertEqual(self.device.get_state("SOC").fn_value,99)
        self.assertEqual(self.device.get_state("ACLoadAllTotalPower").fn_value,75)
        self.assertEqual(self.device.get_state("TotalOutputPower").fn_value,100)
        self.assertTrue(self.device.online)
        self.assertEqual(self.updates,[True])

    def test_stale_data_becomes_unavailable_without_a_new_packet(self):
        self.device.accept_sample(sample());stamp=self.device.last_successful_poll
        self.now=191;self.device.check_freshness()
        self.assertFalse(self.device.online)
        self.assertEqual(self.updates,[True,False])
        self.assertEqual(self.device.last_successful_poll,stamp)
        self.device.check_freshness();self.assertEqual(len(self.updates),2)

    def test_disconnect_immediately_invalidates_the_latest_sample(self):
        self.device.accept_sample(sample());self.device.disconnect()
        self.assertFalse(self.device.online)
        self.assertEqual(self.updates,[True,False])

    def test_new_connection_needs_a_new_valid_sample(self):
        self.device.accept_sample(sample());self.device.disconnect()
        self.now=110;self.device.accept_sample(sample(2))
        self.assertTrue(self.device.online)
        self.assertEqual(self.device.valid_samples,2)

    def test_partial_or_unchecked_data_cannot_change_device_state(self):
        bad=sample();bad["values"].pop("grid_voltage_v")
        with self.assertRaises(ValueError):self.device.accept_sample(bad)
        bad=sample();bad["validated_crc"]=False
        with self.assertRaises(ValueError):self.device.accept_sample(bad)
        self.assertEqual(self.device.valid_samples,0)

    def test_sensor_classes_use_official_callback_model(self):
        state=self.device.get_state("SOC")
        entity=BluettiSensor(self.device,state,SENSOR_MAP[state.sensor_info["sensorType"]])
        self.assertFalse(entity.available)
        self.device.accept_sample(sample())
        self.assertEqual(entity.native_value,99)
        self.assertTrue(entity.available)
        self.assertTrue(BluettiBinarySensor(self.device).is_on)


class ConfigurationTests(unittest.TestCase):
    def config(self):
        return dict(name="Test UPS",device_ip="192.168.1.50",listen_host="192.168.1.10",listen_port=18760,serial="123456789")

    def test_valid_local_settings_need_no_cloud_account(self):
        self.assertEqual(validate_config(self.config())["serial"],"123456789")

    def test_unsupported_addresses_and_privileged_ports_are_rejected(self):
        for key,value in (("device_ip","8.8.8.8"),("device_ip","::1"),("device_ip","0.0.0.0"),("listen_host","0.0.0.0"),("serial","EL30V2secret"),("listen_port",22)):
            with self.subTest(key=key,value=value):
                data=self.config();data[key]=value
                with self.assertRaises(ValueError):validate_config(data)
