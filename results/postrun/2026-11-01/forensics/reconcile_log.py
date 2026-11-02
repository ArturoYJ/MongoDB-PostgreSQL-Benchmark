"""Reconcile terminal attempts with original CSV; derive evidence labels, never rewrite status."""
import ast,collections,csv,datetime,hashlib,json,re,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[3]
raw=(ROOT/'benchmark.log').read_bytes();text=raw.decode('utf-8',errors='replace')
rows=list(csv.DictReader((ROOT/'results/metrics.csv').open(newline='')))
bytime={int(r['timestamp_epoch']):(i,r) for i,r in enumerate(rows,2)}
starts=list(re.finditer(r'STARTING BENCHMARK CELL: (\w+) \| (\w+) \| (Q[12]) \| Concurrency: (\d+) \| Run: (\d+) \| Variant: (\w+)',text))
# De-duplicate repeated Docker log snapshots; retain file/line provenance.
kills={}
for path in sorted((ROOT/'results/logs').glob('*postgresql*.log')):
 for lineno,line in enumerate(path.read_text(errors='replace').splitlines(),1):
  if 'was terminated by signal 9: Killed' not in line:continue
  m=re.match(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) UTC',line)
  if not m:continue
  epoch=datetime.datetime.fromisoformat(m[1]).replace(tzinfo=datetime.timezone.utc).timestamp()
  key=line.strip();kills.setdefault(key,dict(epoch=epoch,text=line,sources=[]))['sources'].append({'file':str(path.relative_to(ROOT)),'line':lineno})
records=[];unmatched=[];mismatches=[]
for n,match in enumerate(starts):
 block=text[match.start():starts[n+1].start() if n+1<len(starts) else len(text)]
 rec=re.search(r'Recorded (?:verified )?metric row in results/metrics.csv: (\{[^\n]+\})',block)
 if not rec:unmatched.append(n+1);continue
 metric=ast.literal_eval(rec[1]);ts=int(metric['timestamp_epoch'])
 if ts not in bytime:unmatched.append(n+1);continue
 line,row=bytime[ts]
 if any(str(metric[k])!=v for k,v in row.items()):mismatches.append(line)
 wp=[float(x) for x in re.findall(r'\[WARMUP\] ([\d.]+)s',block)];mp=[float(x) for x in re.findall(r'\[MEASUREMENT\] ([\d.]+)s',block)]
 wm=max(wp) if wp else None;mm=max(mp) if mp else None
 caught=re.findall(r'Worker \d+ caught [^\r\n]+',block)
 timeout=bool(re.search(r'statement timeout|timed out|NetworkTimeout|time limit', '\n'.join(caught),re.I))
 disconnect=bool(re.search('server closed the connection unexpectedly','\n'.join(caught),re.I))
 warmup_errors=sum('warmup failure' in x for x in caught)
 telemetry_errors='Telemetry validation error:' in block
 forced=bool(re.search(r'did not exit within deadline|exited with non-zero|Worker file .* missing|Error reading worker file',block))
 # Approximate boundaries only: console progress has one-second granularity.
 candidate_start=ts-(wm+4 if wm is not None else 4);candidate_end=ts+(mm+4 if mm is not None else 4)
 related=[k for k in kills.values() if candidate_start<=k['epoch']<=candidate_end] if row['engine']=='postgresql' else []
 if related and disconnect and mm is None:classification='warmup_sigkill_failure_oom_consistent'
 elif timeout:classification='timeout_incomplete_measurement' if mm is None or mm<299 else 'timeout_requires_review'
 elif forced:classification='forced_worker_termination_partial_results'
 elif caught or telemetry_errors:classification='other_failure_requires_review'
 elif row['status']=='oom' and mm is not None and mm>=299 and not related:classification='full_measurement_persistent_oom_label_suspected'
 elif row['status'] in ('ok','spill') and mm is not None and mm>=299:classification='full_measurement_performance_candidate'
 else:classification='unresolved'
 records.append(dict(csv_line=line,terminal_attempt=n+1,terminal_start_line=text[:match.start()].count('\n')+1,
    terminal_row_line=text[:match.start()+rec.start()].count('\n')+1,**row,
    max_warmup_progress_s=wm,max_measurement_progress_s=mm,worker_failure_messages=len(caught),warmup_failure_messages=warmup_errors,
    timeout_evidence=timeout,disconnect_evidence=disconnect,telemetry_error=telemetry_errors,forced_or_missing_worker=forced,
    matched_sigkill_events=len(related),sigkill_times_utc=';'.join(datetime.datetime.fromtimestamp(k['epoch'],datetime.timezone.utc).isoformat() for k in related),
    derived_classification=classification,worker_failure_examples=' | '.join(list(dict.fromkeys(caught))[:3])))
keys=['engine','profile','query_id','concurrency','run_number'];latest={tuple(r[k] for k in keys):r for r in records}
for record in records:record['latest_attempt_provisional']=latest[tuple(record[k] for k in keys)]['csv_line']==record['csv_line']
with (OUT/'attempt_reconciliation.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
(OUT/'sigkill_events.json').write_text(json.dumps(sorted(kills.values(),key=lambda x:x['epoch']),indent=2))
summary=dict(log_attempts=len(starts),matched_rows=len(records),unmatched_attempts=unmatched,row_mismatches=mismatches,
 all_classifications=dict(collections.Counter(r['derived_classification'] for r in records)),
 latest_classifications=dict(collections.Counter(r['derived_classification'] for r in latest.values())),
 distinct_logged_sigkill_events=len(kills),benchmark_log_sha256=hashlib.sha256(raw).hexdigest(),
 caveat='Derived labels are not corrected canonical statuses. SIGKILL is consistent with OOM but is not standalone kernel-cause proof. Progress bounds are approximate.')
(OUT/'reconciliation_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
print('Latest failing/suspect groups:')
for key,count in collections.Counter(tuple(r[k] for k in keys[:4])+ (r['derived_classification'],) for r in latest.values() if r['derived_classification']!='full_measurement_performance_candidate').items():print(key,count)
