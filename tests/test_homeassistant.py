"""Real Home Assistant config entry and TCP lifecycle tests, in an isolated container."""
import asyncio
import contextlib
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
        self.assertEqual(len([s for s in states if s.entity_id.startswith('binary_sensor.test_ups_')]),3)
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

    async def test_homeassistant_stop_cleans_pending_router_setup(self):
        from homeassistant.const import EVENT_HOMEASSISTANT_STOP
        manager=SimpleNamespace(start=AsyncMock(),stop=AsyncMock())
        with patch('custom_components.bluetti_local.config_flow.capabilities',return_value={'supported':True,'reason':None}), \
             patch('custom_components.bluetti_local.config_flow.socket.if_nameindex',return_value=[(1,'lo'),(2,'eth0')]), \
             patch('custom_components.bluetti_local.config_flow.RouterRules'), \
             patch('custom_components.bluetti_local.RouterManager',return_value=manager):
            flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data={**self.config,'network_mode':'automatic'})
            await self.hass.config_entries.flow.async_configure(flow['flow_id'],{'interface':'eth0','confirm':True})
            await self.hass.async_block_till_done()
            self.hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
            await self.hass.async_block_till_done()
            manager.stop.assert_awaited_once()

    async def test_direct_dns_with_external_resolver_needs_no_router_privileges(self):
        self.config.update(network_mode='direct_dns',listen_port=18760)
        with patch('custom_components.bluetti_local.config_flow.capabilities') as caps,patch('custom_components.bluetti_local.config_flow.RouterRules') as rules:
            flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data=self.config)
            self.assertEqual(flow['step_id'],'direct_dns')
            bad=await self.hass.config_entries.flow.async_configure(flow['flow_id'],{'dns_source':'external','confirm_dns':False})
            self.assertEqual(bad['errors']['base'],'direct_dns_confirmation_required')
            done=await self.hass.config_entries.flow.async_configure(flow['flow_id'],{'dns_source':'external','confirm_dns':True})
            self.assertEqual(done['type'],'create_entry')
            await self.hass.async_block_till_done()
            self.entry=self.hass.config_entries.async_entries(DOMAIN)[0]
            self.assertIsNone(self.entry.runtime_data.router)
            self.assertIsNone(self.entry.runtime_data.receiver.dns)
            self.assertEqual(self.entry.runtime_data.receiver.diagnostics['dns_answer_mode'],'receiver_address')
            caps.assert_not_called();rules.assert_not_called()
        _,writer=await self.connect();await self.send_sample(writer)
        self.assertTrue(self.entry.runtime_data.device.online)

    async def test_direct_dns_rejects_nonstandard_mqtt_port(self):
        flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data={**self.config,'network_mode':'direct_dns','listen_port':18888})
        self.assertEqual(flow['type'],'form');self.assertEqual(flow['errors']['base'],'invalid_config')

    async def test_direct_dns_builtin_selects_standard_dns_port(self):
        self.config.update(network_mode='direct_dns',listen_port=18760)
        with patch('custom_components.bluetti_local.LocalReceiver') as receiver:
            receiver.return_value.start=AsyncMock();receiver.return_value.stop=AsyncMock()
            flow=await self.hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'},data=self.config)
            done=await self.hass.config_entries.flow.async_configure(flow['flow_id'],{'dns_source':'builtin','confirm_dns':True})
            self.assertEqual(done['type'],'create_entry')
            await self.hass.async_block_till_done()
            self.assertEqual(receiver.call_args.args[5],53)
            self.assertTrue(receiver.call_args.kwargs['direct_dns']);self.assertFalse(receiver.call_args.kwargs['external_dns'])
            # Keep the patch active during unload, so fake diagnostic fields do not leak.
            entry=self.hass.config_entries.async_entries(DOMAIN)[0]
            await self.hass.config_entries.async_unload(entry.entry_id)

    async def test_reload_with_live_mqtt_session_closes_client_before_waiting(self):
        await self.setup_entry();reader,writer=await self.connect();await self.send_sample(writer)
        self.assertTrue(await asyncio.wait_for(self.hass.config_entries.async_reload(self.entry.entry_id),2))
        self.assertEqual(await asyncio.wait_for(reader.read(1),1),b'')
        _,new_writer=await self.connect();await self.send_sample(new_writer,2)
        self.assertTrue(self.entry.runtime_data.device.online)

    async def test_output_flags_are_read_only_and_reach_homeassistant_at_zero_load(self):
        await self.setup_entry();_,writer=await self.connect()
        writer.write(publish(report(values={102:99,140:0,142:0,144:0,146:0}))+publish(report(start=1300,values={1314:2381})))
        await writer.drain();await asyncio.sleep(.05);await self.hass.async_block_till_done()
        ac='binary_sensor.test_ups_uscita_ac_abilitata'
        dc='binary_sensor.test_ups_uscita_dc_abilitata'
        self.assertEqual(self.hass.states.get(ac).state,'unavailable')
        writer.write(publish(report(start=2000,values={2011:1,2012:0})))
        await writer.drain();await asyncio.sleep(.05);await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get(ac).state,'on')
        self.assertEqual(self.hass.states.get(dc).state,'off')
        self.assertTrue(self.hass.states.get(ac).attributes['read_only'])
        numeric_stamp=self.entry.runtime_data.device.last_successful_poll
        self.assertEqual(self.entry.runtime_data.device.valid_samples,1)
        writer.write(publish(report(cycle=2,start=2000,values={2011:0,2012:1})))
        await writer.drain();await asyncio.sleep(.05);await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get(ac).state,'off')
        self.assertEqual(self.hass.states.get(dc).state,'on')
        self.assertEqual(self.entry.runtime_data.device.last_successful_poll,numeric_stamp)
        self.assertFalse([s for s in self.hass.states.async_all() if s.entity_id.startswith(('switch.','button.','select.'))])

    async def test_only_output_reports_cannot_keep_numeric_data_session_alive(self):
        await self.setup_entry()
        with patch('custom_components.bluetti_local.server.SAMPLE_TIMEOUT_SECONDS',.4):
            reader,writer=await self.connect();await self.send_sample(writer)
            for cycle in range(1,4):
                writer.write(publish(report(cycle=cycle,start=2000,values={2011:1,2012:1})))
                await writer.drain();await asyncio.sleep(.03)
            self.assertEqual(await asyncio.wait_for(reader.read(1),1),b'')
        self.assertEqual(self.entry.runtime_data.device.valid_samples,1)
        self.assertFalse(self.entry.runtime_data.device.outputs_online)

    async def send_profile(self, writer, cycle):
        from test_discovery import full_reports
        for payload in full_reports(cycle):writer.write(publish(payload))
        await writer.drain();await asyncio.sleep(.05);await self.hass.async_block_till_done()

    async def test_continuous_discovery_creates_all_supported_entities_without_reload(self):
        await self.setup_entry();_,writer=await self.connect()
        initial=entity_registry.async_entries_for_config_entry(entity_registry.async_get(self.hass),self.entry.entry_id)
        initial_ids={r.unique_id:r.entity_id for r in initial}
        await self.send_profile(writer,1)
        self.assertEqual(len(entity_registry.async_entries_for_config_entry(entity_registry.async_get(self.hass),self.entry.entry_id)),12)
        await self.send_profile(writer,2)
        rows=entity_registry.async_entries_for_config_entry(entity_registry.async_get(self.hass),self.entry.entry_id)
        self.assertEqual(len(rows),23)
        self.assertTrue(all(r.domain in ('sensor','binary_sensor') for r in rows))
        self.assertTrue(all(initial_ids.get(r.unique_id,r.entity_id)==r.entity_id for r in rows))
        for row in rows:
            state=self.hass.states.get(row.entity_id)
            self.assertNotIn(state.state,('unknown','unavailable'),row.entity_id)
        mode=self.hass.states.get('sensor.test_ups_modalita_di_ricarica')
        self.assertEqual(mode.state,'standard')
        self.assertEqual(mode.attributes['options'],['standard','silent','turbo'])
        await self.send_profile(writer,3)
        self.assertEqual(len(entity_registry.async_entries_for_config_entry(entity_registry.async_get(self.hass),self.entry.entry_id)),23)

    async def test_discovered_entities_restore_on_reload_without_old_values_or_duplicates(self):
        await self.setup_entry();_,writer=await self.connect()
        await self.send_profile(writer,1);await self.send_profile(writer,2)
        registry=entity_registry.async_get(self.hass)
        before={r.unique_id:r.entity_id for r in entity_registry.async_entries_for_config_entry(registry,self.entry.entry_id)}
        old=self.entry.runtime_data.device.discovery
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertFalse(old._callbacks);self.assertFalse(old._discovery_callbacks)
        for key in ['TimeRemaining','EcoAc','ChargingMode','ReportedSerial']:
            self.assertEqual(self.hass.states.get(before['123456789_'+key]).state,'unavailable')
        _,writer=await self.connect();await self.send_profile(writer,1)
        after={r.unique_id:r.entity_id for r in entity_registry.async_entries_for_config_entry(registry,self.entry.entry_id)}
        self.assertEqual(before,after)
        self.assertEqual(self.hass.states.get(before['123456789_TimeRemaining']).state,'0.1002')

    async def test_late_new_field_and_user_disabled_entity(self):
        await self.setup_entry();_,writer=await self.connect()
        await self.send_sample(writer,1);await self.send_sample(writer,2)
        # Late settings group: no reconnect or reload required.
        from test_discovery import full_reports
        for cycle in [3,4]:
            writer.write(publish(full_reports(cycle)[2]));await writer.drain()
        await asyncio.sleep(.05);await self.hass.async_block_till_done()
        registry=entity_registry.async_get(self.hass)
        row=next(r for r in entity_registry.async_entries_for_config_entry(registry,self.entry.entry_id) if r.unique_id=='123456789_EcoAc')
        registry.async_update_entity(row.entity_id,disabled_by=entity_registry.RegistryEntryDisabler.USER)
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(registry.async_get(row.entity_id).disabled_by,entity_registry.RegistryEntryDisabler.USER)
        self.assertIsNone(self.hass.states.get(row.entity_id))

    async def test_optional_entity_freshness_does_not_follow_core(self):
        await self.setup_entry();_,writer=await self.connect()
        await self.send_profile(writer,1);await self.send_profile(writer,2)
        device=self.entry.runtime_data.device
        later=device._sample_time+91
        device._clock=lambda:later
        device.discovery.check_freshness()
        # Feed core only at the later clock, keeping the actual UPS online.
        writer.write(publish(report(cycle=3,start=102,values={102:99}))+publish(report(cycle=3,start=140,values={140:26,142:68,144:0,146:276}))+publish(report(cycle=3,start=1300,values={1314:2383})))
        await writer.drain();await asyncio.sleep(.05);await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get('sensor.test_ups_tempo_rimanente_stimato').state,'unavailable')
        self.assertEqual(self.hass.states.get('sensor.test_ups_batteria').state,'99')
