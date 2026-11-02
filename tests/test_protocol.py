import unittest
from unittest.mock import patch
from custom_components.bluetti_local.protocol import Protocol,Samples,decode_report,crc16,remaining_length


def report(cycle=1,start=100,values=None):
    if values is None:
        values={102:99,140:26,142:73,144:0,146:73}
    count=max(values)-start+1
    data=b''.join(values.get(i,0).to_bytes(2,'big') for i in range(start,start+count))
    modbus=b'\x01\x10'+start.to_bytes(2,'big')+count.to_bytes(2,'big')+bytes([len(data)])+data
    return b'\x01\xf8'+cycle.to_bytes(2,'big')+b'\x0c\x01\x14\xce\0\0'+modbus+crc16(modbus).to_bytes(2,'little')


def packet(kind,body):return bytes([kind])+remaining_length(len(body))+body


def connect_packet(client_id=b'IOT-test'):
    # Valid MQTT 3.1.1 CONNECT with username and password, like the captured V2.
    fields=(client_id,b'IOT-test',b'test-password-only')
    return packet(0x10,b'\x00\x04MQTT\x04\xc2\x00\x32'+b''.join(len(v).to_bytes(2,'big')+v for v in fields))

def prelude(kind,body):return kind.to_bytes(2,'big')+len(body).to_bytes(2,'big')+body

def publish(payload):
    topic=b'PUB/EL30V2/123456789'
    return packet(0x30,len(topic).to_bytes(2,'big')+topic+payload)


def identify(protocol):
    protocol.feed(prelude(1,b'IOT-test'))
    protocol.feed(connect_packet())
    protocol.feed(prelude(2,b'EL30V2&123456789'))


class DecoderTests(unittest.TestCase):
    def test_reference_modbus_crc_vector(self):
        self.assertEqual(crc16(bytes.fromhex('01030000000a')),0xcdc5)
    def test_decodes_only_whitelisted_measurements(self):
        cycle,values=decode_report(report())
        self.assertEqual(cycle,1)
        self.assertEqual(values,{'battery_pct':99,'dc_output_w':26,'ac_output_w':73,'dc_input_w':0,'grid_input_w':73})
    def test_voltage_scale(self):
        cycle,values=decode_report(report(start=1300,values={1314:2354}))
        self.assertEqual(values,{'grid_voltage_v':235.4})
    def test_rejects_corrupted_crc(self):
        p=bytearray(report());p[20]^=1
        with self.assertRaises(ValueError):decode_report(p)
    def test_rejects_bad_length_even_with_plausible_data(self):
        with self.assertRaises(ValueError):decode_report(report()[:-1])
    def test_rejects_unrecognized_report_type(self):
        p=bytearray(report());p[1]=0
        with self.assertRaises(ValueError):decode_report(p)
    def test_rejects_impossible_battery(self):
        with self.assertRaises(ValueError):decode_report(report(values={102:65535}))
    def test_rejects_impossible_voltage(self):
        with self.assertRaises(ValueError):decode_report(report(start=1300,values={1314:65535}))
    def test_incomplete_cycle_is_not_a_sample(self):
        s=Samples();self.assertIsNone(s.receive(report(),now=10));self.assertIsNone(s.current(now=10))
    def test_complete_cycle_expires_after_90_seconds(self):
        s=Samples();s.receive(report(),now=10)
        sample=s.receive(report(start=1300,values={1314:2354}),now=12)
        self.assertEqual(sample['values']['battery_pct'],99)
        self.assertIsNotNone(s.current(now=100))
        self.assertIsNone(s.current(now=103))
    def test_different_cycles_are_never_combined(self):
        s=Samples();s.receive(report(cycle=1),now=10)
        self.assertIsNone(s.receive(report(cycle=2,start=1300,values={1314:2354}),now=12))
    def test_same_cycle_after_a_long_gap_is_not_combined(self):
        s=Samples();s.receive(report(),now=10)
        self.assertIsNone(s.receive(report(start=1300,values={1314:2354}),now=16))
    def test_old_cycle_cannot_refresh_the_timestamp(self):
        s=Samples();s.receive(report(cycle=2),now=10)
        s.receive(report(cycle=2,start=1300,values={1314:2354}),now=11)
        s.receive(report(cycle=1),now=50)
        self.assertIsNone(s.receive(report(cycle=1,start=1300,values={1314:2354}),now=51))
        self.assertEqual(s.last_time,11)
    def test_cycle_wrap_is_accepted(self):
        s=Samples();s.receive(report(cycle=65535),now=10)
        s.receive(report(cycle=65535,start=1300,values={1314:2354}),now=11)
        s.receive(report(cycle=0),now=40)
        self.assertIsNotNone(s.receive(report(cycle=0,start=1300,values={1314:2354}),now=41))


