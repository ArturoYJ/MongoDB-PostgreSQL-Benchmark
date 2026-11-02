# Terminal-log reconciliation and failure diagnosis

Date: 2026-11-01. Scope: read-only diagnosis and derived evidence labels; no code repair, status overwrite, database mutation or rerun.

## Reconciliation result

`benchmark.log` contains **198 benchmark attempts**. Every recorded dictionary matches its corresponding original CSV row by timestamp and all 14 values: **198 matches, zero unmatched attempts, zero value mismatches**. The final success message establishes that the shell reached its last line, not that every measurement met the protocol.

The source CSV still has 162 distinct expected keys. The table below describes the **latest attempt per key**, a provisional descriptive view rather than a selection policy approved for final statistical analysis.

| Derived evidence class | Latest keys | All attempts | Interpretation |
|---|---:|---:|---|
| Full measurement; original `ok`/`spill` | 108 | 108 | Performance candidates, still subject to telemetry/method validation |
| Full measurement; persistent OOM label suspected | 30 | 30 | Approximately 300 seconds reported, no worker failure reported in that attempt, no matching SIGKILL in retained logs; do not count as fresh OOM incidents |
| Timeout interrupted measurement | 15 | 39 | Not comparable to an uninterrupted 300-second measurement |
| Forced worker termination / partial results | 3 | 3 | Parent killed workers after drain deadline; missing output files make aggregate metrics incomplete |
| Warmup failure with timestamp-matched PostgreSQL SIGKILL | 6 | 18 | Stability failure consistent with OOM; no valid measured latency sample |
| **Total** | **162** | **198** | Retries must not become extra protocol repetitions |

There are therefore **138 candidates for further performance validation**, not 138 already-certified rows. Six warmup failures remain useful failure/stability evidence. The other 18 keys have truncated or incomplete measurements and need a targeted methodological decision before reuse or rerun.

## Finding 1 — Confirmed CRLF bug in resume/retry parsing

The CSV uses CRLF line endings. `run_benchmarks.sh:134-151` obtains field 14 through `grep` and `awk`, retaining its trailing carriage return. The status is actually `oom\r` or `error\r`; Bash's command substitution removes the linefeed but not the carriage return.

Reproduction against the real CSV:

- Timestamp 1793575333: status bytes `6f 6f 6d 0d`, shell representation `$'oom\r'`. This is not equal to literal `oom`, so the zero-throughput OOM attempt is wrongly retried.
- Timestamp 1793541504: a positive-throughput `error\r` fails the literal `error` comparison and is accepted by the current predicate.

This explains the contradictory terminal warning "0.0 throughput without OOM" on a row displaying `oom`, and why partial positive-throughput errors can be called validated. The extra attempts are retained; they are not automatically additional experimental repetitions. Proof: `crlf_reproduction.txt`. **Not fixed in this diagnosis.** The correction should use a CSV-aware parser or correctly normalize/validate fields, with tests for all four canonical statuses and CRLF/LF.

## Finding 2 — OOM label is not interval-scoped

Current `observe.py:170-178` reads Docker's persistent `State.OOMKilled`. Current PostgreSQL was observed running with that flag true. The main classifier gives this flag priority over worker errors.

The 42 latest rows labeled `oom` split into:

- **6 Q1 warmup-failure keys**, profiles B/C, concurrency 50. Retained PostgreSQL logs contain 18 distinct timestamp-matched SIGKILL events across their retries and corresponding connection loss/recovery. This supports real process termination and is consistent with memory pressure. SIGKILL alone does not independently prove the kernel's reason; do not label it definitive kernel OOM without corresponding event/cgroup/kernel evidence.
- **30 Q2 keys**: all profile B concurrencies/variants and profile C concurrencies 1/10, both variants, three repetitions. Terminal progress reaches approximately 300 seconds with no reported worker failure in those attempts. Their OOM flag is consistent with inherited state rather than a new event. Preserve their metrics as candidates; do not blindly discard them or silently change the original status.
- **6 Q2 keys**, profile C/concurrency 50, both variants: timeout failures are visible in the terminal, but the persistent OOM flag overrides the error classification. These are not six newly demonstrated OOM kills.

`sigkill_events.json` de-duplicates copied log messages and retains file/line sources. Correlation windows use console progress and CSV start timestamps, so bounds are approximate; absence from retained logs is not proof of absence from the kernel.

