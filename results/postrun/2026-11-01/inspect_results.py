"""Non-destructive post-run census. Latest attempt is provisional, not accepted data."""
import collections,csv,datetime,gzip,hashlib,json,math,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
COLUMNS=['timestamp_epoch','engine','profile','query_id','concurrency','run_number','throughput_qps','latency_p50_ms','latency_p95_ms','latency_p99_ms','cpu_throttled_seconds','max_rss_mb','disk_spill','status']
KEYS=COLUMNS[1:6]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 with (ROOT/'results/metrics.csv').open(newline='') as f:
  reader=csv.DictReader(f); rows=list(reader); header=reader.fieldnames
 groups=collections.defaultdict(list); malformed=[]; numeric_issues=[]
 for line,row in enumerate(rows,2):
  if set(row)!=set(COLUMNS) or any(v is None for v in row.values()):malformed.append(line);continue
  groups[tuple(row[k] for k in KEYS)].append((line,row))
  for k in COLUMNS[6:12]:
   try:
    if not math.isfinite(float(row[k])) or float(row[k])<0: numeric_issues.append([line,k,row[k]])
   except (ValueError,TypeError): numeric_issues.append([line,k,row[k]])
 expected=set()
 for e in ('mongodb','postgresql'):
  for p in ('PROFILE_A_BASELINE','PROFILE_B_MEDIUM','PROFILE_C_SEVERE'):
   for q in (('Q1','Q2_allowDiskUse_false','Q2_allowDiskUse_true') if e=='mongodb' else ('Q1','Q2_work_mem_4MB','Q2_work_mem_64MB')):
    for c in ('1','10','50'):
     for r in ('1','2','3'): expected.add((e,p,q,c,r))
 catalog=[]; missing_plans=[]; invalid_plans=[]
 for key,attempts in sorted(groups.items()):
  e,p,q,c,r=key; base='Q1' if q=='Q1' else 'Q2'; variant='default' if q=='Q1' else q[3:]
  plan=ROOT/f'results/plans/{e}_{p}_{base}_{variant}_c{c}_run{r}.'; plan=Path(str(plan)+('json' if e=='mongodb' else 'txt'))
  present=plan.is_file()
  if not present:missing_plans.append(list(key))
  else:
   try:
    data=json.loads(plan.read_text()) if e=='mongodb' else plan.read_text()
    if not data: invalid_plans.append(str(plan.relative_to(ROOT)))
   except Exception:invalid_plans.append(str(plan.relative_to(ROOT)))
  for i,(line,row) in enumerate(attempts,1):
   catalog.append(dict(source_line=line,attempt_ordinal=i,is_latest_provisional=i==len(attempts),**row,
    plan_path=str(plan.relative_to(ROOT)),plan_exists=present,shared_plan_across_retries=len(attempts)>1,
    interpretation='requires_failure_review' if row['status'] in ('oom','error') else 'candidate_performance_row'))
 latest=[v[-1][1] for v in groups.values()]
 report=dict(header_matches=header==COLUMNS,rows=len(rows),unique_keys=len(groups),expected_keys=len(expected),
   missing_keys=sorted(expected-set(groups)),unexpected_keys=sorted(set(groups)-expected),
   duplicate_keys=sum(len(v)>1 for v in groups.values()),extra_attempts=len(rows)-len(groups),
   status_all=dict(collections.Counter(r['status'] for r in rows)),
   status_latest_provisional=dict(collections.Counter(r['status'] for r in latest)),
   latest_by_engine={e:dict(collections.Counter(r['status'] for r in latest if r['engine']==e)) for e in ('mongodb','postgresql')},
   malformed_lines=malformed,numeric_issues=numeric_issues,missing_plans=missing_plans,invalid_plans=invalid_plans,
   first_timestamp=min(int(r['timestamp_epoch']) for r in rows),last_timestamp=max(int(r['timestamp_epoch']) for r in rows),
   original_csv_sha256=sha(ROOT/'results/metrics.csv'),selection_policy='None accepted. Latest is descriptive/provisional only.')
 (OUT/'census.json').write_text(json.dumps(report,indent=2))
 with (OUT/'attempt_catalog.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(catalog[0]));w.writeheader();w.writerows(catalog)
 files=[ROOT/'results/metrics.csv']+list((ROOT/'results/plans').glob('*'))+list((ROOT/'results/logs').glob('*'))+list((ROOT/'benchmark_runner').glob('*.py'))+[ROOT/'run_benchmarks.sh',ROOT/'docker-compose.yml']
 (OUT/'sha256_manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):sha(p) for p in files if p.is_file()},indent=2))
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