class TransportTests(unittest.TestCase):
    def test_default_bootstrap_uses_observed_vendor_clock_offset(self):
        with patch('custom_components.bluetti_local.protocol.time.time',return_value=1793659362):
            p=Protocol(lambda s:None)
            self.assertEqual(p.feed(prelude(1,b'IoT0000000000000')),
                             [b'\x00\x01\x00\x04'+(1793630562).to_bytes(4,'big')])

    def test_prelude_and_mqtt_acknowledgements(self):
        p=Protocol(lambda s:None,epoch=lambda:1,expected_iot='IOT-test',expected_serial='123456789')
        self.assertEqual(p.feed(prelude(1,b'IOT-test')),[bytes.fromhex('0001000400000001')])
        self.assertEqual(p.feed(connect_packet()),[bytes.fromhex('20020000')])
        self.assertEqual(p.feed(prelude(2,b'EL30V2&123456789')),[bytes.fromhex('0002000101')])
        sub=b'\x00\x01\x00\x10PUBLIC/IOT/POWER\x00'
        # Use the correct UTF-8 string length, then a second subscription.
        topic=b'PUBLIC/IOT/POWER';sub=b'\x00\x01'+len(topic).to_bytes(2,'big')+topic+b'\x00'
        self.assertEqual(p.feed(packet(0x82,sub)),[bytes.fromhex('9003000100')])
        self.assertEqual(p.feed(b'\xc0\x00'),[b'\xd0\x00'])
    def test_rejects_wrong_device_before_mqtt(self):
        p=Protocol(lambda s:None,expected_iot='expected')
        with self.assertRaises(ValueError):p.feed(prelude(1,b'other'))
    def test_rejects_report_before_identification(self):
        p=Protocol(lambda s:None)
        with self.assertRaises(ValueError):p.feed(publish(report()))
    def test_tcp_fragmentation_and_coalescence(self):
        samples=[];p=Protocol(samples.append)
        data=prelude(1,b'IOT-test')+connect_packet()+prelude(2,b'EL30V2&123456789')
        for b in data:p.feed(bytes([b]))
        self.assertEqual(p.feed(publish(report())+publish(report(start=1300,values={1314:2354}))),[])
        self.assertEqual(len(samples),1)
    def test_corrupt_telemetry_never_reaches_callback(self):
        samples=[];p=Protocol(samples.append);identify(p)
        damaged=bytearray(report());damaged[-1]^=1
        p.feed(publish(damaged));p.feed(publish(report(start=1300,values={1314:2354})))
        self.assertFalse(samples)
    def test_rejects_huge_mqtt_packet(self):
        p=Protocol(lambda s:None)
        with self.assertRaises(ValueError):p.feed(b'\x30'+remaining_length(65536))
    def test_only_transport_acknowledgements_are_emitted(self):
        samples=[];p=Protocol(samples.append);identify(p)
        replies=p.feed(publish(report())+publish(report(start=1300,values={1314:2354}))+b'\xc0\x00')
        self.assertEqual(replies,[b'\xd0\x00'])
        self.assertEqual(len(samples),1)

    def test_actual_v2_direct_mqtt_without_vendor_prelude(self):
        samples=[];p=Protocol(samples.append,expected_iot='IOT-test',expected_serial='123456789')
        self.assertEqual(p.feed(connect_packet()),[b'\x20\x02\x00\x00'])
        for topic in (b'PUBLIC/IOT/POWER/V2',b'SUB/EL30V2/123456789'):
            subscription=b'\x00\x01'+len(topic).to_bytes(2,'big')+topic+b'\x00'
            self.assertEqual(p.feed(packet(0x82,subscription)),[bytes.fromhex('9003000100')])
        p.feed(publish(report())+publish(report(start=1300,values={1314:2354})))
        self.assertEqual(len(samples),1)
        self.assertFalse(p.iot_seen)
        self.assertTrue(p.model_seen)

    def test_direct_mqtt_cannot_report_for_another_device(self):
        p=Protocol(lambda s:None,expected_serial='different-device')
        p.feed(connect_packet())
        with self.assertRaises(ValueError):p.feed(publish(report()))

    def test_direct_mqtt_rejects_wrong_module_identity(self):
        p=Protocol(lambda s:None,expected_iot='correct-module')
        with self.assertRaises(ValueError):p.feed(connect_packet())

    def test_truncated_connect_is_rejected(self):
        p=Protocol(lambda s:None)
        with self.assertRaises(ValueError):p.feed(packet(0x10,b'\x00\x04MQTT\x04\xc2\x00\x3c'))


if __name__=='__main__':unittest.main(verbosity=2)
