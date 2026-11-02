"""Generate derived analysis dataset and 54-condition statistical summary."""
import csv, json, statistics
from pathlib import Path
from benchmark_runner.contracts import CANONICAL_COLUMNS

WORKSPACE_ROOT = Path('/Users/arturoyj/Universidad Politecnica/7 cuatrimestre/BDaaS/U2/playbook')
FINAL_DIR = WORKSPACE_ROOT / 'results/final'
ACCEPTANCE_CSV = FINAL_DIR / 'attempt_acceptance.csv'
RAW_CSV = WORKSPACE_ROOT / 'results/metrics.csv'

# Read raw CSV to map line numbers and exact original values
raw_rows = list(csv.DictReader(open(RAW_CSV, 'r', encoding='utf-8')))

# Read acceptance table
attempts = list(csv.DictReader(open(ACCEPTANCE_CSV, 'r', encoding='utf-8')))

# Select 1 attempt per key using deterministic rule: latest attempt
# We verify that exactly 162 keys exist and each has latest_attempt_provisional == 'True'
selected_attempts = [a for a in attempts if a['latest_attempt_provisional'] == 'True']
assert len(selected_attempts) == 162, f"Expected 162 selected attempts, got {len(selected_attempts)}"

# Keep raw observations immutable. Rows whose status came from a sticky Docker
# OOM flag or a preflight plan are non-conclusive for run-level outcome claims.
audit_rows = []
derived_rows = []
for a in selected_attempts:
    raw = raw_rows[int(a['csv_line']) - 2]
    original = a['original_status']
    if original == 'ok':
        derived_status = 'ok'
        include = True
        reason = 'Full-measurement candidate accepted by the archived acceptance audit; raw row retained unchanged.'
        evidence = a['associated_evidence']
    elif original == 'spill':
        derived_status = 'error'
        include = False
        reason = 'Non-conclusive: historical runner OR-ed a preflight plan with runtime counters; no per-attempt spill proof establishes this label.'
        evidence = 'Runner source: benchmark_runner/__main__.py effective_disk_spill = runtime_spill or plan_spill; preflight plan is not run-level proof.'
    elif original == 'oom':
        derived_status = 'error'
        include = False
        reason = 'Non-conclusive: archived status uses container state not scoped to this attempt; available full-window/SIGKILL/timeout evidence does not prove a measurement-window OOM event.'
        evidence = a['derived_classification'] + '; ' + a['associated_evidence']
    else:
        derived_status = 'error'
        include = False
        reason = 'Original attempt was already classified error/incomplete; excluded from performance statistics.'
        evidence = a['derived_classification'] + '; ' + a['justification']

    a['derived_status'] = derived_status
    a['accepted_for_performance'] = 'True' if include else 'False'
    a['derived_classification'] = 'accepted_ok_candidate' if include else 'nonconclusive_or_failed_excluded'
    audit_rows.append({
        'source_csv_line': a['csv_line'], 'key': a['key'], 'engine': a['engine'],
        'profile': a['profile'], 'query_id': a['query_id'], 'concurrency': a['concurrency'],
        'run_number': a['run_number'], 'original_status': original,
        'derived_status': derived_status, 'include_in_performance': str(include),
        'decision_reason': reason, 'evidence': evidence,
    })
    out = {k: raw[k] for k in CANONICAL_COLUMNS}
    out['status'] = derived_status
    if not include:
        for k in CANONICAL_COLUMNS[6:13]:
            out[k] = ''
    derived_rows.append(out)

crosswalk = FINAL_DIR / 'status_adjudication_162.csv'
with crosswalk.open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(audit_rows[0]))
    w.writeheader(); w.writerows(audit_rows)
derived_metrics = FINAL_DIR / 'metrics_selected_derived.csv'
with derived_metrics.open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=CANONICAL_COLUMNS)
    w.writeheader(); w.writerows(derived_rows)
print(f'Wrote auditable status crosswalk ({len(audit_rows)} rows) and 14-column derived CSV; raw CSV untouched.')

