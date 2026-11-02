"""
Telemetry Collection and 14-Column CSV Metrics Recorder
======================================================
Course: Bases de Datos en la Nube (IT2iD - 2026)
Reference: playbook.pdf (Section 8.2) & playbook_sesion.pdf (Slides 8-10)

Resolves:
- Bloqueo 6: Rigorous telemetry validation prohibiting silent NaN acceptance,
  requiring minimum scrape sample density across measurement intervals.
"""

import csv
import math
import os
import time
from typing import Dict, Any, List, Optional
import numpy as np
import requests

CANONICAL_COLUMNS = [
    "timestamp_epoch",
    "engine",
    "profile",
    "query_id",
    "concurrency",
    "run_number",
    "throughput_qps",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "cpu_throttled_seconds",
    "max_rss_mb",
    "disk_spill",
    "status"
]

VALID_STATUSES = {"ok", "spill", "oom", "error"}


class PrometheusCollector:
    """Scrapes container metrics from local Prometheus instance with strict data validation."""

    def __init__(self, prometheus_url: str = "http://127.0.0.1:9090"):
        self.url = prometheus_url.rstrip("/")

    def _get_container_id(self, container_name: str) -> str:
        try:
            import subprocess
            res = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", container_name],
                capture_output=True,
                text=True,
                check=True
            )
            return res.stdout.strip()
        except Exception:
            return container_name

    def query_instant(self, query: str) -> Optional[float]:
        try:
            resp = requests.get(f"{self.url}/api/v1/query", params={"query": query}, timeout=3)
            data = resp.json()
            if data.get("status") == "success" and data["data"]["result"]:
                val = float(data["data"]["result"][0]["value"][1])
                if not math.isnan(val) and not math.isinf(val):
                    return val
        except Exception as e:
            print(f"Prometheus instant query failed ('{query}'): {e}")
        return None

    def query_range_values(self, query: str, start_epoch: float, end_epoch: float) -> List[float]:
        try:
            resp = requests.get(
                f"{self.url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": start_epoch,
                    "end": end_epoch,
                    "step": "1s"
                },
                timeout=5
            )
            data = resp.json()
            if data.get("status") == "success" and data["data"]["result"]:
                raw_values = data["data"]["result"][0]["values"]
                clean_values = []
                for v in raw_values:
                    try:
                        f_val = float(v[1])
                        if not math.isnan(f_val) and not math.isinf(f_val):
                            clean_values.append(f_val)
                    except (ValueError, TypeError):
                        continue
                return clean_values
        except Exception as e:
            print(f"Prometheus range query failed ('{query}'): {e}")
        return []

    def get_container_throttled_seconds(self, container_name: str, start_epoch: float, end_epoch: float) -> float:
        """
        Calculate total CFS throttled seconds during the measurement window.
        Validates start and end samples.
        """
        cid = self._get_container_id(container_name)
        query = f'container_cpu_cfs_throttled_seconds_total{{id=~".*{cid}.*"}}'
        start_val = self.query_instant(f"{query} @ {int(start_epoch)}")
        end_val = self.query_instant(f"{query} @ {int(end_epoch)}")

        if start_val is not None and end_val is not None:
            return max(0.0, round(end_val - start_val, 4))

        raise RuntimeError(f"Prometheus failed to supply valid CFS throttled metrics for {container_name}")

    def get_container_max_rss_mb(self, container_name: str, start_epoch: float, end_epoch: float) -> float:
        """
        Obtain peak RSS memory in Megabytes during the measurement window.
        Enforces minimum sample density to reject uncollected / NaN windows.
        """
        cid = self._get_container_id(container_name)
        query = f'container_memory_rss{{id=~".*{cid}.*"}}'
        values = self.query_range_values(query, start_epoch, end_epoch)

        duration = max(1.0, end_epoch - start_epoch)
        min_expected_samples = max(2, int(duration * 0.35))

        if len(values) < min_expected_samples:
            raise RuntimeError(
                f"Telemetry validation failed: insufficient samples ({len(values)} < {min_expected_samples}) "
                f"for {container_name} in interval {int(start_epoch)}..{int(end_epoch)}"
            )

        return round(max(values) / (1024 * 1024), 2)


def calculate_latencies(latencies_ms: List[float]) -> Dict[str, Optional[float]]:
    """Calculate P50, P95, and P99 percentiles from latency array, rejecting NaNs."""
    clean = [l for l in latencies_ms if not math.isnan(l) and not math.isinf(l) and l >= 0.0]
    if not clean:
        return {"p50": None, "p95": None, "p99": None}

    arr = np.array(clean)
    return {
        "p50": round(float(np.percentile(arr, 50)), 2),
        "p95": round(float(np.percentile(arr, 95)), 2),
        "p99": round(float(np.percentile(arr, 99)), 2),
    }


def record_metrics_row(
    csv_path: str,
    timestamp_epoch: int,
    engine: str,
    profile: str,
    query_id: str,
    concurrency: int,
    run_number: int,
    throughput_qps: Optional[float],
    latency_p50_ms: Optional[float],
    latency_p95_ms: Optional[float],
    latency_p99_ms: Optional[float],
    cpu_throttled_seconds: Optional[float],
    max_rss_mb: Optional[float],
    disk_spill: Optional[bool],
    status: str
) -> None:
    """
    Append an immutable measurement row conforming verbatim to the 14-column contract.
    Validates against invalid numbers (NaN, Inf).
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

    for val_name, val in [
        ("throughput_qps", throughput_qps),
        ("latency_p50_ms", latency_p50_ms),
        ("latency_p95_ms", latency_p95_ms),
        ("latency_p99_ms", latency_p99_ms),
        ("cpu_throttled_seconds", cpu_throttled_seconds),
        ("max_rss_mb", max_rss_mb)
    ]:
        if val is not None and (math.isnan(val) or math.isinf(val)):
            raise ValueError(f"Metric '{val_name}' has invalid float value: {val}")

    row = {
        "timestamp_epoch": timestamp_epoch,
        "engine": engine,
        "profile": profile,
        "query_id": query_id,
        "concurrency": concurrency,
        "run_number": run_number,
        "throughput_qps": round(throughput_qps, 2) if throughput_qps is not None else "",
        "latency_p50_ms": round(latency_p50_ms, 2) if latency_p50_ms is not None else "",
        "latency_p95_ms": round(latency_p95_ms, 2) if latency_p95_ms is not None else "",
        "latency_p99_ms": round(latency_p99_ms, 2) if latency_p99_ms is not None else "",
        "cpu_throttled_seconds": round(cpu_throttled_seconds, 4) if cpu_throttled_seconds is not None else "",
        "max_rss_mb": round(max_rss_mb, 2) if max_rss_mb is not None else "",
        "disk_spill": disk_spill if disk_spill is not None else "",
        "status": status
    }

    file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"Recorded verified metric row in {csv_path}: {row}")
