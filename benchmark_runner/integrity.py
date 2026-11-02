"""Cell identities, immutable attempts and evidence-backed resume receipts."""
import argparse
from contextlib import contextmanager
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import uuid

from benchmark_runner.contracts import CANONICAL_COLUMNS

PROFILES = {'PROFILE_A_BASELINE': (4,4096), 'PROFILE_B_MEDIUM': (2,2048), 'PROFILE_C_SEVERE': (1,1024)}

def cell_key(engine, profile, query_id, concurrency, run):
    return f'{engine}_{profile}_{query_id}_c{int(concurrency)}_r{int(run)}'

def completed_in_window(records, start, end):
    return [r for r in records if r['ok'] and start <= r['start'] <= r['end'] <= end]

def atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w') as f:
        json.dump(value,f,indent=2,default=str,allow_nan=False); f.flush(); os.fsync(f.fileno())
    os.replace(temp,path)

def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def contract():
    files=sorted(Path('benchmark_runner').glob('*.py'))+sorted(Path('queries').glob('*/*'))+[Path('docker-compose.yml'),Path('run_benchmarks.sh'),Path('data/orders.jsonl')]
    hashes={str(p):file_hash(p) for p in files}
    images=json.loads(subprocess.check_output(['docker','inspect','benchmark_mongodb','benchmark_postgresql'],text=True))
    hashes['images']={v['Name']:v['Image'] for v in images}
    return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()

def evidence_root(csv_path): return Path(csv_path).with_suffix('.evidence')

def new_attempt(csv_path,key):
    path=evidence_root(csv_path)/key/uuid.uuid4().hex
    path.mkdir(parents=True,exist_ok=False)
    return path

def validate_row(row):
    if set(row)!=set(CANONICAL_COLUMNS): raise ValueError('Row schema mismatch')
    if row['engine'] not in ('mongodb','postgresql') or row['profile'] not in PROFILES:
        raise ValueError('Invalid engine/profile')
    if int(row['concurrency']) not in (1,10,50) or int(row['run_number'])<1 or int(row['timestamp_epoch'])<=0:
        raise ValueError('Invalid cell identity/timestamp')
    if row['status'] not in ('ok','spill','oom','error'): raise ValueError('Invalid status')
    numeric=CANONICAL_COLUMNS[6:12]
    for k in numeric:
        if row[k] not in ('',None) and (not math.isfinite(float(row[k])) or float(row[k])<0):
            raise ValueError(f'Invalid metric: {k}')
    if row['status'] in ('ok','spill'):
        if any(row[k] in ('',None) for k in numeric): raise ValueError('Incomplete successful row')
        if float(row['throughput_qps'])<=0: raise ValueError('No completed queries for successful row')
        if not float(row['latency_p50_ms'])<=float(row['latency_p95_ms'])<=float(row['latency_p99_ms']):
            raise ValueError('Invalid latency ordering')
        if str(row['disk_spill']) != str(row['status']=='spill'): raise ValueError('Spill/status mismatch')


def valid_receipt(root, row, expected_contract, durations):
    try: validate_row(row)
    except (ValueError,TypeError): return False
    key=cell_key(*(row[k] for k in ('engine','profile','query_id','concurrency','run_number')))
    for path in (root/key).glob('*/receipt.json'):
        try:
            receipt=json.loads(path.read_text())
            if not receipt['valid'] or receipt['contract']!=expected_contract or receipt['durations']!=durations or receipt['row']!=row: continue
            if not receipt['files']: continue
            if all(file_hash(path.parent/name)==digest for name,digest in receipt['files'].items()): return True
        except (OSError,KeyError,ValueError,TypeError): continue
    return False

def load_valid_rows(csv_path, expected_contract, durations):
    if not Path(csv_path).exists(): return []
    with open(csv_path,newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=CANONICAL_COLUMNS: raise ValueError('CSV schema mismatch')
        rows=list(reader)
    seen=set()
    for row in rows:
        if None in row or any(v is None for v in row.values()): raise ValueError('Truncated CSV row')
        key=cell_key(*(row[k] for k in ('engine','profile','query_id','concurrency','run_number')))
        if key in seen: raise ValueError(f'Duplicate CSV cell: {key}')
        seen.add(key)
        if not valid_receipt(evidence_root(csv_path),row,expected_contract,durations):
            raise ValueError(f'No valid evidence receipt for {key}; preserve/archive this CSV before retrying')
    return rows

def commit_row(csv_path, attempt, row, fingerprint, durations):
    # Invalid attempts are never appended to the official CSV. Their row.json remains evidence.
    validate_row(row)
    manifest=json.loads((attempt/'manifest.json').read_text())
    if not manifest.get('valid'): raise ValueError('Cannot commit invalid measurement')
    if row['status'] in ('error','oom') and not manifest.get('observed_engine_failure'): raise ValueError('Unproven engine outcome')
    normalized={k:'' if row[k] is None else str(row[k]) for k in CANONICAL_COLUMNS}
    files={str(p.relative_to(attempt)):file_hash(p) for p in attempt.rglob('*') if p.is_file() and p.name!='receipt.json'}
    atomic_json(attempt/'receipt.json',dict(valid=True,contract=fingerprint,durations=durations,row=normalized,files=files))
    path=Path(csv_path); path.parent.mkdir(parents=True,exist_ok=True)
    exists=path.exists() and path.stat().st_size>0
    with path.open('a',newline='') as f:
        w=csv.DictWriter(f,fieldnames=CANONICAL_COLUMNS)
        if not exists: w.writeheader()
        w.writerow(normalized); f.flush(); os.fsync(f.fileno())

def expected_keys():
    for e in ('mongodb','postgresql'):
      for p in PROFILES:
       variants=('Q1','Q2_allowDiskUse_false','Q2_allowDiskUse_true') if e=='mongodb' else ('Q1','Q2_work_mem_4MB','Q2_work_mem_64MB')
       for q in variants:
        for c in (1,10,50):
         for r in (1,2,3): yield cell_key(e,p,q,c,r)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--csv',required=True); p.add_argument('--key'); p.add_argument('--summary',action='store_true'); a=p.parse_args()
    rows=load_valid_rows(a.csv,contract(),[60.0,300.0,60.0])
    keys={cell_key(*(r[k] for k in ('engine','profile','query_id','concurrency','run_number'))) for r in rows}
    if a.key: return 0 if a.key in keys else 1
    if a.summary:
        if keys!=set(expected_keys()): raise ValueError(f'Matrix incomplete: {len(keys)}/162 valid keys')
        print('162 evidence-validated outcomes:',{s:sum(r['status']==s for r in rows) for s in ('ok','spill','oom','error')})
    return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print(f'Integrity failure: {exc}'); raise SystemExit(2)


@contextmanager
def exclusive_cell_lock(path='.benchmark-cell.lock'):
    lock=Path(path)
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError('Another cell is active or a stale cell lock exists; refusing concurrent measurements')
    try: yield
    finally: lock.rmdir()
