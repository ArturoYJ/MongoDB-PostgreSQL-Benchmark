"""Dependency-free CSV contract shared by runner and receipts."""
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