# Save selected attempts dataset
selected_csv_path = FINAL_DIR / 'derived_selected_attempts.csv'
fieldnames = list(selected_attempts[0].keys())
with open(selected_csv_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(selected_attempts)
print(f"Wrote {len(selected_attempts)} selected attempts to {selected_csv_path}")

# Group by 54 conditions: (engine, profile, query_id, concurrency)
conditions = {}
for a in selected_attempts:
    c_key = (a['engine'], a['profile'], a['query_id'], int(a['concurrency']))
    if c_key not in conditions:
        conditions[c_key] = []
    conditions[c_key].append(a)

assert len(conditions) == 54, f"Expected 54 conditions, got {len(conditions)}"

condition_rows = []
for (engine, profile, query_id, concurrency), reps in sorted(conditions.items()):
    assert len(reps) == 3, f"Condition {(engine, profile, query_id, concurrency)} has {len(reps)} reps instead of 3"
    
    # Repetition breakdown
    valid_perf_reps = [r for r in reps if r['accepted_for_performance'] == 'True']
    n_valid = len(valid_perf_reps)
    n_failures = 3 - n_valid
    
    # Classifications and statuses
    statuses = [r['original_status'] for r in reps]
    derived_classes = [r['derived_classification'] for r in reps]
    status_summary = f"{statuses.count('ok')} ok, {statuses.count('spill')} spill, {statuses.count('oom')} oom, {statuses.count('error')} error"
    class_summary = "; ".join(sorted(set(derived_classes)))
    
    # Statistical aggregation
    def compute_stats(vals):
        if not vals:
            return {'median': 'ND', 'min': 'ND', 'max': 'ND', 'range': 'ND'}
        med = round(statistics.median(vals), 2)
        v_min = round(min(vals), 2)
        v_max = round(max(vals), 2)
        v_range = round(v_max - v_min, 2)
        return {'median': med, 'min': v_min, 'max': v_max, 'range': v_range}
    
    if n_valid > 0:
        qps_vals = [float(r['associated_evidence'].split('qps=')[1].split(',')[0]) if 'qps=' in r['associated_evidence'] else float(raw_rows[int(r['csv_line'])-2]['throughput_qps']) for r in valid_perf_reps]
        p50_vals = [float(raw_rows[int(r['csv_line'])-2]['latency_p50_ms']) for r in valid_perf_reps]
        p95_vals = [float(raw_rows[int(r['csv_line'])-2]['latency_p95_ms']) for r in valid_perf_reps]
        p99_vals = [float(raw_rows[int(r['csv_line'])-2]['latency_p99_ms']) for r in valid_perf_reps]
        cfs_vals = [float(r['prom_cfs_throttled_sec']) for r in valid_perf_reps]
        rss_vals = [float(r['prom_max_rss_mb']) if r['prom_max_rss_mb'] != 'ND' else float(r['csv_max_rss_mb']) for r in valid_perf_reps]
        cpu_sec_vals = [float(r['prom_cpu_usage_sec']) for r in valid_perf_reps]
        
        qps_stats = compute_stats(qps_vals)
        p50_stats = compute_stats(p50_vals)
        p95_stats = compute_stats(p95_vals)
        p99_stats = compute_stats(p99_vals)
        cfs_stats = compute_stats(cfs_vals)
        rss_stats = compute_stats(rss_vals)
        cpu_stats = compute_stats(cpu_sec_vals)
        outcome_category = "performance_usable" if n_valid == 3 else "partial_performance"
    else:
        qps_stats = p50_stats = p95_stats = p99_stats = cfs_stats = rss_stats = cpu_stats = compute_stats([])
        outcome_category = "stability_failure"

    condition_rows.append({
        'engine': engine,
        'profile': profile,
        'query_id': query_id,
        'concurrency': concurrency,
        'n_total': 3,
        'n_valid_perf': n_valid,
        'n_failures': n_failures,
        'outcome_category': outcome_category,
        'original_statuses': status_summary,
        'derived_classifications': class_summary,
        'throughput_qps_median': qps_stats['median'],
        'throughput_qps_min': qps_stats['min'],
        'throughput_qps_max': qps_stats['max'],
        'latency_p50_ms_median': p50_stats['median'],
        'latency_p50_ms_min': p50_stats['min'],
        'latency_p50_ms_max': p50_stats['max'],
        'latency_p95_ms_median': p95_stats['median'],
        'latency_p95_ms_min': p95_stats['min'],
        'latency_p95_ms_max': p95_stats['max'],
        'latency_p99_ms_median': p99_stats['median'],
        'latency_p99_ms_min': p99_stats['min'],
        'latency_p99_ms_max': p99_stats['max'],
        'cfs_throttled_sec_median': cfs_stats['median'],
        'cfs_throttled_sec_min': cfs_stats['min'],
        'cfs_throttled_sec_max': cfs_stats['max'],
        'max_rss_mb_median': rss_stats['median'],
        'max_rss_mb_min': rss_stats['min'],
        'max_rss_mb_max': rss_stats['max'],
        'cpu_usage_sec_median': cpu_stats['median'],
        'notes': reps[0]['justification'] if n_valid == 0 else ("Suspicious OOM inherited label" if "persistent_oom" in class_summary else "Verified full measurement")
    })

# Write condition summary CSV
cond_csv_path = FINAL_DIR / 'condition_summary_54.csv'
cond_fieldnames = list(condition_rows[0].keys())
with open(cond_csv_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=cond_fieldnames)
    writer.writeheader()
    writer.writerows(condition_rows)

print(f"Wrote 54 conditions summary to {cond_csv_path}")

# Write JSON
cond_json_path = FINAL_DIR / 'condition_summary_54.json'
with open(cond_json_path, 'w', encoding='utf-8') as f:
    json.dump(condition_rows, f, indent=2)

# Quick sanity counts
outcome_counts = {}
for r in condition_rows:
    outcome_counts[r['outcome_category']] = outcome_counts.get(r['outcome_category'], 0) + 1
print(f"54 Conditions outcome breakdown: {outcome_counts}")

