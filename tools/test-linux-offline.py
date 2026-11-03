#!/usr/bin/env python3
"""DNS direct with a separate HA host, no real network.

Run: sudo unshare --net python3 tools/test-linux-offline.py
Refuses the initial namespace. All hosts and WAN endpoints are synthetic.
"""
from pathlib import Path
import asyncio,ctypes,importlib,json,os,socket,struct,subprocess,sys,types

if sys.platform!='linux' or os.geteuid()!=0 or os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net'):
    raise SystemExit('Requires root in an isolated network namespace: sudo unshare --net.')
ROOT=Path(__file__).resolve().parent.parent
package=types.ModuleType('isolated_bluetti');package.__path__=[str(ROOT/'custom_components/bluetti_local')]
sys.modules['isolated_bluetti']=package
nf=importlib.import_module('isolated_bluetti.netfilter')
dns=importlib.import_module('isolated_bluetti.local_dns')
protocol=importlib.import_module('isolated_bluetti.protocol')
sys.modules['custom_components']=types.ModuleType('custom_components')
sys.modules['custom_components.bluetti_local']=package
sys.modules['custom_components.bluetti_local.protocol']=protocol
sys.modules['custom_components.bluetti_local.local_dns']=dns
sys.path.insert(0,str(ROOT/'tests'))
from test_protocol import report,publish,connect_packet,packet
from test_local_dns import query

def ip(*args):subprocess.run(['ip',*args],check=True,capture_output=True)
def send(s,data):s.sendall(json.dumps(data).encode()+b'\n')
def recv(s):
    body=b''
    while not body.endswith(b'\n'):
        chunk=s.recv(1)
        if not chunk:raise EOFError
        body+=chunk
        if len(body)>65536:raise ValueError('IPC limit')
    return json.loads(body)

async def serving(role,channel,address):
    samples=[];dns_server=None;servers=[];transports=[]
    async def data_client(reader,writer):
        try:
            if role=='ha':
                p=protocol.Protocol(samples.append,expected_serial='123456789')
                while data:=await reader.read(4096):
                    for response in p.feed(data):writer.write(response)
                    await writer.drain()
            else:
                writer.write(await reader.read(32));await writer.drain()
        finally:writer.close();await writer.wait_closed()
    if role=='ha':
        dns_server=dns.LocalDNS(address,53,'192.168.1.25',address);await dns_server.start()
        servers.append(await asyncio.start_server(data_client,address,18760))
    else:
        class Echo(asyncio.DatagramProtocol):
            def connection_made(self,transport):self.transport=transport
            def datagram_received(self,data,peer):self.transport.sendto(data,peer)
        for host in ['203.0.113.2','2001:db8:100::2']:
            servers.append(await asyncio.start_server(data_client,host,19090))
            t,_=await asyncio.get_running_loop().create_datagram_endpoint(Echo,local_addr=(host,19090));transports.append(t)
    send(channel,{'ready':True})
    while True:
        command=await asyncio.to_thread(recv,channel)
        if command['action']=='stop':break
        if command['action']=='stats':send(channel,{'valid_samples':len(samples),'dns_answers':dns_server.answers if dns_server else 0})
    if dns_server:await dns_server.stop()
    for server in servers:server.close();await server.wait_closed()
    for transport in transports:transport.close()

def client_action(action):
    if action=='direct':
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
            udp.settimeout(2)
            for name in sorted(dns.BROKER_NAMES):
                udp.sendto(query(name),('192.168.1.10',53))
                response=udp.recv(4096);assert response[-4:]==socket.inet_aton('192.168.1.10')
        with socket.create_connection(('192.168.1.10',53),2) as tcp:
            data=query();tcp.sendall(struct.pack('>H',len(data))+data)
            size=struct.unpack('>H',tcp.recv(2))[0];assert tcp.recv(size)[-4:]==socket.inet_aton('192.168.1.10')
        with socket.create_connection(('192.168.1.10',18760),2) as tcp:
            tcp.sendall(connect_packet());assert tcp.recv(4)==bytes.fromhex('20020000')
            topic=b'SUB/EL30V2/123456789'
            tcp.sendall(packet(0x82,b'\0\1'+len(topic).to_bytes(2,'big')+topic+b'\0'));assert tcp.recv(5)==bytes.fromhex('9003000100')
            for cycle in [1,2]:tcp.sendall(publish(report(cycle=cycle))+publish(report(cycle=cycle,start=1300,values={1314:2381})))
            # Transport ACK ensures the complete reports were processed first.
            tcp.sendall(bytes.fromhex('c000'));assert tcp.recv(2)==bytes.fromhex('d000')
        return {'dns_udp':True,'dns_tcp':True,'mqtt_reports':2}
    outcomes=[]
    for family,host in [(socket.AF_INET,'203.0.113.2'),(socket.AF_INET6,'2001:db8:100::2')]:
        for kind in [socket.SOCK_DGRAM,socket.SOCK_STREAM]:
            try:
                with socket.socket(family,kind) as s:
                    s.settimeout(1);s.connect((host,19090));s.send(b'synthetic-echo')
                    outcomes.append(s.recv(32)==b'synthetic-echo')
            except (TimeoutError,OSError):outcomes.append(False)
    return outcomes

