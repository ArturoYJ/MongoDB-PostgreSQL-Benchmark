# Post-run acceptance and analysis plan

Date: 2026-11-01. Status: **execution coverage confirmed; scientific acceptance pending**.

## 1. Confirmed initial census

The user reports the full matrix finished. Current files confirm:

- Exact 14-column CSV header; 198 rows representing all 162 expected engine/profile/query-variant/concurrency/repetition keys. No missing or unexpected keys.
- 19 keys have repeated attempts, producing 36 extra rows. These are not additional independent repetitions.
- All 162 expected plan paths exist and are readable. Their filenames separate concurrency but not retry attempts; a present plan cannot automatically be attributed to every previous attempt for that key.
- No malformed rows, blank/nonfinite numeric fields, or negative numeric fields were found by the initial census. This is structural validation, not proof that the numeric values are scientifically valid.
- Last-attempt-only descriptive counts: 93 `ok`, 15 `spill`, 42 `oom`, 12 `error`. This is a provisional view, not an approved selection policy or corrected dataset.
- MongoDB last-attempt view: 75 `ok`, 6 `error`. PostgreSQL: 18 `ok`, 15 `spill`, 42 `oom`, 6 `error`.
- All-attempt counts: 93 `ok`, 15 `spill`, 58 `oom`, 32 `error`.
- CSV measurement-start timestamps span 2026-10-31 23:10:45 to 2026-11-01 19:22:08 in America/Mexico_City. The last timestamp is a measurement start, not the final shutdown time.
- No active benchmark runner/orchestrator process was found. PostgreSQL and telemetry remain running; MongoDB is stopped. Nothing was stopped or restarted by this review.

## 2. Preservation already completed

- `evidence_snapshot.tgz`: original CSV, plans/logs, current runner, shell/Compose/Prometheus configuration, queries, previous agent state and dependency lock.
- `sha256_manifest.json`: hashes of current CSV, plans/logs and implementation.
- `census.json`: machine-readable initial validation results.
- `attempt_catalog.csv`: all original rows with original line numbers, attempt ordinals, provisional latest flags and expected plan paths. Original CSV is unchanged; no rows were discarded.
- `inspect_results.py`: reproducible read-only census logic.
- `export_telemetry.py`, `*.json.gz`, `telemetry_export_summary.json`: exported available raw Prometheus samples for RSS, CPU usage/throttling, last-seen/up, host memory/swap and disk I/O. Includes source sample timestamps rather than only a peak.
- `prometheus_config.json`, `prometheus_flags.json`, `prometheus_targets.json`, `database_container_state.json`: current operating evidence. Environment secrets are excluded from container snapshots.

Prometheus is currently configured with **1-day retention**, so the raw export was prioritized. Available raw samples were saved across the run's time range; matching container IDs and checking per-cell coverage still remain. The export is not a claim of complete valid telemetry for every attempt.

## 3. Priority acceptance issues (do this before graphs or performance conclusions)

### A. Distinguish actual OOM events from persistent state

Current PostgreSQL has `Running=true` and `OOMKilled=true`. Current `observe.py:170-178` reads that state; it does not establish a new OOM event inside each CSV row's measurement window. Thus 42 labels cannot yet be reported as 42 independent OOM incidents. Review timestamped logs/events/cgroup evidence and container lifecycle mapping. Do not automatically relabel rows as healthy just because the container is running.

### B. Diagnose the 12 latest error rows

| Engine | Profile | Query | Concurrency | Latest error rows |
|---|---|---|---:|---:|
| MongoDB | C | Q1 | 50 | 3 |
| MongoDB | C | Q2 allowDiskUse=false | 50 | 2 |
| MongoDB | C | Q2 allowDiskUse=true | 50 | 1 |
| PostgreSQL | A | Q1 | 50 | 3 |
| PostgreSQL | C | Q1 | 10 | 3 |

