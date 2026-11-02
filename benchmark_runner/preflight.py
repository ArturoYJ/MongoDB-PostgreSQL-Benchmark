"""Read-only fail-fast checks before any pilot or benchmark matrix run."""

import argparse
import hashlib
import json
import math
import re
import subprocess
import time
import uuid
from typing import List, Sequence, Tuple

import psycopg2
import pymongo
import requests

CSV_COLUMNS = [
    "timestamp_epoch", "engine", "profile", "query_id", "concurrency", "run_number",
    "throughput_qps", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
    "cpu_throttled_seconds", "max_rss_mb", "disk_spill", "status",
]
EXPECTED_MONGO_INDEXES = {
    "_id_": [("_id", 1)],
    "timestamp_1": [("timestamp", 1)],
    "order_id_1": [("order_id", 1)],
}
EXPECTED_POSTGRES_INDEXES = {"orders_pkey", "idx_orders_timestamp_text"}


def canonical_document_digest(document: dict) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_expected_documents(data_path: str, expected_count: int) -> List[Tuple[str, str]]:
    records = []
    seen = set()
    with open(data_path, "r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            try:
                document = json.loads(line)
                order_id = document["order_id"]
                canonical_id = str(uuid.UUID(order_id))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Invalid source dataset row {line_number}: {exc}") from exc
            if canonical_id in seen:
                raise RuntimeError(f"Duplicate order_id in source dataset at row {line_number}: {canonical_id}")
            seen.add(canonical_id)
            records.append((canonical_id, canonical_document_digest(document)))
    if len(records) != expected_count:
        raise RuntimeError(f"Source dataset has {len(records):,} rows; expected exactly {expected_count:,}.")
    return sorted(records)


def validate_csv_header(csv_path: str) -> None:
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as source:
            header = source.readline().rstrip("\r\n").split(",")
    except OSError as exc:
        raise RuntimeError(f"Cannot read metrics CSV {csv_path}: {exc}") from exc
    if header != CSV_COLUMNS:
        raise RuntimeError(f"Metrics CSV header differs from the canonical 14-column contract: {header}")


def check_prometheus(prometheus_url: str, container_name: str) -> None:
    base = prometheus_url.rstrip("/")
    try:
        healthy = requests.get(f"{base}/-/healthy", timeout=4)
        healthy.raise_for_status()
        targets_response = requests.get(f"{base}/api/v1/targets", timeout=4)
        targets_response.raise_for_status()
        targets = targets_response.json()["data"]["activeTargets"]
    except Exception as exc:
        raise RuntimeError(f"Prometheus is unavailable or returned an invalid response: {exc}") from exc

    for job in ("cadvisor", "node-exporter"):
        if not any(target.get("labels", {}).get("job") == job and target.get("health") == "up" for target in targets):
            raise RuntimeError(f"Prometheus has no healthy active scrape target for job '{job}'.")

    try:
        container_id = subprocess.run(
            ["docker", "inspect", "--format", "{{.Id}}", container_name],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception as exc:
        raise RuntimeError(f"Cannot inspect benchmark container '{container_name}': {exc}") from exc
    if not container_id:
        raise RuntimeError(f"Docker returned an empty container ID for '{container_name}'.")

    last_error = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for metric in ("container_memory_rss", "container_cpu_cfs_throttled_seconds_total"):
            query = f'{metric}{{id=~".*{container_id}.*"}}'
            try:
                response = requests.get(f"{base}/api/v1/query", params={"query": query}, timeout=4)
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") != "success" or not payload.get("data", {}).get("result"):
                    break
                result = payload['data']['result']
                if len(result) != 1 or not math.isfinite(float(result[0]['value'][1])) or float(result[0]['value'][1]) < 0:
                    break
            except Exception:
                break
        else:
            try:
                from benchmark_runner.metrics import PrometheusCollector, collect_window
                now = time.time() - 2
                collect_window(PrometheusCollector(base), container_name, now - 5, now)
                return
            except Exception as exc:
                last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Prometheus did not scrape RSS and CFS metrics for '{container_name}' within 30 seconds: {last_error}.")


def _wait_for_mongodb(uri: str, timeout_seconds: int = 60) -> pymongo.MongoClient:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=2500, connectTimeoutMS=2500)
        try:
            client.admin.command("ping")
            return client
        except Exception as exc:
            last_error = exc
            client.close()
            time.sleep(2)
    raise RuntimeError(f"MongoDB did not become available within {timeout_seconds}s: {last_error}")


def validate_mongodb(uri: str, expected_documents: Sequence[Tuple[str, str]], expected_count: int) -> str:
    client = _wait_for_mongodb(uri)
    try:
        info = client.admin.command("buildInfo")
        collection = client["benchmark"]["orders"]
        count = collection.count_documents({})
        if count != expected_count:
            raise RuntimeError(f"MongoDB has {count:,} documents; expected exactly {expected_count:,}.")

        raw_indexes = list(collection.list_indexes())
        for item in raw_indexes:
            if any(k in item for k in ('partialFilterExpression','collation','expireAfterSeconds','hidden')) or item.get('sparse') or (item['name'] != '_id_' and item.get('unique')):
                raise RuntimeError(f"Unexpected Mongo index options: {item}")
        actual_indexes = {
            item["name"]: list(item["key"].items())
            for item in raw_indexes
        }
        if actual_indexes != EXPECTED_MONGO_INDEXES:
            raise RuntimeError(f"MongoDB indexes mismatch. Actual indexes: {actual_indexes}")

        cursor = collection.find({}, {"_id": 0}).sort("order_id", 1).batch_size(2000)
        rows_read = 0
        for row_number, (expected_record, document) in enumerate(zip(expected_documents, cursor), 1):
            rows_read += 1
            actual_id = document.get("order_id")
            actual_record = (actual_id, canonical_document_digest(document))
            if actual_record != expected_record:
                raise RuntimeError(f"MongoDB document content mismatch at sorted row {row_number}: {actual_id!r}")
        if rows_read != expected_count:
            # The explicit count above checks total rows; this guards cursor truncation.
            raise RuntimeError("MongoDB order_id scan ended before all expected rows were read.")
        return info.get("version", "unknown")
    finally:
        client.close()


def _wait_for_postgresql(
    host: str, port: int, database: str, user: str, password: str, timeout_seconds: int = 60
):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            return psycopg2.connect(
                host=host, port=port, dbname=database, user=user, password=password, connect_timeout=3
            )
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"PostgreSQL did not become available within {timeout_seconds}s: {last_error}")