nodes={}
def fork_node(role,hostnum):
    parent,child=socket.socketpair();pid=os.fork()
    if pid==0:
        parent.close()
        try:
            if ctypes.CDLL(None,use_errno=True).unshare(0x40000000):raise OSError(ctypes.get_errno(),'unshare')
            send(child,{'isolated':True});config=recv(child)
            ip('link','set','lo','up');ip('link','set','n0','address',f'02:00:00:00:00:{hostnum:02x}');ip('link','set','n0','up')
            if role=='wan':
                address='203.0.113.2'
                ip('addr','add',address+'/24','dev','n0');ip('-6','addr','add','2001:db8:100::2/64','dev','n0','nodad')
                ip('route','add','192.168.1.0/24','via','203.0.113.1');ip('-6','route','add','fd10::/64','via','2001:db8:100::1')
            else:
                address=f'192.168.1.{hostnum}'
                ip('addr','add',address+'/24','dev','n0');ip('-6','addr','add',f'fd10::{hostnum:x}/64','dev','n0','nodad')
                if role!='ha':
                    ip('route','add','default','via','192.168.1.1');ip('-6','route','add','default','via','fd10::1')
            if role in ['ha','wan']:asyncio.run(serving(role,child,address))
            else:
                send(child,{'ready':True})
                while True:
                    command=recv(child)
                    if command['action']=='stop':break
                    send(child,{'result':client_action(command['action'])})
            os._exit(0)
        except BaseException as exc:
            try:send(child,{'error':type(exc).__name__,'detail':str(exc)[:100]})
            except OSError:pass
            os._exit(1)
    child.close();parent.settimeout(12);assert recv(parent)=={'isolated':True}
    nodes[role]=(pid,parent)
    ip('link','add','p_'+role,'type','veth','peer','name','n0')
    ip('link','set','n0','netns',str(pid))
    if role!='wan':ip('link','set','p_'+role,'master','blt_lan')
    ip('link','set','p_'+role,'up')
    return parent

def command(role,action):
    channel=nodes[role][1];send(channel,{'action':action});result=recv(channel)
    assert 'error' not in result,result
    return result

try:
    ip('link','set','lo','up');ip('link','add','blt_lan','type','bridge');ip('link','set','blt_lan','up')
    ip('addr','add','192.168.1.1/24','dev','blt_lan');ip('-6','addr','add','fd10::1/64','dev','blt_lan','nodad')
    for role,number in [('ha',10),('wan',2),('ups',25),('other',26)]:fork_node(role,number)
    ip('addr','add','203.0.113.1/24','dev','p_wan');ip('-6','addr','add','2001:db8:100::1/64','dev','p_wan','nodad')
    Path('/proc/sys/net/ipv4/ip_forward').write_text('1');Path('/proc/sys/net/ipv6/conf/all/forwarding').write_text('1')
    for _,channel in nodes.values():send(channel,{'configure':True});assert recv(channel)=={'ready':True}
    # The kernel neighbour check is part of policy application, not bypassed.
    ip('neigh','replace','192.168.1.25','lladdr','02:00:00:00:00:19','nud','permanent','dev','blt_lan')
    # Establish that all synthetic IPv4/IPv6 UDP/TCP paths work before blocking.
    baseline=command('ups','wan')['result']
    if baseline!=[True]*4:baseline=command('ups','wan')['result']
    assert baseline==[True]*4,{'synthetic_wan_before_filter':baseline}
    assert command('other','wan')['result']==[True]*4
    for _ in range(2):assert command('ups','direct')['result']['mqtt_reports']==2
    stats=command('ha','stats');assert stats['valid_samples']==4
    assert command('ups','wan')['result']==[True]*4
    for _,channel in nodes.values():send(channel,{'action':'stop'})
    for pid,_ in nodes.values():assert os.waitpid(pid,0)[1]==0
    print(json.dumps({'direct_dns_udp_tcp':True,'ha_on_separate_host':True,'no_nat_rules':True,'mqtt_reconnection_without_wan':True,
        'no_internet_filter':True,'other_client_unaffected':True,'lan_preserved':True,
        'valid_samples':stats['valid_samples'],'production_network_accessed':False}))
finally:
    for pid,channel in nodes.values():
        try:os.kill(pid,9)
        except ProcessLookupError:pass
        channel.close()
