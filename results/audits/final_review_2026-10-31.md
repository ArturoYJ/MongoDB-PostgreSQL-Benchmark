# Final pre-matrix audit

Date: 2026-10-31 (America/Mexico_City). Verdict: **NO-GO for unattended full-matrix execution**.

## Scope and evidence rules

Authoritative requirements: `playbook.pdf`, sections 2-8; `playbook_sesion.pdf`, slides 5-13. Repository documents describe intent and are not substitutes for these sources or runtime evidence.

This review inspected the PDFs, current implementation, source dataset, saved pilot CSV/plans, Docker configuration/state, and running telemetry. It reran the 19 unit tests and added isolated diagnostic reproductions. It did **not** start databases, rerun ingestion/preflight against databases, execute new database pilots, or launch the matrix. Previous successful full-document database comparisons are historical evidence, not a fresh live verification in this audit. Production scripts and Compose configuration were not changed.

Status vocabulary: **confirmed** = directly inspected/reproduced; **proposed** = recommended correction/acceptance test; **unknown** = not verified at the relevant workload/profile. A passing smoke pilot is not proof of full-matrix correctness.

## Confirmed checks

- Bash syntax, Python compilation, `docker compose config --quiet`, and all 19 existing unit tests pass. The shell script is executable.
- Source JSONL: 500,000 rows and unique valid UUIDs; 1,500,414 items (mean 3.000828); required nested keys present; `payment.final_total` matches `payment.totals.final_total` in every row checked.
- Source SHA-256: `747396b2705e0ff4f7b842cff00a82eb67dc3682a082e6f171b6475e43f2884d`.
- Q1 and Q2 query files match the supplied PDF workloads. Both Q2 variants are included for each engine.
- Matrix expansion is 54 Q1 runs + 108 Q2 variant runs = 162. Nominal lifecycle alone is 68,040 seconds = 18 h 54 min; cache flushes, preflight, startup, worker draining and plan replays add time. Nineteen hours is not a completion deadline.
- Resource parameter mapping is A=4 CPU/4096m, B=2 CPU/2048m, C=1 CPU/1024m. Stopped database container HostConfig currently shows profile A, equal memory and memory-swap limits, and loopback bindings. Active B/C cgroup enforcement was not exercised.
- Docker VM: ARM64, cgroups v2, 6 CPUs, 5,685,506,048 bytes RAM. Node Exporter reports zero VM swap. Installed images for both databases and all three telemetry services are ARM64.
- All five Compose-published ports bind to `127.0.0.1`; running telemetry bindings agree. This limits exposure, not access by other local users/processes or other Docker containers.
- Prometheus targets are healthy and configured for 1-second scraping. cAdvisor's installed binary defaults to 1-second housekeeping but allows dynamic housekeeping up to 60 seconds; scraping frequency is not a guarantee of equally fresh source samples.
- Saved pilot CSV contains six unique complete 14-column rows at profile A/C=1, with 5/15/3-second phases. Six matching plans exist. PostgreSQL Q1 replay explicitly shows external sorts/temp pages. Both PostgreSQL Q2 replays show top-N heapsort (~273 kB), not spill. MongoDB replays have explicit no-spill evidence.
- Official matrix CSV still contains only its header. Both databases are stopped, telemetry is running, and the orchestration lock is absent.

## Blocking findings

### F1 [P1, confirmed] Evidence paths collide across concurrency levels

`benchmark_runner/__main__.py:83,213` omit concurrency from plan/log filenames. Evaluating the actual plan-path expression over all 162 matrix keys produces only **54 distinct paths**. C=10 and C=50 overwrite evidence for earlier concurrencies with the same engine/profile/query/variant/repetition. Logs have the same identity defect.

**Proposed gate:** include the complete cell key and a session/attempt identity in artifacts; prohibit silent overwrite. Assert 162 unique planned identities and verify each CSV row resolves to its own evidence.

### F2 [P1, confirmed] Invalid runs can be treated as completed/successful

The runner writes `status=error` but returns normally (`benchmark_runner/__main__.py:244-251`). The shell's final message always says all runs completed successfully (`run_benchmarks.sh:305`). Resume only matches the first six CSV fields (`run_benchmarks.sh:160-174`), without checking row integrity, status, artifacts, code/dataset identity or measurement duration. An isolated reproduction shows a row with blank metrics and `error` is accepted as completed.

**Proposed gate:** distinguish a valid observed engine outcome (including expected memory-limit errors/OOM) from an invalid measurement/infrastructure failure. Preserve both as evidence, but do not resume past an invalid/incomplete attempt as though it satisfied the matrix. Validate all keys/rows/artifacts before reporting completion; report counts by outcome. Do not simply rerun every legitimate OOM/error forever or erase them.