## Finding 3 — Timeouts truncate measurement and reveal a missing readiness barrier

PostgreSQL workers set `statement_timeout=120000`; MongoDB sets `socketTimeoutMS=120000`. The parent starts measurement after 60 seconds of wall time without waiting for every worker to finish its current warmup query. A worker may still be executing a warmup query when the parent already prints `[MEASUREMENT]`.

This explains "warmup failure" messages appearing about 60 seconds into measurement: a query that started during warmup reaches the 120-second timeout, sets the shared error event and stops the parent's measurement loop. Such a row does not represent 300 seconds at the declared measured concurrency.

Latest affected keys:

- MongoDB Q1, profile C, concurrency 50: 3.
- PostgreSQL Q1, profile A, concurrency 50: 3.
- PostgreSQL Q1, profile C, concurrency 10: 3.
- PostgreSQL Q2, profile C, concurrency 50, both variants: 6 (originally labeled OOM).

Timeout failure is not synonymous with OOM. A corrected rerun protocol needs an explicit readiness barrier, bounded and documented query/drain policy, actual interval metadata, and separate infrastructure versus observed-engine outcomes. Do not arbitrarily increase timeouts or change PDF queries just to obtain successful-looking rows.

## Finding 4 — Three MongoDB rows have missing worker output

These reach approximately 300 seconds but fail afterward:

- Profile C/Q2/allowDiskUse=false/concurrency 50, repetitions 1 and 2.
- Profile C/Q2/allowDiskUse=true/concurrency 50, repetition 1.

The terminal explicitly reports forced worker termination after the collective 90-second join deadline and missing `/tmp/benchmark_worker_*.json` files. Other workers' samples are still aggregated, producing positive QPS/latency from an incomplete worker set. These are not clean performance measurements. The CRLF predicate can then accept them because throughput is positive. The parent's 90-second drain budget is also shorter than the configured 120-second socket timeout; query completion and output persistence are not guaranteed before termination.

## Finding 5 — Zeros and low RSS after a warmup crash are not valid performance observations

The six Q1 warmup-failure keys do not enter a substantive measurement loop. `calculate_latencies([])` returns zeros, and throughput is computed with a minimum one-second denominator. Thus P95/P99=0 means **no measured latency sample**, not instantaneous execution. A 5 MB RSS value collected after process failure/recovery is not the peak memory that caused the failure.

Keep original values as evidence. In a derived analysis dataset, mark these performance fields unavailable with an explicit reason rather than rank them as fast/low-memory executions. Retain their failure outcome separately.

## Recommended next gate

1. Accept this terminal/CSV reconciliation as evidence inventory, not final metric certification.
2. Validate archived telemetry/window provenance for the 138 candidate rows, prioritizing the 30 suspicious OOM labels. Preserve raw and interpreted statuses in separate fields/files.
3. Define a transparent attempt-selection policy and document all excluded/censored metrics. No "best run" selection.
4. Before targeted reruns, correct and regression-test CRLF parsing, readiness/window accounting, timeout/drain handling, missing-worker aggregation, OOM interval attribution and telemetry fallback behavior. No repair was performed in this turn.
5. Consider rerunning only the 18 incomplete/partial keys if full-duration performance is needed and the corrected protocol can measure them. Retain genuine resource-failure outcomes; do not require every scenario to succeed. Exact rerun scope remains subject to telemetry validation and approval.
6. Only then aggregate valid protocol repetitions and generate graphs/report. Do not restart the entire 19-hour matrix by default.

## Reproducibility / evidence

- Original log archived as `benchmark.log.gz`; SHA-256 recorded in `reconciliation_summary.json`.
- `reconcile_log.py`: parses terminal attempts, matches all CSV values, correlates retained SIGKILL events and writes derived labels.
- `attempt_reconciliation.csv`: all 198 attempts, original CSV line, terminal line references, progress/failure indicators and classification.
- `latest_attempt_review.csv`: latest-attempt descriptive view (162 keys); not an approved cleaned dataset.
- `sigkill_events.json`: 18 distinct process-termination log messages, timestamps and provenance.
- `crlf_reproduction.txt`: real-file shell reproduction of OOM retry and positive-throughput error acceptance.

The unchanged original CSV, plans/logs and production code remain the primary evidence. Derived labels never overwrite them.
