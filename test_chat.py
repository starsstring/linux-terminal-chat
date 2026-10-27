#!/usr/bin/env python3
"""Integration tests: run on Linux after compiling ./chat."""
import hashlib
import json
import os
import platform
import select
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = str(ROOT / 'chat')
results = []

def check(name, fn):
    try:
        fn()
        results.append({'test': name, 'status': 'PASS'})
        print('PASS', name, flush=True)
    except Exception as exc:
        results.append({'test': name, 'status': 'FAIL', 'detail': repr(exc)})
        print('FAIL', name, repr(exc), flush=True)

def expect(sock, text, timeout=4):
    end = time.monotonic() + timeout
    data = b''
    while text not in data:
        remaining = end - time.monotonic()
        assert remaining > 0, (text, data)
        sock.settimeout(remaining)
        block = sock.recv(8192)
        assert block, ('connection closed', data)
        data += block
    return data

def drain(sock):
    while select.select([sock], [], [], 0.05)[0]:
        if not sock.recv(8192):
            break

def unused_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

PORT = unused_port()
srv = subprocess.Popen([BIN, 'server', str(PORT)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
peers = []

def connect():
    s = socket.create_connection(('127.0.0.1', PORT), timeout=3)
    peers.append(s)
    welcome = expect(s, b'joined.\n')
    return s, welcome

def message(s, *targets, payload=b'hello\n'):
    for peer in set([s, *targets]):
        drain(peer)
    s.sendall(payload)
    for peer in set([s, *targets]):
        expect(peer, payload)

try:
    assert select.select([srv.stdout], [], [], 5)[0], 'server startup timeout'
    startup = srv.stdout.readline().decode()
    a, aw = connect()
    b, bw = connect()
    def welcome_check():
        assert b'You are User 1.' in aw and b'You are User 2.' in bw
    check('automatic_user_ids', welcome_check)
    check('bidirectional_group_chat', lambda: (message(a,b), message(b,a,payload=b'reply\n')))
    check('utf8_chinese', lambda: message(a,b,payload='你好，鲁班猫！\n'.encode()))
    def split():
        drain(a); drain(b)
        a.sendall(b'frag'); time.sleep(0.1)
        assert not select.select([b], [], [], 0.15)[0]
        a.sendall(b'ment\n')
        expect(b,b'fragment\n')
    check('tcp_split_message', split)
    def merged():
        drain(a); drain(b)
        a.sendall(b'one\ntwo\nthree\n')
        data=expect(b,b'three\n')
        assert b'one\n' in data and b'two\n' in data
    check('tcp_coalesced_messages', merged)
    def crlf():
        drain(a); drain(b); a.sendall(b'windows-line\r\n')
        data=expect(b,b'windows-line\n')
        assert b'\r' not in data
    check('crlf_input',crlf)
    check('maximum_length_message',lambda:message(a,b,payload=b'x'*1023+b'\n'))
    def overlong():
        c,_=connect(); c.sendall(b'x'*1024+b'\n')
        expect(c,b'Invalid or too long message.\n'); c.settimeout(3)
        try: assert c.recv(100)==b''
        except ConnectionResetError: pass
        message(a,b,payload=b'still-alive\n')
    check('overlong_disconnect_isolated',overlong)
    def quit_reconnect():
        c,_=connect(); drain(a); c.sendall(b'/quit\n')
        expect(a,b'left.\n')
        d,_=connect(); message(d,a,payload=b'reconnected\n')
        d.close()
    check('quit_and_reconnect',quit_reconnect)
    def control():
        c,_=connect(); c.sendall(b'bad\x1b[31m\n')
        expect(c,b'Invalid or too long message.\n')
        message(a,b,payload=b'healthy\n')
    check('reject_terminal_control_characters',control)
    def capacity():
        # Use an independent server so occupancy is deterministic.
        port=unused_port()
        p=subprocess.Popen([BIN,'server',str(port)],stdout=subprocess.PIPE)
        sockets=[]
        try:
            assert select.select([p.stdout],[],[],5)[0]
            p.stdout.readline()
            for _ in range(16):
                s=socket.create_connection(('127.0.0.1',port),timeout=3)
                sockets.append(s); expect(s,b'joined.\n')
            extra=socket.create_connection(('127.0.0.1',port),timeout=3)
            sockets.append(extra); extra.settimeout(3)
            assert extra.recv(100)==b''
            assert p.poll() is None
        finally:
            for s in sockets:s.close()
            p.terminate();p.wait(timeout=5)
    check('sixteen_clients_and_capacity_limit',capacity)
    def invalid():
        for args in [[],['server','0'],['server','65536'],['server','x'],
                     ['client','bad-ip','8888']]:
            p=subprocess.run([BIN,*args],capture_output=True,timeout=5)
            assert p.returncode!=0 and p.stderr
    check('invalid_arguments',invalid)
    def real_clients():
        c=subprocess.Popen([BIN,'client','127.0.0.1',str(PORT)],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            assert select.select([c.stdout],[],[],4)[0]
            initial=os.read(c.stdout.fileno(),8192)
            assert b'You are User' in initial
            drain(a); c.stdin.write(b'from-real-client\n');c.stdin.flush()
            expect(a,b'from-real-client\n')
            a.sendall(b'to-real-client\n')
            end=time.monotonic()+4;data=initial
            while b'to-real-client\n' not in data:
                assert time.monotonic()<end
                if select.select([c.stdout],[],[],0.2)[0]:
                    data+=os.read(c.stdout.fileno(),8192)
            c.stdin.write(b'/quit\n');c.stdin.flush()
            c.wait(timeout=4);assert c.returncode==0
        finally:
            if c.poll() is None:c.kill();c.wait()
    check('real_c_client_send_receive_quit',real_clients)
    def eof():
        p=subprocess.run([BIN,'client','127.0.0.1',str(PORT)],input=b'',
                         capture_output=True,timeout=4)
        assert p.returncode==0
    check('client_stdin_eof',eof)
    def shutdown():
        c=subprocess.Popen([BIN,'client','127.0.0.1',str(PORT)],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            assert select.select([c.stdout],[],[],4)[0]
            os.read(c.stdout.fileno(),8192)
            srv.terminate();srv.wait(timeout=4)
            c.wait(timeout=4)
            assert b'Connection closed.' in c.stdout.read()
        finally:
            if c.poll() is None:c.kill();c.wait()
    check('server_shutdown_client_exits',shutdown)
finally:
    for s in peers:s.close()
    if srv.poll() is None:srv.terminate();srv.wait(timeout=5)

record={
 'platform':platform.platform(),
 'os_release':Path('/etc/os-release').read_text(),
 'compiler':subprocess.check_output(['gcc','--version'],text=True).splitlines()[0],
 'source_sha256':hashlib.sha256((ROOT/'chat.c').read_bytes()).hexdigest(),
 'server_startup':startup.strip(),
 'passed':sum(r['status']=='PASS' for r in results),
 'total':len(results),'tests':results,
 'scope':'Local Linux virtual machine; no physical LubanCat or cross-device test.'
}
(ROOT/'test-results.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(record,ensure_ascii=False,indent=2))
raise SystemExit(any(r['status']!='PASS' for r in results))