### F3 [P1, confirmed] Worker collection can block on queue backpressure

`benchmark_runner/__main__.py:141-159` joins producers before consuming `result_queue`; workers enqueue their complete latency lists. A spawn-based reproduction with a large payload leaves the child alive during `join`, then it exits immediately after the queue is drained. In the current runner this can trigger forced termination, result loss or an unsafe partially written queue. This is a demonstrated mechanism, **not** a claim that a particular database cell has already hit it.

**Proposed gate:** drain results while workers are alive, implement bounded shutdown and preserve error details. Test large payloads, C=50, missing workers and interrupted producers. Python documents this exact queue/join hazard: https://docs.python.org/3.9/library/multiprocessing.html#programming-guidelines

### F4 [P1, confirmed] Throughput, latency and telemetry use inconsistent windows

Workers start their own deadlines and count queries that finish after those deadlines (`workers.py:42-46,96-101`). The parent closes telemetry before joining workers (`__main__.py:138`) and divides all completions by the configured duration (`:223`). A mocked worker counted a 2-second query in a 1-second measurement without error.

**Proposed gate:** define one common monotonic window and the treatment of in-flight queries; count only completions belonging to the chosen throughput window, or explicitly measure a different denominator and matching resource window. Preserve completion timestamps. Test boundary-crossing queries and slow severe-profile workers. Provide bounded query/explain timeouts and reasoned failure classification; current EXPLAIN execution has no explicit execution timeout.

### F5 [P1, confirmed] Spill/OOM attribution is not isolated to the measured workload

`__main__.py:174-211` executes a new single-client EXPLAIN after cooldown and uses its spill flag for the measured concurrent CSV row. Container OOM state is inspected after this extra query. Thus the evidence describes a different execution, and an OOM caused by the replay could be attributed to the earlier measurement. A replay with no spill cannot establish that 50 measured clients had no spill. The corrected parser does not solve this attribution problem.

**Proposed gate:** collect interval-correlated engine spill evidence and OOM events/cgroup counter deltas for the measurement. Keep EXPLAIN as separate supporting evidence with its own timestamps/outcome. Test a workload failure independently from a plan-replay failure. Do not equate absent Docker container death with proof that no backend/process memory failure occurred.

### F6 [P1, confirmed] Telemetry/preflight still accept invalid evidence

