"""
Plan Observation, Runtime Spill Tracking & Diagnostics Collector
================================================================
Course: Bases de Datos en la Nube (IT2iD - 2026)
Reference: playbook.pdf (Section 8.3 & Reviewer Question 4/6)

Resolves:
- Bloqueo 5: Tracks actual runtime disk spill during concurrent load via database
  internal counters (pg_stat_database for PostgreSQL, serverStatus metrics for MongoDB),
  complemented by structured execution plan parsing.
"""

import json
import os
import re
import subprocess
import time
from typing import Dict, Any, Tuple, Optional
import psycopg2
import pymongo


def get_postgresql_temp_stats(
    pg_host: str = "127.0.0.1",
    pg_port: int = 5434,
    pg_db: str = "benchmark",
    pg_user: str = "postgres",
    pg_pass: str = "postgres"
) -> Optional[Tuple[int, int]]:
    """
    Query pg_stat_database to obtain cumulative (temp_files, temp_bytes) for benchmark DB.
    """
    try:
        conn = psycopg2.connect(
            host=pg_host,
            port=pg_port,
            dbname=pg_db,
            user=pg_user,
            password=pg_pass,
            connect_timeout=3
        )
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("SELECT temp_files, temp_bytes FROM pg_stat_database WHERE datname = %s;", (pg_db,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return int(row[0] or 0), int(row[1] or 0)
    except Exception as e:
        print(f"Warning: Could not fetch pg_stat_database temp stats: {e}")
    return None


def get_mongodb_spill_stats(mongo_uri: str = "mongodb://127.0.0.1:27017") -> Optional[int]:
    """
    Query MongoDB serverStatus to obtain cumulative spillsToDisk count.
    """
    try:
        client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        status = client.admin.command("serverStatus")
        client.close()
        value = status.get("metrics", {}).get("query", {}).get("executor", {}).get("spillsToDisk")
        return int(value) if value is not None else None
    except Exception as e:
        print(f"Warning: Could not fetch MongoDB serverStatus spill stats: {e}")
        return None


def _parse_mongo_plan_spill(node: Any) -> bool:
    """
    Recursively traverse MongoDB explain document to detect boolean usedDisk / spilledToDisk.
    Avoids naive substring matching against 'usedDisk: false'.
    """
    if isinstance(node, dict):
        if node.get("usedDisk") is True or node.get("spilledToDisk") is True:
            return True
        for v in node.values():
            if _parse_mongo_plan_spill(v):
                return True
    elif isinstance(node, list):
        for item in node:
            if _parse_mongo_plan_spill(item):
                return True
    return False


def capture_mongodb_plan(
    mongo_uri: str,
    pipeline: list,
    output_path: str,
    allow_disk_use: bool = False
) -> Tuple[Dict[str, Any], bool]:
    """
    Execute MongoDB explain("executionStats") and structurally verify if disk spill occurred.
    """
    client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client["benchmark"]

    cmd = {
        "explain": {
            "aggregate": "orders",
            "pipeline": pipeline,
            "cursor": {},
            "allowDiskUse": allow_disk_use
        },
        "verbosity": "executionStats"
    }

    plan_result = db.command(cmd)
    client.close()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(plan_result, f, indent=2, default=str)

    plan_spill = _parse_mongo_plan_spill(plan_result)
    print(f"Captured MongoDB execution plan to {output_path} (plan_spill={plan_spill})")
    return plan_result, plan_spill


def capture_postgresql_plan(
    pg_host: str,
    pg_port: int = 5434,
    pg_db: str = "benchmark",
    pg_user: str = "postgres",
    pg_pass: str = "postgres",
    sql_query: str = "",
    output_path: str = "",
    work_mem: str = "4MB"
) -> Tuple[str, bool]:
    """
    Execute PostgreSQL EXPLAIN (ANALYZE, BUFFERS) and check for external merge sort
    or nonzero temporary buffer writes.
    """
    conn = psycopg2.connect(
        host=pg_host,
        port=pg_port,
        dbname=pg_db,
        user=pg_user,
        password=pg_pass
    )
    conn.autocommit = True
    cursor = conn.cursor()

    cursor.execute(f"SET work_mem = '{work_mem}';")
    cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql_query}")
    plan_lines = [row[0] for row in cursor.fetchall()]
    plan_text = "\n".join(plan_lines)

    cursor.close()
    conn.close()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(plan_text)

    # Robust detection: external merge OR temp written > 0
    plan_spill = False
    if "sort method: external merge" in plan_text.lower():
        plan_spill = True
    else:
        m = re.search(r"written=(\d+)", plan_text)
        if m and int(m.group(1)) > 0:
            plan_spill = True

    print(f"Captured PostgreSQL execution plan to {output_path} (plan_spill={plan_spill})")
    return plan_text, plan_spill


def check_container_oom(container_name: str, start_epoch: float, end_epoch: float) -> Optional[bool]:
    """Return whether Docker emitted an OOM event during this attempt's measurement window."""
    try:
        inspect = subprocess.run(["docker", "inspect", "--format", "{{.Id}}", container_name],
                                 capture_output=True, text=True, check=True)
        container_id = inspect.stdout.strip()
        cmd = ["docker", "events", "--since", str(start_epoch), "--until", str(end_epoch),
               "--filter", f"container={container_id}", "--filter", "event=oom", "--format", "{{json .}}"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return any(line.strip() for line in result.stdout.splitlines())
    except Exception as e:
        print(f"Warning: Could not verify measurement-window OOM events for {container_name}: {e}")
        return None


def save_container_diagnostic_log(container_name: str, log_path: str) -> None:
    """Dump container logs upon detecting spill or OOM failure."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        cmd = ["docker", "logs", "--tail", "500", container_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(result.stderr)
        print(f"Saved diagnostic log for {container_name} to {log_path}")
    except Exception as e:
        print(f"Warning: Failed to save diagnostic log: {e}")
