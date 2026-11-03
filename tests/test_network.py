"""Ownership, rollback and setup consent; never accesses the real host firewall."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.bluetti_local import netfilter as nf
from custom_components.bluetti_local import network as routing

CONFIG = {'interface':'br0','device_ip':'192.168.1.25','listen_host':'192.168.1.1',
          'listen_port':18760,'dns_port':25354}


class FakeNetlink:
    def __init__(self):self.table=None;self.chains=[];self.rules=[];self.operations=[]
    def close(self):pass
    def dump(self,op,data=b''):
        return ([self.table] if self.table else []) if op==1 else self.chains if op==4 else self.rules
    def batch(self,ops):
        self.operations.extend(ops)
        for op,flags,data in ops:
            if op==0:self.table=nf.attrs(data)
            elif op==3:self.chains.append(nf.attrs(data))
            elif op==6:self.rules.append(nf.attrs(data))
            elif op==2:self.table=None;self.chains=[];self.rules=[]


class RouterRuleTests(unittest.TestCase):
    def setUp(self):
        self.kernel=FakeNetlink()
        self.rules=nf.RouterRules('synthetic-test-ups',CONFIG,lambda:self.kernel)
        self.plan=patch.object(self.rules,'plan',return_value={});self.plan.start()
    def tearDown(self):self.plan.stop()
    def test_apply_is_idempotent_and_has_exactly_three_rules(self):
        self.assertTrue(self.rules.apply()['changed'])
        count=len(self.kernel.operations)
        self.assertFalse(self.rules.apply()['changed'])
        self.assertEqual(len(self.kernel.operations),count)
        self.assertEqual(len(self.kernel.rules),3)
        self.assertTrue(all(rule[1]==self.rules.table.encode()+b'\0' for rule in self.kernel.rules))
    def test_remove_touches_only_the_dedicated_table(self):
        self.rules.apply();self.assertTrue(self.rules.remove());self.assertFalse(self.rules.remove())
        self.assertEqual(nf.attrs(self.kernel.operations[-1][2])[1],self.rules.table.encode()+b'\0')
    def test_table_name_collision_never_adopts_or_removes_foreign_rules(self):
        self.kernel.table={1:self.rules.table.encode()+b'\0',6:b'foreign-owner'}
        with self.assertRaises(nf.NetworkError):self.rules.apply()
        with self.assertRaises(nf.NetworkError):self.rules.remove()
        self.assertFalse(self.kernel.operations)
    def test_extra_rules_or_chains_are_preserved(self):
        self.rules.apply();self.kernel.rules.append({1:self.rules.table.encode()+b'\0',7:b'foreign'})
        count=len(self.kernel.operations)
        with self.assertRaises(nf.NetworkError):self.rules.remove()
        self.assertEqual(len(self.kernel.operations),count)
    def test_modified_rule_with_same_marker_is_not_removed(self):
        self.rules.apply()
        self.kernel.rules[0][4]=nf.attrs(nf.nat_rule(self.rules.table,'br0','192.168.1.26','192.168.1.1',6,18760,18760,0))[4]
        with self.assertRaises(nf.NetworkError):self.rules.remove()
    def test_modified_chain_priority_is_not_removed(self):
        self.rules.apply();self.kernel.chains[0][4]=nf.u32(1,0)+nf.u32(2,-109)
        with self.assertRaises(nf.NetworkError):self.rules.remove()
    def test_failed_post_apply_validation_rolls_back_only_new_table(self):
        with patch.object(self.rules,'_state',side_effect=[False,nf.NetworkError('validation_failed')]):
            with self.assertRaises(nf.NetworkError):self.rules.apply()
        self.assertIsNone(self.kernel.table)
    def test_addresses_ports_and_interface_cannot_widen_scope(self):
        for key,value in [('interface','br0;rm -rf /'),('device_ip','0.0.0.0'),('device_ip','127.0.0.1'),('device_ip','8.8.8.8'),('listen_host','192.168.1.25'),('listen_port',22),('dns_port',True)]:
            with self.subTest(key=key,value=value),self.assertRaises((ValueError,nf.NetworkError)):
                nf.RouterRules('test',{**CONFIG,key:value})
    def test_packet_attributes_reject_truncation(self):
        for bad in [b'\x03\x00\x01\x00',b'\x10\x00\x01\x00x',b'abc']:
            with self.assertRaises(nf.NetworkError):nf.attrs(bad)
    def test_missing_capability_returns_clear_reason(self):
        with patch.object(nf.sys,'platform','linux'),patch.object(nf.Path,'read_text',return_value='CapEff:\t0000000000000000\n'):
            self.assertEqual(nf.capabilities(),{'supported':False,'reason':'net_admin_required'})


class RouterLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.removed=0
        self.changed=True
        self.real_sleep=asyncio.sleep
        async def executor(fn):return fn()
        self.hass=SimpleNamespace(async_add_executor_job=lambda fn:asyncio.create_task(executor(fn)),
            async_create_background_task=lambda coro,name:asyncio.create_task(coro))
        self.device=SimpleNamespace(online=False,valid_samples=0)
        self.manager=routing.RouterManager(self.hass,'test',CONFIG,self.device)
        self.manager.rules=SimpleNamespace(apply=lambda:{'changed':self.changed},remove=self.remove,reconnect_data=lambda:0)
        self.constants=patch.multiple(routing,VERIFY_SECONDS=.03,RECHECK_SECONDS=.01);self.constants.start()
        async def fast_sleep(delay):await self.real_sleep(.005)
        self.sleep=patch.object(routing.asyncio,'sleep',side_effect=fast_sleep);self.sleep.start()
    def remove(self):self.removed+=1;return True
    async def asyncTearDown(self):
        await self.manager.stop();self.sleep.stop();self.constants.stop()
    async def test_no_fresh_reports_rolls_back_new_rules(self):
        await self.manager.start();await self.manager.task
        self.assertEqual(self.manager.phase,'rolled_back');self.assertEqual(self.removed,1)
    async def test_old_reports_do_not_confirm_new_rules(self):
        self.device.online=True;self.device.valid_samples=10
        await self.manager.start();await self.manager.task
        self.assertEqual(self.manager.phase,'rolled_back')
    async def test_two_new_reports_confirm_and_stop_removes_runtime_rules(self):
        await self.manager.start();self.changed=False
        self.device.online=True;self.device.valid_samples=2
        await self.real_sleep(.01)
        self.assertEqual(self.manager.phase,'verified')
        await self.manager.stop();self.assertEqual(self.removed,1)
    async def test_cancel_pending_verification_rolls_back(self):
        await self.manager.start();await self.manager.stop()
        self.assertEqual(self.removed,1)
    async def test_direct_task_cancellation_rolls_back_unverified_rules(self):
        await self.manager.start();await self.real_sleep(.001)
        self.manager.task.cancel()
        with self.assertRaises(asyncio.CancelledError):await self.manager.task
        self.assertEqual(self.removed,1)
        self.assertFalse(self.manager._new_rules)
    async def test_direct_task_cancellation_removes_confirmed_rules(self):
        await self.manager.start();self.changed=False
        self.device.online=True;self.device.valid_samples=2
        await self.real_sleep(.01);self.assertEqual(self.manager.phase,'verified')
        self.manager.task.cancel()
        with self.assertRaises(asyncio.CancelledError):await self.manager.task
        self.assertEqual(self.removed,1)
    async def test_existing_rules_are_not_removed_when_no_change_was_made(self):
        self.changed=False;await self.manager.start();await self.manager.task
        self.assertEqual(self.manager.phase,'unverified');self.assertEqual(self.removed,0)
    async def test_explicit_restore_removes_only_owned_rules(self):
        self.changed=False;await self.manager.start();await self.manager.stop(remove=True)
        self.assertEqual(self.removed,1)
