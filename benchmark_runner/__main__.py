"""
Benchmark Load Harness & Execution Orchestrator
===============================================
Course: Bases de Datos en la Nube (IT2iD - 2026)
Reference: playbook.pdf (Section 6, 7, 8) & playbook_sesion.pdf (Slide 8-13)

Resolves:
- Bloqueo 1: Generates 162 distinct plan paths including concurrency tag (_c{C}_run{N}).
- Bloqueo 3: Reads worker outputs from temporary files avoiding multiprocessing.Queue deadlocks.
- Bloqueo 4: Strictly filters queries whose start and end fell inside [meas_start, meas_end].
- Bloqueo 5: Observes runtime temp file/spill deltas from database engines during load.
"""

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from typing import List, Dict, Any, Tuple

from benchmark_runner.metrics import (
    PrometheusCollector,
    calculate_latencies,
    record_metrics_row
)
from benchmark_runner.observe import (
    capture_mongodb_plan,
    capture_postgresql_plan,
    get_mongodb_spill_stats,
    get_postgresql_temp_stats,
    check_container_oom,
    save_container_diagnostic_log
)
from benchmark_runner.workers import (
    get_worker_result_file,
    run_mongodb_worker,
    run_postgresql_worker
)
from benchmark_runner.status import classify_status


