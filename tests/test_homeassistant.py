"""Real Home Assistant config entry and TCP lifecycle tests, in an isolated container."""
import asyncio
import contextlib
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, HassJob
from homeassistant.config_entries import ConfigEntries
from homeassistant.setup import async_setup_component
from homeassistant import loader
from homeassistant.helpers import (entity_registry, device_registry, area_registry,
                                  category_registry, floor_registry, issue_registry,
                                  label_registry, translation, restore_state)

from custom_components.bluetti_local.const import DOMAIN
from test_protocol import report,packet,publish,connect_packet


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]


class HomeAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        shutil.copytree('/audit/custom_components',Path(self.temp.name)/'custom_components')
        self.hass=HomeAssistant(self.temp.name)
        loader.async_setup(self.hass)
        if getattr(self.hass,'config_entries',None) is None:
            self.hass.config_entries=ConfigEntries(self.hass,{})
        translation.async_setup(self.hass)
        device_registry.async_setup(self.hass)
        await asyncio.gather(self.hass.config_entries.async_initialize(),
                            *(module.async_load(self.hass) for module in
                              (device_registry,entity_registry,area_registry,category_registry,
                               floor_registry,issue_registry,label_registry,restore_state)))
        self.hass.config.time_zone='Europe/Rome'
        self.hass.config.skip_pip=True
        self.connections=[]
        self.assertTrue(await async_setup_component(self.hass,'homeassistant',{}))
        await self.hass.async_start()
        self.config=dict(name="Test UPS",device_ip="127.0.0.1",listen_host="127.0.0.1",listen_port=free_port(),dns_port=free_port(),serial="123456789")

    async def asyncTearDown(self):
        for writer in self.connections:
            writer.close()
            with contextlib.suppress(OSError):await writer.wait_closed()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            await self.hass.config_entries.async_unload(entry.entry_id)
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def setup_entry(self):
        flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={"source":"user"},data=self.config)
        self.assertEqual(flow['type'],'create_entry',flow)
        await self.hass.async_block_till_done()
        self.entry=self.hass.config_entries.async_entries(DOMAIN)[0]
        self.assertEqual(self.entry.state.value,'loaded')
        return self.entry

    async def connect(self):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.config['listen_port'])
        self.connections.append(writer)
        writer.write(connect_packet());await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(4),2),b'\x20\x02\x00\x00')
        topic=b'SUB/EL30V2/123456789'
        writer.write(packet(0x82,b'\x00\x01'+len(topic).to_bytes(2,'big')+topic+b'\x00'))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(5),2),bytes.fromhex('9003000100'))
        return reader,writer

    async def send_sample(self,writer,cycle=1):
        writer.write(publish(report(cycle=cycle))+publish(report(cycle=cycle,start=1300,values={1314:2381})))
        await writer.drain();await asyncio.sleep(.05);await self.hass.async_block_till_done()

    async def test_setup_creates_only_monitoring_entities(self):
        await self.setup_entry()
        states=self.hass.states.async_all()
        self.assertEqual(len([s for s in states if s.entity_id.startswith('sensor.test_ups_')]),9)
        self.assertEqual(len([s for s in states if s.entity_id.startswith('binary_sensor.test_ups_')]),1)
        self.assertFalse([s for s in states if s.entity_id.startswith(('switch.','button.','select.'))])
        self.assertEqual(self.hass.states.get('sensor.test_ups_batteria').state,'unavailable')

    async def test_actual_tcp_reports_reach_homeassistant_and_disconnect_invalidates(self):
        await self.setup_entry();reader,writer=await self.connect();await self.send_sample(writer)
        state=self.hass.states.get('sensor.test_ups_batteria')
        self.assertEqual(state.state,'99')
        self.assertEqual(state.attributes['transport'],'wifi_local')
        self.assertTrue(state.attributes['validated_crc'])
        self.assertEqual(self.hass.states.get('binary_sensor.test_ups_telemetria_locale').state,'on')
        writer.close();await writer.wait_closed();await asyncio.sleep(.05);await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get('sensor.test_ups_batteria').state,'unavailable')

    async def test_reconnect_after_socket_loss_accepts_fresh_reports(self):
        await self.setup_entry();_,writer=await self.connect();await self.send_sample(writer)
        writer.close();await writer.wait_closed();await asyncio.sleep(.05)
        _,new_writer=await self.connect();await self.send_sample(new_writer,2)
        self.assertTrue(self.entry.runtime_data.device.online)
        self.assertEqual(self.entry.runtime_data.device.valid_samples,2)

    async def test_wrong_peer_cannot_submit_data(self):
        self.config['device_ip']='127.0.0.2';await self.setup_entry()
        reader,writer=await asyncio.open_connection('127.0.0.1',self.config['listen_port']);self.connections.append(writer)
        self.assertEqual(await asyncio.wait_for(reader.read(1),2),b'')
        self.assertFalse(self.entry.runtime_data.device.online)

    async def test_reconnect_replaces_half_open_socket_without_waiting_90_seconds(self):
        await self.setup_entry();_,old=await self.connect();await self.send_sample(old)
        _,new=await self.connect();await self.send_sample(new,2)
        old.close();await asyncio.sleep(.05);await self.hass.async_block_till_done()
        self.assertTrue(self.entry.runtime_data.device.online)
        self.assertEqual(self.entry.runtime_data.device.valid_samples,2)

    async def test_unload_releases_listening_port(self):
        await self.setup_entry()
        self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))
        with socket.socket() as s:s.bind(('127.0.0.1',self.config['listen_port']))

    async def test_duplicate_device_is_rejected(self):
        await self.setup_entry();config={**self.config,'listen_port':free_port()}
        flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data=config)
        self.assertEqual(flow['type'],'abort')
        self.assertEqual(flow['reason'],'already_configured')

    async def test_silent_connection_cannot_keep_old_data_available(self):
        await self.setup_entry();_,writer=await self.connect();await self.send_sample(writer)
        device=self.entry.runtime_data.device
        device._clock=lambda:device._sample_time+91
        self.hass.async_run_hass_job(HassJob(self.entry.runtime_data.receiver._check_freshness),None)
        await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get('sensor.test_ups_batteria').state,'unavailable')
        self.assertEqual(self.hass.states.get('binary_sensor.test_ups_telemetria_locale').state,'off')

    async def test_keepalives_without_fresh_samples_close_only_data_socket(self):
        await self.setup_entry()
        with patch('custom_components.bluetti_local.server.SAMPLE_TIMEOUT_SECONDS',.2):
            reader,writer=await self.connect();await self.send_sample(writer)
            for _ in range(3):
                writer.write(bytes.fromhex('c000'));await writer.drain()
                self.assertEqual(await asyncio.wait_for(reader.readexactly(2),1),bytes.fromhex('d000'))
                await asyncio.sleep(.02)
            self.assertEqual(await asyncio.wait_for(reader.read(1),1),b'')
        self.assertEqual(self.entry.runtime_data.receiver.telemetry_reconnects,1)
        self.assertFalse(self.entry.runtime_data.device.online)
        _,new=await self.connect();await self.send_sample(new,2)
        self.assertTrue(self.entry.runtime_data.device.online)

    async def test_new_complete_reports_renew_data_session_deadline(self):
        await self.setup_entry()
        with patch('custom_components.bluetti_local.server.SAMPLE_TIMEOUT_SECONDS',.3):
            reader,writer=await self.connect()
            for cycle in range(1,4):
                await self.send_sample(writer,cycle)
                await asyncio.sleep(.08)
            writer.write(bytes.fromhex('c000'));await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(2),1),bytes.fromhex('d000'))
        self.assertEqual(self.entry.runtime_data.receiver.telemetry_reconnects,0)
        self.assertTrue(self.entry.runtime_data.device.online)

    async def test_automatic_network_requires_explicit_confirmation(self):
        manager=SimpleNamespace(start=AsyncMock(),stop=AsyncMock())
        with patch('custom_components.bluetti_local.config_flow.capabilities',return_value={'supported':True,'reason':None}), \
             patch('custom_components.bluetti_local.config_flow.socket.if_nameindex',return_value=[(1,'lo'),(2,'eth0')]), \
             patch('custom_components.bluetti_local.config_flow.RouterRules') as rules, \
             patch('custom_components.bluetti_local.RouterManager',return_value=manager):
            flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data={**self.config,'network_mode':'automatic'})
            self.assertEqual(flow['type'],'form');self.assertEqual(flow['step_id'],'network')
            manager.start.assert_not_awaited()
            rejected=await self.hass.config_entries.flow.async_configure(flow['flow_id'],{'interface':'eth0','confirm':False})
            self.assertEqual(rejected['errors']['base'],'network_confirmation_required')
            manager.start.assert_not_awaited()
            accepted=await self.hass.config_entries.flow.async_configure(flow['flow_id'],{'interface':'eth0','confirm':True})
            self.assertEqual(accepted['type'],'create_entry')
            await self.hass.async_block_till_done();manager.start.assert_awaited_once()

    async def test_automatic_network_reports_missing_permissions_without_applying_rules(self):
        with patch('custom_components.bluetti_local.config_flow.capabilities',return_value={'supported':False,'reason':'net_admin_required'}), \
             patch('custom_components.bluetti_local.config_flow.RouterRules') as rules:
            flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data={**self.config,'network_mode':'automatic'})
            self.assertEqual(flow['type'],'form')
            self.assertEqual(flow['errors']['base'],'net_admin_required')
            rules.assert_not_called()
