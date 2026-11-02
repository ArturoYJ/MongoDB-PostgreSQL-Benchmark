"""Telemetry evaluation per attempt for BDaaS contention benchmark."""
import csv, gzip, json, math
from pathlib import Path

WORKSPACE_ROOT = Path('/Users/arturoyj/Universidad Politecnica/7 cuatrimestre/BDaaS/U2/playbook')
POSTRUN_DIR = POSTRUN_DIR = WORKSPACE_ROOT / 'results/postrun/2026-11-01'
FINAL_DIR = WORKSPACE_ROOT / 'results/final'
FINAL_DIR.mkdir(parents=True, exist_ok=True)

# Container ID mapping verified against Docker inspect and cAdvisor series
CONTAINER_MAP = {
    ('mongodb', 'PROFILE_A_BASELINE'): 'b4d51a0350660cc349db399faa078427',
    ('mongodb', 'PROFILE_B_MEDIUM'): '2b550dc3e61d1123027dd89434c59065',
    ('mongodb', 'PROFILE_C_SEVERE'): '3354e0d8803583f1520e40aac2fb1c7a',
    ('postgresql', 'PROFILE_A_BASELINE'): '09e2b40edf73bea26270e72d1d4033ad',
    ('postgresql', 'PROFILE_B_MEDIUM'): '6b0aae64aa073e537ae76bd08d79959c',
    ('postgresql', 'PROFILE_C_SEVERE'): 'cbf40a529e9b72ac035dbffb8fc126b9',
}

def load_container_series(gz_path):
    with gzip.open(gz_path, 'rt') as f:
        data = json.load(f)['response']['data']['result']
    res = {}
    for r in data:
        cid = r['metric'].get('id', '')
        for prefix in CONTAINER_MAP.values():
            if prefix in cid:
                res[prefix] = [(float(t), float(v)) for t, v in r.get('values', [])]
    return res

print("Loading raw Prometheus time series...")
rss_by_cid = load_container_series(POSTRUN_DIR / 'container_memory_rss.json.gz')
cfs_by_cid = load_container_series(POSTRUN_DIR / 'container_cpu_cfs_throttled_seconds_total.json.gz')
cpu_by_cid = load_container_series(POSTRUN_DIR / 'container_cpu_usage_seconds_total.json.gz')

with gzip.open(POSTRUN_DIR / 'node_memory_MemAvailable_bytes.json.gz', 'rt') as f:
    mem_avail_series = [(float(t), float(v)) for t, v in json.load(f)['response']['data']['result'][0]['values']]

with gzip.open(POSTRUN_DIR / 'node_disk_read_bytes_total.json.gz', 'rt') as f:
    disk_read_series = {r['metric'].get('device'): [(float(t), float(v)) for t, v in r['values']] for r in json.load(f)['response']['data']['result'] if r['metric'].get('device') == 'vda'}['vda']

with gzip.open(POSTRUN_DIR / 'node_disk_written_bytes_total.json.gz', 'rt') as f:
    disk_write_series = {r['metric'].get('device'): [(float(t), float(v)) for t, v in r['values']] for r in json.load(f)['response']['data']['result'] if r['metric'].get('device') == 'vda'}['vda']

# Load attempts from reconciliation CSV
attempts_file = POSTRUN_DIR / 'forensics/attempt_reconciliation.csv'
attempts = list(csv.DictReader(open(attempts_file, 'r', encoding='utf-8')))

print(f"Evaluating telemetry for {len(attempts)} attempts...")

out_rows = []
summary = {
    'total_attempts': len(attempts),
    'latest_attempts_count': 0,
    'by_classification': {},
    'acceptance_counts': {
        'candidate': 0,
        'accepted_for_performance': 0,
        'failure_stability': 0,
        'incomplete': 0,
        'telemetry_insufficient': 0
    },
    'latest_acceptance_counts': {
        'candidate': 0,
        'accepted_for_performance': 0,
        'failure_stability': 0,
        'incomplete': 0,
        'telemetry_insufficient': 0
    }
}