`metrics.py:46-54` accepts non-finite instant values. A mocked `NaN` survives and the non-None completeness test allows `status=ok`. `query_range_max` accepts a single finite sample for a 300-second window. `preflight.py:89-102` tests only nonempty results: a mocked NaN series passes. Freshness, source housekeeping, sample coverage and resource enforcement are not verified. PromQL range evaluation can reuse preceding samples; requesting a 1-second step is not proof of 300 fresh measurements (https://prometheus.io/docs/prometheus/latest/querying/basics/#staleness).

**Proposed gate:** reject NaN/Inf and impossible values; define freshness/coverage thresholds against actual sample timestamps; validate counters and resets; check limits/isolation/telemetry for every switched or recreated container. Configure or verify stable cAdvisor source sampling. Keep missing evidence unknown/error, not zero. Test stale, sparse, non-finite, reset and unavailable series.

### F7 [P1, confirmed deviation; resolution unknown] PostgreSQL version does not satisfy literal latest-stable

The PDF requests latest-stable, while `docker-compose.yml` selects `postgres:17`. PostgreSQL 18 was officially released on 2025-09-25, so version 17 cannot be described as the latest stable major release at this audit date: https://www.postgresql.org/about/news/postgresql-18-released-3142/

**Proposed gate:** resolve the course requirement explicitly (use the required stable version with a planned migration/reload, or obtain approval for a documented version exception). Do not point a newer major image at the existing data volume without a migration plan. Record exact versions/image digests and pin the chosen environment before final pilots. No version/image/data-volume changes were performed here.

## Additional conditions and premises to resolve

1. **Preflight is narrower than its wording.** MongoDB comparison ignores index options such as partial filters/collation; PostgreSQL uses names and a loose expression regex, not a complete index/type/validity comparison. Database availability and hashes were checked previously, but no current runtime enforcement of profile B/C or isolation failure injection exists. `switch_engine` ignores failure to stop the other engine (`run_benchmarks.sh:114,119`); it should assert isolation rather than assume it.
2. **Resource/runtime metadata:** snapshot effective `cpu.max`, `memory.max`, `memory.swap.max`, engine version/settings, image digest and container identity per profile/session. Docker's current `MemorySwappiness` inspect field is unset despite the Compose setting; zero VM swap is observed, not proof that every requested kernel interface exists. Do not claim full host-plus-VM swap absence from VM-only telemetry.
3. **Baseline means reference limits, not guaranteed absence of contention.** B/C=1/10/50, sustained 300-second behavior, OOM recovery and failed-telemetry recovery remain untested by the six smoke pilots. VM available memory while databases are stopped does not predict peak workload demand. Host free space was about 39 GiB; VM filesystem reported about 52 GiB. The sparse Docker disk is additionally bounded by host free space. Check both under load.
4. **Q2 does not guarantee spill.** Both variants are required, but `allowDiskUse=true` permits rather than forces spill. Current Q2 replay plans show small top-N sorts. Do not alter the supplied query/dataset merely to fabricate the expected-looking outcome. `work_mem` is per operation and can multiply with parallel workers/concurrency, not a total per-container limit: https://www.postgresql.org/docs/17/runtime-config-resource.html#GUC-WORK-MEM
5. **Timestamp index caveat is real.** The retained text-expression index satisfies the documented extracted-timestamp interpretation but does not match the query's `timestamptz` cast. The saved PostgreSQL Q2 plan confirms a Parallel Seq Scan; the index must not be described as used by that query. Do not change PDF queries or mark an unsafe cast IMMUTABLE merely to force an index scan. Document/clarify the intended comparison.
6. **Cache premise:** dropping Linux page cache is the mandated operation, not an assertion that engine-private caches have also been cleared. Warmup is intentional. Sequential runs retain engine cache state unless the container is recreated; explain replays also perform work. Describe this accurately in the methodology.
7. **M3 and RSS curves:** the CSV preserves a peak and throttling time, not total CPU consumed or a full RSS time series. Define the document-processing denominator (orders/items/qualifying documents) and archive supporting CPU/resource series outside the fixed 14-column CSV. Throttled seconds are not CPU execution seconds. Prometheus retention is only 1 day; export evidence before expiration or select an appropriate retention policy.
8. **Dataset assumptions:** actual completed fraction is 250,307/500,000 = 50.0614%, not the generator comment's 75% (`synthetic_data_generator.py:83-84`). Q2 has 114,676 source candidates. This is not a violation of the PDF, which specifies no completed-status percentage, but it invalidates the comment's selectivity premise. PDF schema lists `payment.totals` while Q2 uses `payment.final_total`; both exist consistently in this dataset.
9. **Loader is not a trustworthy recovery gate:** `load.py` accepts counts above target, Mongo import can append into partial data, and CLI exceptions are printed without a failing exit status. The full matrix does not invoke this loader; its separate preflight mitigates dirty-data execution but does not make the loader correct. Do not use it blindly to repair a database.
10. **Reproducibility and records:** no Python dependency manifest/lockfile was found; several image tags float. Python 3.9's current urllib3 emits a LibreSSL compatibility warning. Store dependency/image manifests and a dataset checksum. Current state documentation previously included stale claims of empty plan/log folders and an unconditional next step to launch. The architecture graph still labels implemented components `proposed`; requirement confirmation is not implementation acceptance.
11. **Results equivalence:** query text and dataset comparability are verified at their stated scope, but this audit did not execute cross-engine result reconciliation. Q1 numeric representation may differ; Q2 ordering ties need a defined comparison policy without silently modifying the source workload.

## Proposed acceptance gate before unattended execution

- Resolve F1-F7 and turn the isolated reproducers into tests that assert corrected behavior.
- Re-run unit/integration tests, syntax/config checks and strict read-only data/index/environment preflight.
- Run fresh Q1 and both Q2 variants for both engines; add bounded C=10/50 tests and representative B/C cases, followed by at least representative full-duration runs. Preserve existing pilot evidence separately.
- Exercise missing telemetry, worker failure, large queues, replay failure and OOM/recovery deliberately in a controlled test, keeping valid engine outcomes distinct from invalid measurement attempts.
- Validate unique cell/attempt identities, CSV schema and row contents, artifact links, exact window agreement, sample coverage and a truthful completion summary.
- Freeze version/dataset/code manifests and define raw telemetry retention/export, resource headroom and unattended sleep/power policy. Only then authorize the full run. No test suite can guarantee the absence of every future failure.

## Reproduction artifacts

`results/audits/final_review_checks.py` uses mocks/temp CSVs and one short synthetic spawn process; it does not access or mutate databases. Its output is `results/audits/final_review_checks.txt`. It is a diagnostic demonstration of current failures, not a passing acceptance suite.

Command from project root: `.venv/bin/python3 results/audits/final_review_checks.py`.
