"""Verified model profile and per-field discovery/freshness, without hardware."""
import json
import struct
import unittest
from unittest.mock import Mock

from custom_components.bluetti_local.models import BluettiDevice
from custom_components.bluetti_local.profile import FIELDS, PROFILE, decode_field
from test_protocol import report, Protocol, publish, identify


def full_reports(cycle):
    values = {102: 99, 104: 6, 140: 26, 142: 68, 144: 0, 146: 276}
    model = b"EL30V2".ljust(12, b"\0")
    wire = b"".join(model[i:i+2][::-1] for i in range(0, len(model), 2))
    values.update({110+i: word for i, word in enumerate(struct.unpack(">6H", wire))})
    values.update({116+i: (123456789 >> (16*i)) & 65535 for i in range(4)})
    return [report(cycle=cycle, values=values),
            report(cycle=cycle, start=1300, values={1314: 2383}),
            report(cycle=cycle, start=2000, values={2011:1,2012:1,2014:0,2015:4,2016:5,2017:0,2018:4,2019:10,2020:0,2021:0})]


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.device = BluettiDevice("123456789", "Test UPS", lambda: self.now)
        self.discovery = self.device.discovery
        self.protocol = Protocol(self.device.accept_sample, expected_serial="123456789",
                                 on_output_status=self.device.accept_output_status,
                                 on_report=self.discovery.receive)
        identify(self.protocol)

    def feed(self, cycle):
        for payload in full_reports(cycle):
            self.protocol.feed(publish(payload))

    def test_full_model_discovers_19_readable_fields_after_two_cycles(self):
        self.feed(1)
        self.assertEqual(self.discovery.confirmed, set())
        self.now += 30; self.feed(2)
        self.assertEqual(len(FIELDS), 19)
        self.assertEqual(self.discovery.confirmed, set(PROFILE))
        self.assertTrue(all(self.discovery.available(k) for k in PROFILE))
        self.assertEqual(self.discovery.observations['remaining_hours'].value, .1002)
        self.assertEqual(self.discovery.observations['reported_model'].value, 'EL30V2')
        self.assertEqual(self.discovery.observations['reported_serial'].value, '123456789')
        self.assertEqual(self.discovery.observations['charging_mode'].value, 'standard')

    def test_duplicate_and_old_cycles_do_not_confirm_or_renew(self):
        self.feed(20); self.now += 30; self.feed(20); self.feed(19)
        self.assertFalse(self.discovery.confirmed)
        self.assertEqual(self.discovery.observations['remaining_hours'].seen_at, 100)

    def test_counter_wrap_confirms_new_cycle(self):
        self.feed(65535); self.now += 30; self.feed(0)
        self.assertEqual(len(self.discovery.confirmed), 19)

    def test_crc_length_and_unknown_registers_cannot_create_entities(self):
        for cycle in [1,2]:
            corrupt = bytearray(full_reports(cycle)[0]); corrupt[-1] ^= 1
            self.discovery.receive(bytes(corrupt))
            self.discovery.receive(full_reports(cycle)[0][:-2])
            self.discovery.receive(report(cycle=cycle,start=8000,values={8000:1}))
        self.assertFalse(self.discovery.confirmed)
        self.assertEqual(self.discovery.invalid_reports, 4)

    def test_invalid_enum_flag_and_duration_are_excluded_individually(self):
        for cycle in [1,2]:
            self.discovery.receive(report(cycle=cycle,start=2014,values={2014:2,2015:0,2016:5,2017:1,2018:5,2019:10,2020:4,2021:65535}))
        self.assertEqual(self.discovery.confirmed, {'eco_dc_threshold_w','eco_ac_enabled','eco_ac_threshold_w'})
        self.assertEqual(self.discovery.invalid_fields, 10)

    def test_incomplete_and_mismatched_identity_are_never_inferred(self):
        for cycle in [1,2]:
            self.discovery.receive(report(cycle=cycle,start=110,values={110:0}))
            self.discovery.receive(report(cycle=cycle,start=116,values={116:42,117:0,118:0,119:0}))
        self.assertFalse(self.discovery.confirmed)
        for raw in [b'XX'*6, b'\xff'*12, b'LE03\x002V\0\0\0\0\0\0']:
            with self.assertRaises(ValueError): decode_field(PROFILE['reported_model'],raw,'123456789')

    def test_discovery_never_refreshes_core_or_status_timestamp(self):
        self.feed(1); numeric=self.device.last_successful_poll; output=self.device.last_output_status
        self.now += 30
        self.discovery.receive(full_reports(2)[2])
        self.assertEqual(self.device.valid_samples, 1)
        self.assertEqual(self.device.last_successful_poll,numeric)
        self.assertEqual(self.device.last_output_status,output)

    def test_each_field_expires_even_while_other_reports_are_fresh(self):
        self.feed(1); self.now +=30; self.feed(2)
        self.now = 221
        # New numeric sample contains no remaining time or identities.
        self.protocol.feed(publish(report(cycle=3,start=102,values={102:99})))
        self.protocol.feed(publish(report(cycle=3,start=140,values={140:26,142:68,144:0,146:276})))
        self.protocol.feed(publish(report(cycle=3,start=1300,values={1314:2383})))
        self.assertTrue(self.device.online)
        self.assertFalse(self.discovery.available('remaining_hours'))
        self.assertTrue(self.discovery.available('grid_voltage_v'))

    def test_expired_candidate_requires_two_new_recent_cycles(self):
        self.feed(1);self.now +=91;self.feed(2)
        self.assertFalse(self.discovery.confirmed)
        self.now +=30;self.feed(3)
        self.assertEqual(len(self.discovery.confirmed),19)

    def test_disconnect_invalidates_and_allows_reset_counter(self):
        self.feed(20);self.now +=30;self.feed(21)
        self.device.disconnect()
        self.assertFalse(self.discovery.available('remaining_hours'))
        self.assertEqual(len(self.discovery.confirmed),19)
        # Real reconnect creates a fresh protocol cycle accumulator.
        self.protocol = Protocol(self.device.accept_sample,expected_serial='123456789',on_report=self.discovery.receive)
        identify(self.protocol); self.feed(1)
        self.assertTrue(self.discovery.available('remaining_hours'))

    def test_native_registry_restore_keeps_no_stale_values(self):
        self.discovery.restore(['remaining_hours','unknown_field'])
        self.assertEqual(self.discovery.confirmed,{'remaining_hours'})
        self.assertIsNone(self.discovery.observations['remaining_hours'].value)
        self.assertFalse(self.discovery.available('remaining_hours'))

    def test_diagnostics_include_no_values_or_identity(self):
        self.feed(1);self.now+=30;self.feed(2)
        diagnostics=json.dumps(self.discovery.diagnostics)
        self.assertNotIn('123456789',diagnostics)
        self.assertNotIn('value',diagnostics)

    def test_late_discovery_notifies_once_and_unsubscribe_works(self):
        callback=Mock();unsubscribe=self.discovery.subscribe(callback,discovery=True)
        self.feed(1);self.feed(2)
        # Multiple groups legitimately discover independently, no duplicates.
        first=callback.call_count;self.feed(3)
        self.assertEqual(callback.call_count,first)
        unsubscribe();self.discovery.confirmed.clear();self.feed(4)
        self.assertEqual(callback.call_count,first)

    def test_receiver_emits_only_transport_acknowledgements(self):
        replies=[]
        for cycle in [1,2]:
            for payload in full_reports(cycle):replies+=self.protocol.feed(publish(payload))
        self.assertEqual(replies,[])

    def test_service_envelopes_are_not_reported_as_bad_sensor_data(self):
        self.discovery.receive(b"\x01\xf8" + b"\0" * 8 + b"\x01\x49" + b"\0" * 10)
        self.assertEqual(self.discovery.invalid_reports,0)
        self.assertEqual(self.discovery.unsupported_reports,1)
        self.assertFalse(self.discovery.confirmed)
