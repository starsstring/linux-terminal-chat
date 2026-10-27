#!/usr/bin/env python3
"""Run the actual C server and two actual C clients, and save their output."""
import json
import os
import select
import socket
import subprocess
import time
from pathlib import Path

root=Path(__file__).resolve().parent
binary=str(root/'chat')
with socket.socket() as probe:
    probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
processes={}
logs={'server':'','client-A':'','client-B':''}
events=[]

def pump(seconds=0.5):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        streams=[p.stdout for p in processes.values() if p.stdout and not p.stdout.closed]
        ready,_,_=select.select(streams,[],[],0.05)
        for stream in ready:
            data=os.read(stream.fileno(),8192)
            if data:
                name=next(k for k,p in processes.items() if p.stdout==stream)
                text=data.decode('utf-8',errors='replace')
                logs[name]+=text;print(f'[{name} output]\n{text}',end='',flush=True)
            else:stream.close()

def start(name,args):
    p=subprocess.Popen([binary,*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    processes[name]=p;pump()

def send(name,text):
    print(f'[{name} input] {text}',flush=True)
    events.append({'client':name,'input':text})
    processes[name].stdin.write((text+'\n').encode());processes[name].stdin.flush();pump()

try:
    print('Actual Linux C program demonstration',flush=True)
    start('server',['server',str(port)])
    start('client-A',['client','127.0.0.1',str(port)])
    start('client-B',['client','127.0.0.1',str(port)])
    send('client-A','Hello from terminal A!')
    send('client-B','你好，终端 A！')
    send('client-A','/quit')
    assert '[User 1] Hello from terminal A!' in logs['client-B']
    assert '[User 2] 你好，终端 A！' in logs['client-A']
    assert 'User 1 left.' in logs['client-B']
    print('DEMO PASS: two actual C clients exchanged messages and quit.',flush=True)
finally:
    for p in processes.values():
        if p.poll() is None:p.terminate()
    for p in processes.values():p.wait(timeout=5)
(root/'demo-output.json').write_text(json.dumps({'events':events,'stdout':logs},
                                             ensure_ascii=False,indent=2)+'\n')