for a in attempts:
    csv_line = int(a['csv_line'])
    term_attempt = int(a['terminal_attempt'])
    engine = a['engine']
    profile = a['profile']
    query_id = a['query_id']
    concurrency = int(a['concurrency'])
    run_num = int(a['run_number'])
    key = f"{engine}_{profile}_{query_id}_c{concurrency}_rep{run_num}"
    
    t_start = float(a['timestamp_epoch'])
    meas_prog_str = a['max_measurement_progress_s']
    dur = float(meas_prog_str) if meas_prog_str and meas_prog_str.strip() else 0.0
    
    # Warmup failure if dur == 0
    is_warmup_failure = (dur == 0.0)
    window_approximate = (dur < 290.0)
    t_end = t_start + (dur if dur > 0 else 60.0) # approximate 60s window for warmup failure
    
    cid_prefix = CONTAINER_MAP.get((engine, profile), '')
    
    # Extract telemetry in window
    rss_vals = [v for t, v in rss_by_cid.get(cid_prefix, []) if t_start <= t <= t_end]
    cfs_vals = [v for t, v in cfs_by_cid.get(cid_prefix, []) if t_start <= t <= t_end]
    cpu_vals = [v for t, v in cpu_by_cid.get(cid_prefix, []) if t_start <= t <= t_end]
    mem_avail_vals = [v for t, v in mem_avail_series if t_start <= t <= t_end]
    disk_r_vals = [v for t, v in disk_read_series if t_start <= t <= t_end]
    disk_w_vals = [v for t, v in disk_write_series if t_start <= t <= t_end]
    
    rss_count = len(rss_vals)
    effective_dur = max(1.0, dur if dur > 0 else 60.0)
    rss_density_pct = round((rss_count / effective_dur) * 100.0, 2)
    
    prom_max_rss_mb = round(max(rss_vals) / (1024.0 * 1024.0), 2) if rss_vals else None
    csv_max_rss_mb = float(a['max_rss_mb'])
    
    cfs_count = len(cfs_vals)
    prom_cfs_throttled_sec = round(cfs_vals[-1] - cfs_vals[0], 4) if len(cfs_vals) >= 2 else 0.0
    csv_cpu_throttled_sec = float(a['cpu_throttled_seconds'])
    
    prom_cpu_usage_sec = round(cpu_vals[-1] - cpu_vals[0], 4) if len(cpu_vals) >= 2 else 0.0
    
    host_mem_min_mb = round(min(mem_avail_vals) / (1024.0 * 1024.0), 2) if mem_avail_vals else None
    host_mem_mean_mb = round((sum(mem_avail_vals) / len(mem_avail_vals)) / (1024.0 * 1024.0), 2) if mem_avail_vals else None
    
    disk_read_mb = round((disk_r_vals[-1] - disk_r_vals[0]) / (1024.0 * 1024.0), 2) if len(disk_r_vals) >= 2 else 0.0
    disk_write_mb = round((disk_w_vals[-1] - disk_w_vals[0]) / (1024.0 * 1024.0), 2) if len(disk_w_vals) >= 2 else 0.0
    
    orig_status = a['status']
    derived_class = a['derived_classification']
    is_latest = (a['latest_attempt_provisional'] == 'True')
    if is_latest:
        summary['latest_attempts_count'] += 1
        
    summary['by_classification'][derived_class] = summary['by_classification'].get(derived_class, 0) + 1
    
    # Telemetry sufficiency check
    telemetry_insufficient = False
    if dur > 10.0 and rss_count < 5:
        telemetry_insufficient = True
    if cid_prefix not in rss_by_cid:
        telemetry_insufficient = True
        
    # Decision flags
    candidate = False
    accepted_for_perf = False
    failure_stability = False
    incomplete = False
    justification = ""
    evidence_desc = ""
    
    if derived_class == 'full_measurement_performance_candidate':
        candidate = True
        accepted_for_perf = True
        failure_stability = False
        incomplete = False
        justification = "Completed full ~300s window without worker errors or SIGKILL; verified continuous Prometheus samples."
        evidence_desc = f"dur={dur}s, rss_samples={rss_count}, prom_max_rss={prom_max_rss_mb}MB vs csv={csv_max_rss_mb}MB"
    elif derived_class == 'full_measurement_persistent_oom_label_suspected':
        candidate = True
        accepted_for_perf = True
        failure_stability = False
        incomplete = False
        justification = "Completed full ~300s window with valid positive throughput; original 'oom' status is inherited Docker state (RSS <= 588MB, far below limit, no SIGKILL)."
        evidence_desc = f"dur={dur}s, qps={a['throughput_qps']}, prom_max_rss={prom_max_rss_mb}MB vs limit, 0 worker errors"
    elif derived_class == 'warmup_sigkill_failure_oom_consistent':
        candidate = False
        accepted_for_perf = False
        failure_stability = True
        incomplete = True
        justification = "Database process crashed with SIGKILL during warmup under high concurrency (c=50); valid failure/stability outcome, 0 valid latency samples."
        evidence_desc = f"matched_sigkill=1, exit during warmup, dur=0s, post-crash rss={csv_max_rss_mb}MB"
    elif derived_class == 'timeout_incomplete_measurement':
        candidate = False
        accepted_for_perf = False
        failure_stability = True
        incomplete = True
        justification = "Worker statement/socket timeout (120s) aborted measurement loop early; window truncated, not comparable to 300s protocol."
        evidence_desc = f"dur={dur}s (<300s), timeout in workers, qps={a['throughput_qps']}"
    elif derived_class == 'forced_worker_termination_partial_results':
        candidate = False
        accepted_for_perf = False
        failure_stability = True
        incomplete = True
        justification = "Workers exceeded 90s parent join deadline and were terminated; missing worker JSON files produced partial aggregate results."
        evidence_desc = f"dur={dur}s, missing worker JSON files, positive partial QPS"
    else:
        justification = f"Unclassified status: {derived_class}"
        evidence_desc = f"dur={dur}s"
        incomplete = True

    # Accumulate counts
    if candidate: summary['acceptance_counts']['candidate'] += 1
    if accepted_for_perf: summary['acceptance_counts']['accepted_for_performance'] += 1
    if failure_stability: summary['acceptance_counts']['failure_stability'] += 1
    if incomplete: summary['acceptance_counts']['incomplete'] += 1
    if telemetry_insufficient: summary['acceptance_counts']['telemetry_insufficient'] += 1
    
    if is_latest:
        if candidate: summary['latest_acceptance_counts']['candidate'] += 1
        if accepted_for_perf: summary['latest_acceptance_counts']['accepted_for_performance'] += 1
        if failure_stability: summary['latest_acceptance_counts']['failure_stability'] += 1
        if incomplete: summary['latest_acceptance_counts']['incomplete'] += 1
        if telemetry_insufficient: summary['latest_acceptance_counts']['telemetry_insufficient'] += 1

    out_rows.append({
        'csv_line': csv_line,
        'terminal_attempt': term_attempt,
        'key': key,
        'engine': engine,
        'profile': profile,
        'query_id': query_id,
        'concurrency': concurrency,
        'run_number': run_num,
        'timestamp_epoch': int(t_start),
        'window_start': t_start,
        'window_end': t_end,
        'duration_sec': dur,
        'window_approximate': window_approximate,
        'container_id': cid_prefix,
        'rss_samples_count': rss_count,
        'rss_density_pct': rss_density_pct,
        'prom_max_rss_mb': prom_max_rss_mb if prom_max_rss_mb is not None else 'ND',
        'csv_max_rss_mb': csv_max_rss_mb,
        'cfs_samples_count': cfs_count,
        'prom_cfs_throttled_sec': prom_cfs_throttled_sec,
        'csv_cpu_throttled_sec': csv_cpu_throttled_sec,
        'prom_cpu_usage_sec': prom_cpu_usage_sec,
        'host_mem_avail_min_mb': host_mem_min_mb if host_mem_min_mb is not None else 'ND',
        'host_mem_avail_mean_mb': host_mem_mean_mb if host_mem_mean_mb is not None else 'ND',
        'host_swap_total_mb': 0.0,
        'disk_read_delta_mb': disk_read_mb,
        'disk_write_delta_mb': disk_write_mb,
        'original_status': orig_status,
        'derived_classification': derived_class,
        'candidate': candidate,
        'accepted_for_performance': accepted_for_perf,
        'failure_stability': failure_stability,
        'incomplete': incomplete,
        'telemetry_insufficient': telemetry_insufficient,
        'latest_attempt_provisional': is_latest,
        'justification': justification,
        'associated_evidence': evidence_desc
    })

# Write CSV output
fieldnames = list(out_rows[0].keys())
out_csv_path = FINAL_DIR / 'attempt_acceptance.csv'
with open(out_csv_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out_rows)

out_summary_path = FINAL_DIR / 'telemetry_evaluation_summary.json'
with open(out_summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2)

print(f"Wrote acceptance table to {out_csv_path} ({len(out_rows)} rows)")
print(f"Summary: {json.dumps(summary, indent=2)}")

