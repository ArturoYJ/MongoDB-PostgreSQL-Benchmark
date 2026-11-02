"""Read-only runtime contract and measured-operation evidence collectors."""
import datetime
import json
from pathlib import Path
import re
import subprocess
import time
import pymongo

from benchmark_runner.integrity import PROFILES, atomic_json


def docker_json(*args):
    return json.loads(subprocess.check_output(['docker',*args],text=True,timeout=15))


def inspect_environment(engine, profile):
    name='benchmark_'+engine
    info=docker_json('inspect',name)[0]
    other=docker_json('inspect','benchmark_'+('postgresql' if engine=='mongodb' else 'mongodb'))[0]
    cpu,mb=PROFILES[profile]; expected=mb*1048576
    if not info['State']['Running'] or other['State']['Running']: raise ValueError('Engine availability/isolation mismatch')
    cfg=info['HostConfig']
    if (cfg['NanoCpus'],cfg['Memory'],cfg['MemorySwap'])!=(cpu*10**9,expected,expected): raise ValueError('Docker resource profile mismatch')
    compose=docker_json('compose','config','--format','json')
    configured=docker_json('image','inspect',compose['services'][engine]['image'])[0]
    if configured['Id']!=info['Image']: raise ValueError('Running image differs from pinned Compose image')
    image=docker_json('image','inspect',info['Image'])[0]
    if image['Architecture']!='arm64': raise ValueError('Non-native database image')
    if any(binding['HostIp']!='127.0.0.1' for ports in cfg['PortBindings'].values() for binding in ports): raise ValueError('Non-loopback database port')
    command='for f in cpu.max memory.max memory.swap.max memory.events; do echo "$f"; cat /sys/fs/cgroup/$f; done'
    raw=subprocess.check_output(['docker','exec',name,'sh','-c',command],text=True,timeout=10)
    lines=raw.splitlines(); values={}
    for field in ('cpu.max','memory.max','memory.swap.max'): values[field]=lines[lines.index(field)+1]
    quota,period=map(int,values['cpu.max'].split())
    if quota/period!=cpu or int(values['memory.max'])!=expected or values['memory.swap.max']!='0': raise ValueError('Effective cgroup limits differ from requested profile')
    return dict(container_id=info['Id'],image=info['Image'],architecture=image['Architecture'],profile=profile,cgroups=raw)


def oom_events(container_id,start,end):
    text=subprocess.check_output(['docker','events','--since',str(start),'--until',str(end),
             '--filter','container='+container_id,'--filter','event=oom','--format','{{json .}}'],text=True,timeout=15)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class WorkloadEvidence:
    """Evidence from measured operations only; EXPLAIN never enters this collector."""
    def __init__(self,engine,attempt,output):
        self.engine=engine; self.attempt=attempt; self.output=Path(output); self.client=None; self.previous=None
        self.started=time.time()
    def start(self):
        if self.engine=='mongodb':
            self.client=pymongo.MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=5000,socketTimeoutMS=10000)
            db=self.client.benchmark; self.previous=db.command('profile',-1)
            if self.previous['was']!=0: raise ValueError('Existing Mongo profiler must be disabled before benchmark instrumentation')
            if 'system.profile' not in db.list_collection_names():
                db.create_collection('system.profile',capped=True,size=128*1024*1024)
            # Filter selects only this attempt; missing records fail the coverage check.
            db.command('profile',1,filter={'command.comment':{'$regex':'^bdaas:'+self.attempt+':'}})
    def finish(self,records):
        expected={r['tag'] for r in records}
        if not expected: raise ValueError('No completed operations for spill attribution')
        if self.engine=='mongodb':
            docs=list(self.client.benchmark['system.profile'].find({'command.comment':{'$regex':'^bdaas:'+self.attempt+':m:'}}))
            atomic_json(self.output/'measured_profile.json',docs)
            actual={d.get('command',{}).get('comment'):d for d in docs if d.get('command',{}).get('aggregate')=='orders'}
            if not expected<=actual.keys(): raise ValueError('Mongo profile missing measured operation records (possibly capped collection rollover)')
            if any(actual[t].get('errCode') for t in expected): raise ValueError('Mongo profile reports query failure')
            return any(actual[t].get('usedDisk',False) is True for t in expected)
        # Session log settings emit completion proof and temp files tagged with application_name.
        result=subprocess.run(['docker','logs','--since',str(self.started),'benchmark_postgresql'],capture_output=True,text=True,check=True,timeout=20)
        text=result.stdout+'\n'+result.stderr; (self.output/'measured_postgresql.log').write_text(text)
        complete=set(); spilled=set()
        for line in text.splitlines():
            tags=set(re.findall(r'bdaas:[a-f0-9]{32}:m:\d+:\d+',line))
            if 'duration:' in line and 'statement: /* bdaas:' in line: complete.update(tags)
            if re.search(r'temporary file:.*size [1-9][0-9]*',line): spilled.update(tags)
        if not expected<=complete: raise ValueError(f'PostgreSQL log missing completion proof for {len(expected-complete)} measured operations')
        return bool(expected & spilled)
    def close(self):
        if self.client is not None:
            try:
                if self.previous is not None:
                    db=self.client.benchmark
                    db.command('profile',self.previous['was'],slowms=self.previous.get('slowms',100),
                               sampleRate=self.previous.get('sampleRate',1),filter=self.previous.get('filter','unset'))
            finally: self.client.close()
