#!/usr/bin/env python3
"""Real DNS/data DNAT test. Run ONLY with: sudo unshare --net python3 this-file.

Refuses the initial network namespace. Creates a second isolated namespace and a
veth pair, exchanges synthetic bytes, and checks unrelated rules survive. No UPS,
LAN, Internet, daemon or production Home Assistant configuration is involved.
"""
from pathlib import Path
import ctypes
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading

if sys.platform != 'linux' or os.geteuid() != 0:
    raise SystemExit('Requires root in an isolated Linux network namespace.')
if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
    raise SystemExit('Refusing the initial network namespace. Use sudo unshare --net.')

project = Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('isolated_netfilter',project/'custom_components/bluetti_local/netfilter.py')
nf=importlib.util.module_from_spec(spec);spec.loader.exec_module(nf)
def ip(*args):subprocess.run(['ip',*args],check=True,capture_output=True)
parent,child=socket.socketpair()
pid=os.fork()
if pid==0:
    parent.close()
    try:
        if ctypes.CDLL(None,use_errno=True).unshare(0x40000000):raise OSError(ctypes.get_errno(),'unshare failed')
        child.sendall(b'R');assert child.recv(1)==b'G'
        ip('link','set','lo','up');ip('link','set','blt_ups','up')
        ip('addr','add','192.168.1.25/24','dev','blt_ups')
        ip('addr','add','192.168.1.26/24','dev','blt_ups')
        ip('route','add','default','via','192.168.1.1')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
            udp.settimeout(3);udp.sendto(b'dns-test',('47.254.169.86',53));assert udp.recv(32)==b'dns-ok'
        for port,label in [(18760,b'data-ok'),(53,b'tcp-dns-ok')]:
            with socket.create_connection(('47.254.169.86',port),timeout=3) as tcp:
                tcp.sendall(b'synthetic');assert tcp.recv(32)==label
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as foreign:
            foreign.settimeout(.25);foreign.bind(('192.168.1.26',0))
            foreign.sendto(b'foreign-source',('47.254.169.86',53))
            try:foreign.recv(32);raise AssertionError('Foreign client was redirected')
            except (TimeoutError,ConnectionRefusedError,OSError):pass
        child.sendall(b'OK')
        os._exit(0)
    except BaseException as exc:
        child.sendall(('FAILED:'+type(exc).__name__).encode());os._exit(1)
child.close();parent.settimeout(10)
try:
    assert parent.recv(1)==b'R'
    ip('link','add','blt_router','type','veth','peer','name','blt_ups')
    ip('link','set','blt_ups','netns',str(pid))
    ip('link','set','lo','up');ip('link','set','blt_router','up')
    ip('addr','add','192.168.1.1/24','dev','blt_router')
    rules=nf.RouterRules('public-isolated-test',{'interface':'blt_router','device_ip':'192.168.1.25',
            'listen_host':'192.168.1.1','listen_port':18760,'dns_port':25354})
    n=nf.Netlink();n.batch([(0,nf.CREATE|nf.EXCL,nf.string(1,'foreign_test')+nf.attr(6,b'other-owner'))]);n.close()
    assert rules.apply()['changed'];assert not rules.apply()['changed']
    servers=[];errors=[]
    def server(kind,port,label):
        try:
            with socket.socket(socket.AF_INET,kind) as s:
                s.settimeout(6);s.bind(('192.168.1.1',port))
                if kind==socket.SOCK_DGRAM:
                    data,peer=s.recvfrom(32);assert peer[0]=='192.168.1.25';assert data==b'dns-test';s.sendto(label,peer)
                else:
                    s.listen();conn,peer=s.accept()
                    with conn:assert peer[0]=='192.168.1.25';conn.recv(32);conn.sendall(label)
        except BaseException as exc:errors.append(type(exc).__name__)
    for kind,port,label in [(socket.SOCK_DGRAM,25354,b'dns-ok'),(socket.SOCK_STREAM,18760,b'data-ok'),(socket.SOCK_STREAM,25354,b'tcp-dns-ok')]:
        t=threading.Thread(target=server,args=(kind,port,label));t.start();servers.append(t)
    parent.sendall(b'G');assert parent.recv(128)==b'OK'
    for t in servers:t.join(7)
    assert not errors,errors
    expired=rules.reconnect_data()
    assert rules.remove();assert not rules.remove()
    n=nf.Netlink();tables=n.dump(1)
    assert any(t.get(1)==b'foreign_test\0' for t in tables)
    assert not any(t.get(1)==rules.table.encode()+b'\0' for t in tables)
    n.batch([(2,0,nf.string(1,'foreign_test'))]);n.close()
    _,status=os.waitpid(pid,0);assert status==0
    print(json.dumps({'dns_udp':True,'dns_tcp':True,'mqtt_tcp':True,'other_client_untouched':True,
                     'idempotent':True,'unrelated_table_preserved':True,'owned_rules_removed':True,
                     'targeted_conntrack_entries_expired':expired,'production_network_accessed':False}))
except BaseException:
    try:os.kill(pid,9)
    except ProcessLookupError:pass
    raise
finally:
    parent.close()
