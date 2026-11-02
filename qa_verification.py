"""Evidence-aware QA for the BDaaS delivery; does not certify an unrerun matrix."""
import csv
import hashlib
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent
FINAL = WORKSPACE / 'results/final'
EXPECTED_SHA = 'f3139481670f04f4387030ed929fcbf39f15278b6bd1e440c9b11321b9dca1f3'
EXPECTED_HEADER = ['timestamp_epoch','engine','profile','query_id','concurrency','run_number','throughput_qps','latency_p50_ms','latency_p95_ms','latency_p99_ms','cpu_throttled_seconds','max_rss_mb','disk_spill','status']

raw = WORKSPACE / 'results/metrics.csv'
assert hashlib.sha256(raw.read_bytes()).hexdigest() == EXPECTED_SHA
raw_rows = list(csv.DictReader(raw.open(encoding='utf-8')))
assert len(raw_rows) == 198 and list(raw_rows[0]) == EXPECTED_HEADER
with (WORKSPACE / 'data/orders.jsonl').open(encoding='utf-8') as dataset:
    assert sum(1 for _ in dataset) == 500_000
subprocess.run(
    [sys.executable, str(WORKSPACE / 'dataset_manifest.py'), '--check'],
    cwd=WORKSPACE,
    check=True,
)
print('PASS: raw CSV hash/schema/198 rows and 500,000-record dataset')

selected = list(csv.DictReader((FINAL / 'derived_selected_attempts.csv').open(encoding='utf-8')))
assert len(selected) == 162
keys = {(r['engine'],r['profile'],r['query_id'],r['concurrency'],r['run_number']) for r in selected}
assert len(keys) == 162
summary = list(csv.DictReader((FINAL / 'condition_summary_54.csv').open(encoding='utf-8')))
assert len(summary) == 54 and sum(int(r['n_total']) for r in summary) == 162
valid = sum(int(r['n_valid_perf']) for r in summary)
assert valid == 93 and sum(int(r['n_failures']) for r in summary) == 69
print('PASS: 162 unique selected attempts; 54 conditions; 93 admitted candidate rows and 69 excluded')

metrics = list(csv.DictReader((FINAL / 'metrics_selected_derived.csv').open(encoding='utf-8')))
assert len(metrics) == 162 and list(metrics[0]) == EXPECTED_HEADER
assert {r['status'] for r in metrics} <= {'ok','spill','oom','error'}
audit = list(csv.DictReader((FINAL / 'status_adjudication_162.csv').open(encoding='utf-8')))
assert len(audit) == 162 and len({r['key'] for r in audit}) == 162
assert all(r['derived_status'] in {'ok','spill','oom','error'} for r in audit)
assert sum(r['include_in_performance'] == 'True' for r in audit) == 93
assert all(r['status'] != 'oom' for r in metrics)
print('PASS: derived 14-column status contract and 162-row evidence crosswalk')

figures = ['fig1_p99_latency_vs_concurrency.png','fig2_throughput_vs_concurrency.png','fig3_rss_memory_vs_concurrency.png','fig4_cfs_throttling_vs_throughput.png','fig5_state_matrix_heatmap.png']
for name in figures:
    p = FINAL / 'figures' / name
    assert p.exists() and p.stat().st_size > 10_000, f'Missing/invalid figure {name}'
fig_source = (WORKSPACE / 'generate_figures.py').read_text(encoding='utf-8')
fig4 = fig_source.split('# Figure 4:',1)[1].split('# Figure 5:',1)[0]
assert "set_xlabel('Throughput QPS (median)')" in fig4 and 'cfs_throttled_sec_median' in fig4
print('PASS: five figures exist; Figure 4 maps QPS to CFS')

compose = (WORKSPACE / 'docker-compose.yml').read_text(encoding='utf-8')
manifest = (WORKSPACE / 'results/audits/image_manifest.json').read_text(encoding='utf-8')
assert ':latest' not in compose and compose.count('@sha256:') == 5
assert 'MongoDB 9.0.4' in manifest or 'mongo@sha256:452bdaf2' in compose
report_path = FINAL / 'report_bdaas_contention.typ'
pdf = FINAL / 'report_bdaas_contention.pdf'
assert report_path.exists() == pdf.exists(), 'Report source and PDF must be present or absent together'
if report_path.exists():
    report = report_path.read_text(encoding='utf-8')
    for phrase in ['MongoDB 9.0.4','PostgreSQL 18.6','P50 / P95 / P99','status_adjudication_162.csv','Parallel Seq Scan','no se afirma cumplimiento empírico del 100%']:
        assert phrase in report, f'Missing report requirement/evidence: {phrase}'
    figure4_start = report.index('== Figura 4.')
    figure5_start = report.index('== Figura 5.', figure4_start)
    assert 'timestamptz' in report and 'QPS' in report[figure4_start:figure5_start]
    assert len(re.findall(r'\[\*[1-6]\. ¿', report)) == 6
    for i in range(1,8):
        assert re.search(r'^= '+str(i)+r'\.', report, re.M), f'Missing report section {i}'
    for pattern in [r'\bTODO\b',r'\bFIXME\b',r'\bTBD\b']:
        assert not re.search(pattern, report)
    assert pdf.stat().st_size > 50_000
    assert not (FINAL / 'report_bdaas_contention_humanizado.typ').exists()
    assert not (FINAL / 'report_bdaas_contention_humanizado.pdf').exists()
    info = subprocess.run(['pdfinfo', str(pdf)], check=True, capture_output=True, text=True).stdout
    pages = int(re.search(r'^Pages:\s+(\d+)', info, re.M).group(1))
    assert pages == 18
    pdf_text = subprocess.run(['pdftotext', '-layout', str(pdf), '-'], check=True, capture_output=True, text=True).stdout
    pdf_text = pdf_text.replace('\u00ad', '')
    pdf_text = re.sub(r'(?<=\w)-\s+(?=\w)', '', pdf_text)
    pdf_text = re.sub(r'Mon\s+goDB', 'MongoDB', pdf_text)
    pdf_text = re.sub(r'\s+', ' ', pdf_text)
    for phrase in ['MongoDB 9.0.4','P50 / P95 / P99','PostgreSQL 18.6','status_adjudication_162.csv']:
        assert phrase in pdf_text, f'Missing exported PDF text: {phrase}'
    print('PASS: pinned images; local report has seven sections, six reviewer answers, and passes PDF text/page checks')
else:
    print('SKIP: separate academic report files are not present in this checkout')
print('QA PASSED. Note: this verifies artifacts, not empirical reproducibility of the unrerun matrix.')
