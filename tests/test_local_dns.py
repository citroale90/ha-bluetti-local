import asyncio
import contextlib
import socket
import struct
import unittest

from custom_components.bluetti_local.local_dns import answer, LocalDNS, BROKER_ROUTING_ADDRESS, BROKER_NAMES


def query(name='iot.bluettipower.com',qtype=1):
    return struct.pack('>6H',42,0x100,1,0,0,0)+b''.join(bytes([len(p)])+p.encode() for p in name.split('.'))+b'\0'+struct.pack('>HH',qtype,1)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


class DNSTests(unittest.TestCase):
    def test_el30v2_bootstrap_and_european_broker_aliases_resolve_offline(self):
        for name in BROKER_NAMES:
            with self.subTest(name=name):
                r=answer(query(name),'192.168.1.10')
                self.assertEqual(r[3]&15,0)
                self.assertEqual(r[-4:],socket.inet_aton('192.168.1.10'))

    def test_broker_resolves_locally_without_any_upstream(self):
        r=answer(query(),'192.168.1.10')
        self.assertEqual(struct.unpack_from('>H',r)[0],42)
        self.assertEqual(struct.unpack_from('>H',r,6)[0],1)
        self.assertEqual(r[-4:],socket.inet_aton('192.168.1.10'))

    def test_ipv6_has_no_cloud_address(self):
        r=answer(query(qtype=28),'192.168.1.10')
        self.assertEqual(struct.unpack_from('>H',r,6)[0],0)
        self.assertEqual(r[3]&15,0)

    def test_arbitrary_domains_are_refused_without_recursion(self):
        r=answer(query('example.org'),'192.168.1.10')
        self.assertEqual(r[3]&15,5)

    def test_compression_loop_truncation_and_multiquestion_packets_are_dropped(self):
        loop=struct.pack('>6H',42,0x100,1,0,0,0)+b'\xc0\x0c\0\x01\0\x01'
        two=bytearray(query());two[5]=2
        for packet in (loop,query()[:-2],bytes(two),b'',b'\0'*5000):
            self.assertIsNone(answer(packet,'192.168.1.10'))

    def test_case_and_edns_do_not_require_cloud_resolution(self):
        data=bytearray(query('IOT.BLUETTIPOWER.COM'));data[11]=1
        data+=b'\0\0\x29\x10\0\0\0\0\0\0\0'
        self.assertEqual(answer(bytes(data),'192.168.1.10')[-4:],socket.inet_aton('192.168.1.10'))


class DNSSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port=free_port();self.server=LocalDNS('127.0.0.1',self.port,'127.0.0.1')
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()

    async def test_tcp_dns_works_with_only_loopback_connectivity(self):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        data=query();writer.write(struct.pack('>H',len(data))+data);await writer.drain()
        size=struct.unpack('>H',await reader.readexactly(2))[0]
        response=await reader.readexactly(size)
        self.assertEqual(response[-4:],socket.inet_aton(BROKER_ROUTING_ADDRESS))
        writer.close();await writer.wait_closed()

    async def test_udp_dns_works_with_only_loopback_connectivity(self):
        loop=asyncio.get_running_loop()
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.setblocking(False);await loop.sock_sendto(s,query(),('127.0.0.1',self.port))
            response,_=await asyncio.wait_for(loop.sock_recvfrom(s,512),1)
        self.assertEqual(response[-4:],socket.inet_aton(BROKER_ROUTING_ADDRESS))

    async def test_other_source_cannot_use_the_dns_listener(self):
        self.server.device_ip='127.0.0.2'
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        self.assertEqual(await asyncio.wait_for(reader.read(1),1),b'')

    async def test_direct_dns_answers_receiver_address_without_nat(self):
        self.server.answer_address='127.0.0.1'
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        data=query('gwpry.bluettipower.com');writer.write(struct.pack('>H',len(data))+data);await writer.drain()
        size=struct.unpack('>H',await reader.readexactly(2))[0]
        response=await reader.readexactly(size)
        self.assertEqual(response[-4:],socket.inet_aton('127.0.0.1'))
        writer.close();await writer.wait_closed()

    async def test_stop_releases_both_protocols(self):
        await self.server.stop()
        for kind in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
            with socket.socket(socket.AF_INET,kind) as s:s.bind(('127.0.0.1',self.port))

    async def test_stop_with_open_tcp_client_does_not_wait_for_read_timeout(self):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        try:
            await asyncio.sleep(0)
            await asyncio.wait_for(self.server.stop(),.5)
            self.assertEqual(await asyncio.wait_for(reader.read(1),1),b'')
            for kind in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
                with socket.socket(socket.AF_INET,kind) as s:
                    if kind==socket.SOCK_STREAM:s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    s.bind(('127.0.0.1',self.port))
        finally:
            writer.close();await writer.wait_closed()
