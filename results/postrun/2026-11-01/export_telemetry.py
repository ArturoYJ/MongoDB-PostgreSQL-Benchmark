"""Archive available Prometheus source samples without altering service configuration."""
import csv,gzip,json,math,subprocess,time
from pathlib import Path
import requests
OUT=Path(__file__).resolve().parent; ROOT=OUT.parents[2]
rows=list(csv.DictReader((ROOT/'results/metrics.csv').open()))
start=min(int(r['timestamp_epoch']) for r in rows)-600
end=time.time(); duration=math.ceil(end-start)
metrics=['container_memory_rss','container_cpu_cfs_throttled_seconds_total','container_cpu_usage_seconds_total','container_last_seen','up','node_memory_MemAvailable_bytes','node_memory_SwapTotal_bytes','node_memory_SwapFree_bytes','node_vmstat_pswpin','node_vmstat_pswpout','node_disk_read_bytes_total','node_disk_written_bytes_total']
summary={'requested_start':start,'requested_end':end,'exports':{},'notes':'Available raw samples only; no completeness assertion. Container identity/time-window mapping still requires validation.'}
for metric in metrics:
 selector=metric+('{id=~"/docker/.+"}' if metric.startswith('container_') else '')
 query=f'{selector}[{duration}s] @ {end:.3f}'
 try:
  response=requests.get('http://127.0.0.1:9090/api/v1/query',params={'query':query},timeout=90)
  response.raise_for_status(); data=response.json()
  if data.get('status')!='success':raise RuntimeError(str(data))
  result=data['data']['result'];times=[float(t) for s in result for t,v in s.get('values',[])]
  with gzip.open(OUT/f'{metric}.json.gz','wt') as f:json.dump({'query':query,'response':data},f)
  summary['exports'][metric]={'series':len(result),'samples':len(times),'first':min(times) if times else None,'last':max(times) if times else None}
  print(metric,summary['exports'][metric],flush=True)
 except Exception as exc:summary['exports'][metric]={'error':str(exc)}
for endpoint,name in [('status/config','prometheus_config'),('status/flags','prometheus_flags'),('targets','prometheus_targets')]:
 try:
  response=requests.get('http://127.0.0.1:9090/api/v1/'+endpoint,timeout=10);response.raise_for_status()
  (OUT/f'{name}.json').write_text(json.dumps(response.json(),indent=2))
 except Exception as exc:summary[name]={'error':str(exc)}
state=json.loads(subprocess.check_output(['docker','inspect','benchmark_mongodb','benchmark_postgresql'],text=True))
# Keep state and image/mount identity, omit credential-bearing environment variables.
(OUT/'database_container_state.json').write_text(json.dumps([{k:v[k] for k in ('Id','Name','Image','State','Mounts')} for v in state],indent=2))
(OUT/'telemetry_export_summary.json').write_text(json.dumps(summary,indent=2))
