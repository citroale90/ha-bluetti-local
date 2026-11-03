"""Output flags from registers, independent of power and watchdog freshness."""
import unittest

from custom_components.bluetti_local.protocol import (
    OUTPUT_FIELD_MAP, Protocol, Samples, decode_output_report,
)
from custom_components.bluetti_local.models import BluettiDevice
from custom_components.bluetti_local.binary_sensor import BluettiOutputSensor
from test_protocol import report, publish, identify
from test_models import sample


def status(cycle=1, ac=True, dc=True):
    return {"report_cycle": cycle, "validated_crc": True,
            "values": {"ac_output_enabled": ac, "dc_output_enabled": dc}}


def output_report(cycle=1, ac=1, dc=1):
    return report(cycle=cycle, start=2000, values={2011: ac, 2012: dc})


class OutputDecoderTests(unittest.TestCase):
    def test_explicit_zero_and_one_decode_as_booleans(self):
        for ac, dc in [(0, 0), (0, 1), (1, 0), (1, 1)]:
            with self.subTest(ac=ac, dc=dc):
                cycle, values = decode_output_report(output_report(ac=ac, dc=dc))
                self.assertEqual(cycle, 1)
                self.assertEqual(values, {"ac_output_enabled": bool(ac), "dc_output_enabled": bool(dc)})
                self.assertTrue(all(type(v) is bool for v in values.values()))

    def test_unrecognized_flags_are_not_guessed_as_on_or_off(self):
        for ac, dc in [(2, 1), (1, 65535)]:
            with self.subTest(ac=ac, dc=dc), self.assertRaises(ValueError):
                decode_output_report(output_report(ac=ac, dc=dc))

    def test_status_requires_crc_and_both_flags_from_one_cycle(self):
        samples = Samples(fields=OUTPUT_FIELD_MAP, decoder=decode_output_report)
        damaged = bytearray(output_report()); damaged[-1] ^= 1
        self.assertIsNone(samples.receive(damaged, now=10))
        self.assertIsNone(samples.receive(report(cycle=1, start=2011, values={2011: 1}), now=10))
        self.assertIsNone(samples.receive(report(cycle=2, start=2012, values={2012: 0}), now=11))
        result = samples.receive(report(cycle=2, start=2011, values={2011: 1}), now=12)
        self.assertEqual(result['values'], {"ac_output_enabled": True, "dc_output_enabled": False})

    def test_replayed_or_duplicate_status_cannot_renew_timestamp(self):
        samples = Samples(fields=OUTPUT_FIELD_MAP, decoder=decode_output_report)
        self.assertIsNotNone(samples.receive(output_report(cycle=2), now=10))
        self.assertIsNone(samples.receive(output_report(cycle=2), now=20))
        self.assertIsNone(samples.receive(output_report(cycle=1), now=30))
        self.assertEqual(samples.last_time, 10)

    def test_output_reports_keep_numeric_samples_and_wire_replies_unchanged(self):
        numeric, outputs = [], []
        protocol = Protocol(numeric.append, on_output_status=outputs.append)
        identify(protocol)
        self.assertEqual(protocol.feed(publish(output_report())), [])
        self.assertEqual(len(outputs), 1); self.assertFalse(numeric)
        self.assertEqual(protocol.feed(publish(report()) + publish(report(start=1300, values={1314: 2381}))), [])
        self.assertEqual(len(numeric), 1)
        self.assertEqual(protocol.feed(publish(output_report())), [])
        self.assertEqual(len(outputs), 1)


class OutputModelTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.device = BluettiDevice('123456789', 'Test UPS', lambda: self.now)
        self.ac = BluettiOutputSensor(self.device, 'SetCtrlAc', 'ac_output_enabled', 'mdi:power-socket-eu')

    def test_missing_status_is_unavailable_even_with_live_power(self):
        self.device.accept_sample(sample())
        self.assertTrue(self.device.online)
        self.assertFalse(self.ac.available); self.assertIsNone(self.ac.is_on)

    def test_enabled_output_remains_on_with_zero_watts(self):
        data = sample(); data['values']['ac_output_w'] = 0
        self.device.accept_sample(data); self.device.accept_output_status(status(ac=True, dc=False))
        self.assertTrue(self.ac.available); self.assertIs(self.ac.is_on, True)
        self.assertEqual(self.device.get_state('ACLoadAllTotalPower').fn_value, 0)
        self.device.accept_output_status(status(2, ac=False))
        self.assertIs(self.ac.is_on, False)

    def test_status_does_not_renew_numeric_or_watchdog_freshness(self):
        self.device.accept_sample(sample()); original = self.device.last_successful_poll
        self.now = 191; self.device.accept_output_status(status())
        self.assertFalse(self.device.online); self.assertFalse(self.ac.available)
        self.assertEqual(self.device.valid_samples, 1)
        self.assertEqual(self.device.last_successful_poll, original)

    def test_output_status_expires_while_numeric_reports_continue(self):
        self.device.accept_sample(sample()); self.device.accept_output_status(status())
        observed = []; self.device.register_callback(lambda: observed.append(self.ac.available))
        self.now = 190; self.device.accept_sample(sample(2))
        self.assertTrue(self.ac.available)
        self.now = 191; self.device.check_freshness()
        self.assertFalse(self.ac.available); self.assertTrue(self.device.online)
        self.assertEqual(observed, [True, False])

    def test_reconnect_requires_new_status_before_showing_old_enable_state(self):
        self.device.accept_sample(sample()); self.device.accept_output_status(status())
        self.device.disconnect(); self.now = 110; self.device.accept_sample(sample(2))
        self.assertTrue(self.device.online); self.assertFalse(self.ac.available)
        self.device.accept_output_status(status(2))
        self.assertTrue(self.ac.available)

    def test_unvalidated_nonboolean_or_partial_status_cannot_change_model(self):
        cases = [status(ac=1), status(dc=None), {**status(), 'validated_crc': False},
                 {**status(), 'values': {'ac_output_enabled': True}}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.device.accept_output_status(data)
        self.assertEqual(self.device.valid_output_samples, 0)
        self.assertEqual(self.device.valid_samples, 0)