def validate_postgresql(
    host: str, port: int, database: str, user: str, password: str,
    expected_documents: Sequence[Tuple[str, str]], expected_count: int,
) -> str:
    connection = _wait_for_postgresql(host, port, database, user, password)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM orders")
            count = cursor.fetchone()[0]
            if count != expected_count:
                raise RuntimeError(f"PostgreSQL has {count:,} rows; expected exactly {expected_count:,}.")

            cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = 'orders'")
            indexes = dict(cursor.fetchall())
            if set(indexes) != EXPECTED_POSTGRES_INDEXES:
                raise RuntimeError(f"PostgreSQL indexes mismatch. Actual indexes: {sorted(indexes)}")
            primary = indexes["orders_pkey"].lower()
            timestamp_index = indexes["idx_orders_timestamp_text"].lower()
            cursor.execute(
                "SELECT c.conname, array_agg(a.attname ORDER BY k.ordinality) "
                "FROM pg_constraint c "
                "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
                "WHERE c.conrelid = 'orders'::regclass AND c.contype = 'p' "
                "GROUP BY c.conname"
            )
            primary_constraints = cursor.fetchall()
            if primary_constraints != [("orders_pkey", ["id"])]:
                raise RuntimeError(f"PostgreSQL primary key mismatch: {primary_constraints}")
            if "using btree (id)" not in primary:
                raise RuntimeError(f"Unexpected PostgreSQL primary-key definition: {indexes['orders_pkey']}")
            if "using btree" not in timestamp_index or not re.search(r"data\s*->>\s*'timestamp'", timestamp_index):
                raise RuntimeError(f"Unexpected PostgreSQL timestamp-index definition: {indexes['idx_orders_timestamp_text']}")

            cursor.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='orders' ORDER BY ordinal_position")
            if cursor.fetchall() != [('id','uuid'),('data','jsonb')]:
                raise RuntimeError('PostgreSQL column types differ from UUID/JSONB contract')
            cursor.execute("SELECT indisvalid, indisready, indpred IS NULL, indnkeyatts, indnatts FROM pg_index WHERE indrelid='orders'::regclass")
            if any(row != (True,True,True,1,1) for row in cursor.fetchall()):
                raise RuntimeError('Invalid, partial, multicolumn or covering PostgreSQL index')
            normalized = re.sub(r"\s+", " ", timestamp_index).strip()
            expected = "create index idx_orders_timestamp_text on public.orders using btree (((data ->> 'timestamp'::text)))"
            if normalized != expected:
                raise RuntimeError(f'Unexpected timestamp index expression: {timestamp_index}')
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            cursor.execute("SHOW server_version_num")
            if int(cursor.fetchone()[0]) // 10000 != 18:
                raise RuntimeError('Expected approved stable PostgreSQL major 18')

        scan = connection.cursor(name="bdaas_preflight_orders")
        scan.itersize = 10000
        scan.execute("SELECT id::text, data FROM orders ORDER BY id")
        rows_read = 0
        for row_number, (expected_record, row) in enumerate(zip(expected_documents, scan), 1):
            rows_read += 1
            db_id, document = row
            if isinstance(document, str):
                document = json.loads(document)
            actual_record = (db_id, canonical_document_digest(document))
            if actual_record != expected_record:
                raise RuntimeError(f"PostgreSQL document content or id mismatch at sorted row {row_number}: {db_id!r}")
        if rows_read != expected_count:
            raise RuntimeError("PostgreSQL order_id scan ended before all expected rows were read.")
        scan.close()
        return version
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only validation before benchmark execution.")
    parser.add_argument("--engine", choices=["mongodb", "postgresql"], required=True)
    parser.add_argument("--data-file", default="data/orders.jsonl")
    parser.add_argument("--expected-count", type=int, default=500_000)
    parser.add_argument("--csv", default="results/metrics.csv")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--mongo-uri", default="mongodb://127.0.0.1:27017")
    parser.add_argument("--pg-host", default="127.0.0.1")
    parser.add_argument("--pg-port", type=int, default=5434)
    parser.add_argument("--pg-db", default="benchmark")
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-password", default="postgres")
    parser.add_argument('--profile', choices=['PROFILE_A_BASELINE','PROFILE_B_MEDIUM','PROFILE_C_SEVERE'], default='PROFILE_A_BASELINE')
    parser.add_argument('--runtime-only', action='store_true')
    args = parser.parse_args()
    from benchmark_runner.runtime import inspect_environment
    inspect_environment(args.engine, args.profile)
    if args.runtime_only:
        check_prometheus(args.prometheus_url, f'benchmark_{args.engine}')
        print('Runtime profile/isolation/telemetry PASS')
        return

    validate_csv_header(args.csv)
    expected_documents = read_expected_documents(args.data_file, args.expected_count)
    print(f"Source dataset verified: {len(expected_documents):,} unique order IDs and full-document hashes.")
    check_prometheus(args.prometheus_url, f"benchmark_{args.engine}")
    print("Prometheus, cAdvisor and Node Exporter targets are healthy; required container series are present.")

    if args.engine == "mongodb":
        version = validate_mongodb(args.mongo_uri, expected_documents, args.expected_count)
    else:
        version = validate_postgresql(
            args.pg_host, args.pg_port, args.pg_db, args.pg_user, args.pg_password,
            expected_documents, args.expected_count,
        )
    print(f"PREFLIGHT PASS: engine={args.engine}; version={version}; documents={args.expected_count:,}; full records and indexes match.")


if __name__ == "__main__":
    main()