def load_query_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        if path.endswith(".json"):
            return json.load(f)
        return f.read().strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-engine Contention Benchmark Runner")
    parser.add_argument("--engine", choices=["mongodb", "postgresql"], required=True)
    parser.add_argument("--profile", required=True, help="e.g. PROFILE_A_BASELINE, PROFILE_B_MEDIUM, PROFILE_C_SEVERE")
    parser.add_argument("--query", choices=["Q1", "Q2"], required=True)
    parser.add_argument("--concurrency", type=int, choices=[1, 10, 50], default=1)
    parser.add_argument("--run-number", type=int, default=1)
    parser.add_argument("--variant", type=str, default="default", help="e.g. allowDiskUse_false, work_mem_4MB")
    parser.add_argument("--warmup", type=float, default=60.0, help="Warmup duration in seconds")
    parser.add_argument("--duration", type=float, default=300.0, help="Measurement duration in seconds")
    parser.add_argument("--cooldown", type=float, default=60.0, help="Cooldown duration in seconds")
    parser.add_argument("--output-csv", type=str, default="results/metrics.csv")
    parser.add_argument("--prometheus-url", type=str, default="http://127.0.0.1:9090")
    parser.add_argument("--pg-port", type=int, default=5434, help="PostgreSQL Docker published port")

    args = parser.parse_args()

    container_name = f"benchmark_{args.engine}"
    print(f"\n{'='*70}")
    print(f"STARTING BENCHMARK CELL: {args.engine.upper()} | {args.profile} | {args.query} | Concurrency: {args.concurrency} | Run: {args.run_number} | Variant: {args.variant}")
    print(f"Warmup: {args.warmup}s | Measurement: {args.duration}s | Cooldown: {args.cooldown}s")
    print(f"{'='*70}\n")

    # Load query definition
    query_file = f"queries/{args.engine}/{args.query}.{'json' if args.engine == 'mongodb' else 'sql'}"
    query_data = load_query_file(query_file)

    # Resolve variant flags
    allow_disk_use = (args.variant == "allowDiskUse_true")
    work_mem = "64MB" if args.variant == "work_mem_64MB" else "4MB"

    # Capture execution plan with distinct path per concurrency (Bloqueo 1)
    plan_out = f"results/plans/{args.engine}_{args.profile}_{args.query}_{args.variant}_c{args.concurrency}_run{args.run_number}.json"
    plan_spill = False
    try:
        if args.engine == "mongodb":
            _, plan_spill = capture_mongodb_plan(
                mongo_uri="mongodb://127.0.0.1:27017",
                pipeline=query_data,
                output_path=plan_out,
                allow_disk_use=allow_disk_use
            )
        else:
            _, plan_spill = capture_postgresql_plan(
                pg_host="127.0.0.1",
                pg_port=args.pg_port,
                pg_db="benchmark",
                pg_user="postgres",
                pg_pass="postgres",
                sql_query=query_data,
                output_path=plan_out.replace(".json", ".txt"),
                work_mem=work_mem
            )
    except Exception as e:
        print(f"Plan capture warning/failure: {e}")

    # Launch concurrency workers with synchronized event bus
    measuring_event = mp.Event()
    stop_event = mp.Event()
    error_event = mp.Event()
    workers: List[mp.Process] = []

    for i in range(args.concurrency):
        if args.engine == "mongodb":
            p = mp.Process(
                target=run_mongodb_worker,
                args=(i, query_data, allow_disk_use, measuring_event, stop_event, error_event)
            )
        else:
            p = mp.Process(
                target=run_postgresql_worker,
                args=(i, query_data, work_mem, measuring_event, stop_event, error_event, args.pg_port)
            )
        p.start()
        workers.append(p)

    # Phase 1: Warmup monitoring
    t_start_all = time.time()
    t_meas_start = t_start_all + args.warmup

    while time.time() < t_meas_start and not error_event.is_set():
        w_elapsed = time.time() - t_start_all
        alive_count = sum(1 for p in workers if p.is_alive())
        print(f"\r[WARMUP] {w_elapsed:.1f}s / {args.warmup:.1f}s | Active workers: {alive_count}", end="", flush=True)
        time.sleep(1.0)

    # Phase 2: Active Measurement Window (Strict Temporal Boundary, Bloqueo 4)
    # Snapshot runtime spill counters at the measurement boundary; preflight plans
    # are explanatory evidence only and must not classify an individual run.
    pg_start_stats = get_postgresql_temp_stats(pg_port=args.pg_port) if args.engine == "postgresql" else None
    mongo_start_spills = get_mongodb_spill_stats() if args.engine == "mongodb" else None
    measuring_event.set()
    start_epoch = time.time()
    t_meas_end = start_epoch + args.duration

    while time.time() < t_meas_end and not error_event.is_set():
        m_elapsed = time.time() - start_epoch
        alive_count = sum(1 for p in workers if p.is_alive())
        print(f"\r[MEASUREMENT] {m_elapsed:.1f}s / {args.duration:.1f}s | Active workers: {alive_count}", end="", flush=True)
        time.sleep(1.0)

    end_epoch = time.time()
    print()

    pg_end_stats = get_postgresql_temp_stats(pg_port=args.pg_port) if args.engine == "postgresql" else None
    mongo_end_spills = get_mongodb_spill_stats() if args.engine == "mongodb" else None

    # Signal all workers to conclude in-flight operations and exit cleanly
    stop_event.set()

    # Wait for workers with collective deadline and inspect exit codes (Bloqueo 3)
    worker_error = False
    join_deadline = time.time() + 90.0
    for p in workers:
        time_left = max(1.0, join_deadline - time.time())
        p.join(timeout=time_left)
        if p.is_alive():
            print(f"Warning: Worker PID {p.pid} did not exit within deadline. Terminating...")
            p.terminate()
            p.join(timeout=2.0)
            worker_error = True
        elif p.exitcode != 0:
            print(f"Worker PID {p.pid} exited with non-zero code {p.exitcode}")
            worker_error = True

    # Cooldown phase
    if args.cooldown > 0:
        print(f"Entering cooldown phase ({args.cooldown}s)...")
        time.sleep(args.cooldown)

    # Collect query latencies from worker files, filtering to strict measurement window (Bloqueo 3 & 4)
    all_latencies: List[float] = []
    worker_files_found = 0

    for i in range(args.concurrency):
        w_file = get_worker_result_file(i)
        if os.path.exists(w_file):
            worker_files_found += 1
            try:
                with open(w_file, "r", encoding="utf-8") as f:
                    w_data = json.load(f)
                    if w_data.get("error"):
                        worker_error = True
                    for q_start, q_end, lat in w_data.get("queries", []):
                        # Keep only queries strictly bounded inside the active measurement window
                        if q_start >= start_epoch and q_end <= end_epoch:
                            all_latencies.append(lat)
            except Exception as e:
                print(f"Error reading worker file {w_file}: {e}")
                worker_error = True
            finally:
                try:
                    os.remove(w_file)
                except OSError:
                    pass
        else:
            print(f"Worker file {w_file} missing.")
            worker_error = True

    if worker_files_found < args.concurrency:
        worker_error = True

    # Check runtime disk spill counters (Bloqueo 5)
    runtime_spill = None
    if args.engine == "postgresql":
        if pg_start_stats is not None and pg_end_stats is not None:
            runtime_spill = (pg_end_stats[0] - pg_start_stats[0]) > 0
    elif args.engine == "mongodb":
        if mongo_start_spills is not None and mongo_end_spills is not None:
            runtime_spill = mongo_end_spills - mongo_start_spills > 0

    # Scrape Prometheus Telemetry with strict validation (Bloqueo 6)
    prom = PrometheusCollector(args.prometheus_url)
    telemetry_failed = False
    try:
        cpu_throttled = prom.get_container_throttled_seconds(container_name, start_epoch, end_epoch)
        max_rss = prom.get_container_max_rss_mb(container_name, start_epoch, end_epoch)
    except Exception as e:
        print(f"Telemetry validation error: {e}")
        cpu_throttled = None
        max_rss = None
        telemetry_failed = True

    # Check OOM status
    is_oom = check_container_oom(container_name, start_epoch, end_epoch)

    # Classify Status
    status = classify_status(
        oom_status=is_oom,
        worker_error=worker_error or error_event.is_set(),
        spill=runtime_spill,
        telemetry_complete=not telemetry_failed and cpu_throttled is not None and max_rss is not None,
    )
    if status == "oom":
        save_container_diagnostic_log(container_name, f"results/logs/oom_{args.engine}_{args.profile}_{args.query}_c{args.concurrency}_run{args.run_number}.log")
    elif status == "error":
        save_container_diagnostic_log(container_name, f"results/logs/error_{args.engine}_{args.profile}_{args.query}_c{args.concurrency}_run{args.run_number}.log")
    elif status == "spill":
        save_container_diagnostic_log(container_name, f"results/logs/spill_{args.engine}_{args.profile}_{args.query}_c{args.concurrency}_run{args.run_number}.log")

    # Compute metrics strictly over actual measurement duration
    actual_meas_sec = max(1.0, end_epoch - start_epoch)
    lat_stats = calculate_latencies(all_latencies)
    throughput = len(all_latencies) / actual_meas_sec if all_latencies else None

    # Record 14-column CSV
    record_metrics_row(
        csv_path=args.output_csv,
        timestamp_epoch=int(start_epoch),
        engine=args.engine,
        profile=args.profile,
        query_id=args.query if args.variant == "default" else f"{args.query}_{args.variant}",
        concurrency=args.concurrency,
        run_number=args.run_number,
        throughput_qps=throughput,
        latency_p50_ms=lat_stats["p50"],
        latency_p95_ms=lat_stats["p95"],
        latency_p99_ms=lat_stats["p99"],
        cpu_throttled_seconds=cpu_throttled,
        max_rss_mb=max_rss,
        disk_spill=runtime_spill,
        status=status
    )

    print(f"\nCell complete. Summary: Throughput={throughput if throughput is not None else 'n/a'} QPS | P95={lat_stats['p95'] if lat_stats['p95'] is not None else 'n/a'} ms | Spill={runtime_spill} | Status={status}")


if __name__ == "__main__":
    main()
