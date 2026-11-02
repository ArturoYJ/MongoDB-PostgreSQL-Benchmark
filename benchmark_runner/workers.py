"""
Multiprocessing Worker Functions for Load Generation
===================================================
Resolves:
- Bloqueo 3: Eliminates multiprocessing.Queue buffer deadlock by streaming results
  to worker-specific temporary files.
- Bloqueo 4: Records (t_start, t_end, latency_ms) for every query execution so
  the orchestrator can filter queries strictly bounded within the measurement window.
"""

import json
import os
import time
from typing import List, Tuple
import psycopg2
import pymongo


def get_worker_result_file(worker_id: int) -> str:
    return f"/tmp/benchmark_worker_{worker_id}.json"


def run_mongodb_worker(
    worker_id: int,
    pipeline: list,
    allow_disk_use: bool,
    measuring_event,
    stop_event,
    error_event
) -> None:
    output_file = get_worker_result_file(worker_id)
    if os.path.exists(output_file):
        try:
            os.remove(output_file)
        except OSError:
            pass

    client = pymongo.MongoClient(
        "mongodb://127.0.0.1:27017",
        serverSelectionTimeoutMS=5000,
        socketTimeoutMS=120000,
        connectTimeoutMS=5000
    )
    db = client["benchmark"]
    coll = db["orders"]

    # Phase 1: Warmup Phase (untracked, runs until measuring_event is set)
    while not measuring_event.is_set() and not stop_event.is_set() and not error_event.is_set():
        try:
            coll.aggregate(pipeline, allowDiskUse=allow_disk_use).to_list(length=1000)
        except Exception as e:
            if not stop_event.is_set():
                error_event.set()
                print(f"Worker {worker_id} caught MongoDB warmup failure: {e}")
            break

    # Phase 2: Active Measurement Phase (runs until stop_event is set)
    queries: List[Tuple[float, float, float]] = []
    error_occurred = False
    err_msg = ""

    while not stop_event.is_set() and not error_event.is_set():
        t0 = time.time()
        try:
            coll.aggregate(pipeline, allowDiskUse=allow_disk_use).to_list(length=1000)
            t1 = time.time()
            queries.append((t0, t1, (t1 - t0) * 1000.0))
        except Exception as e:
            if not stop_event.is_set():
                error_occurred = True
                err_msg = str(e)
                error_event.set()
                print(f"Worker {worker_id} caught MongoDB query failure: {e}")
            break

    client.close()

    # Persist worker output to file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "worker_id": worker_id,
            "queries": queries,
            "error": error_occurred,
            "error_msg": err_msg
        }, f)


def run_postgresql_worker(
    worker_id: int,
    sql_query: str,
    work_mem: str,
    measuring_event,
    stop_event,
    error_event,
    pg_port: int = 5434
) -> None:
    output_file = get_worker_result_file(worker_id)
    if os.path.exists(output_file):
        try:
            os.remove(output_file)
        except OSError:
            pass

    conn = psycopg2.connect(
        host="127.0.0.1",
        port=pg_port,
        dbname="benchmark",
        user="postgres",
        password="postgres",
        options="-c statement_timeout=120000"
    )
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute(f"SET work_mem = '{work_mem}';")

    # Phase 1: Warmup Phase (untracked, runs until measuring_event is set)
    while not measuring_event.is_set() and not stop_event.is_set() and not error_event.is_set():
        try:
            cursor.execute(sql_query)
            cursor.fetchall()
        except Exception as e:
            if not stop_event.is_set():
                error_event.set()
                print(f"Worker {worker_id} caught PostgreSQL warmup failure: {e}")
            break

    # Phase 2: Active Measurement Phase (runs until stop_event is set)
    queries: List[Tuple[float, float, float]] = []
    error_occurred = False
    err_msg = ""

    while not stop_event.is_set() and not error_event.is_set():
        t0 = time.time()
        try:
            cursor.execute(sql_query)
            cursor.fetchall()
            t1 = time.time()
            queries.append((t0, t1, (t1 - t0) * 1000.0))
        except Exception as e:
            if not stop_event.is_set():
                error_occurred = True
                err_msg = str(e)
                error_event.set()
                print(f"Worker {worker_id} caught PostgreSQL query failure: {e}")
            break

    cursor.close()
    conn.close()

    # Persist worker output to file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "worker_id": worker_id,
            "queries": queries,
            "error": error_occurred,
            "error_msg": err_msg
        }, f)