Sample retained PostgreSQL logs contain `canceling statement due to statement timeout`; MongoDB logs include ClientDisconnect/interrupted operations. These are observations from samples, not a complete root-cause classification. The complete terminal transcript was requested from the user because worker errors and phase timings may not be retained in Docker logs.

### C. Verify measurement provenance against the actual execution version

The current runner/configuration differs from the earlier receipt-based implementation retained in debug/pilot evidence. Do not use those earlier passing tests or pilot receipts to certify this full matrix. The current code captures a plan before warmup, combines runtime counters with plan spill, samples counters across warmup/drain, provides current-value telemetry fallbacks, and accepts 35% RSS sample density. Missing engine spill counters can become zero. These limitations require evaluation against the archived evidence; do not silently rewrite the existing implementation or invalidate the whole dataset without examining recoverability.

The full matrix has no matching `metrics.evidence/` receipt tree. Current code was snapshotted, but an immutable code fingerprint at each historical attempt is unavailable. Previous state documentation's "100% verified" statement is superseded by this scoped post-run assessment.

### D. Define retry selection and evidence linkage

Keep every attempt in the raw archive. Determine why each retry happened and whether the final attempt is usable before selecting one per key. Do not choose the fastest/best run. Record decisions with original CSV line numbers, reason and linked evidence. Plans are reused per key across retries; Q2 diagnostic log filenames also omit the variant. Where exact association is unrecoverable, mark the limitation rather than inventing it.

Only request targeted reruns if necessary evidence cannot be recovered. Do not repeat the entire 19-hour matrix by default.

## 4. Proposed staged work, with explicit completion gates

### Stage 1 — Evidence reviewer (next active stage)

Inputs: raw CSV/catalog, retained logs/plans, exported telemetry, terminal transcript if available.

Deliverables: per-attempt quality/outcome classification; OOM/time-window attribution; retry selection log; list of irrecoverable cells (if any). Separate valid engine failure outcomes from instrumentation/timeout/infrastructure failures. Acceptance: every selected row has a documented rationale and failures remain visible.

### Stage 2 — Data analyst

Create a derived analysis dataset, never edit the original CSV. Group by engine, profile, query/variant and concurrency: 54 conditions with up to 3 protocol repetitions each. Report `n` of valid performance repetitions and outcomes separately. Use medians and min/max or other explicitly justified dispersion across repetitions. Do not convert missing data into zeros. Do not pool retries as repetitions.

A median of three per-run P99 values is **not** the pooled P99 of all queries. Raw worker latencies were deleted by the current runner, so do not claim a pooled percentile without recoverable observations. Treat valid OOM/failure outcomes as stability results, not ordinary successful throughput/latency points.

### Stage 3 — Visualization and technical interpretation

Produce P99 versus concurrency, throttling versus QPS, and RSS curves from preserved telemetry; retain query/variant/profile separation. Add an outcome/failure matrix and show incomplete conditions explicitly. Cross-check proposed bottlenecks with plans and measured evidence. For M3, define the document-processing denominator and distinguish CPU consumed from CFS throttled seconds.

### Stage 4 — Report author

Follow the seven PDF-required sections: host/Docker specifications; method and baseline; consolidated results; graphical analysis; execution plans; memory/spill/throttling interpretation; deployment recommendations. Explain versions, macOS/Linux-VM boundary, retries, sampling limits, valid/invalid outcomes and any unavailable evidence. Recommendations must follow observed data, not expected-looking results.

### Stage 5 — Independent final review

Trace each table/figure/conclusion to selected rows and evidence; verify the exact CSV contract, variant coverage, repetitions and limitations. Confirm reproducibility instructions and package the raw archive separately from derived analysis/report artifacts.

The roles above describe a proposed agentic workflow, not claims that parallel agents have already completed those tasks. Maintain `Agents/state/current.md` and append `Agents/history/2026-11.md` at each approved stage.

## Scope of this turn

Completed preservation and initial census, not full statistical analysis or final acceptance. No benchmark implementation changes, database writes, reruns, service shutdowns, final plots or academic report were performed.
