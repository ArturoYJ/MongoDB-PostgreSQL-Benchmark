"""
Database Ingestion and Schema Management
=========================================
Adheres to Functional Requirements FR-01 and FR-02.
Verifies dataset cardinality (500,000 records) and permitted indexes.
"""

import subprocess
import time
from typing import Dict, Any, List
import pymongo
import psycopg2


def check_and_load_mongodb(
    mongo_uri: str = "mongodb://localhost:27017",
    db_name: str = "benchmark",
    collection_name: str = "orders",
    container_name: str = "benchmark_mongodb",
    import_file: str = "/data/import/orders.jsonl",
    target_count: int = 500_000
) -> None:
    """
    Ensure MongoDB contains exactly target_count documents and permitted indexes only.
    """
    print(f"Connecting to MongoDB at {mongo_uri}...")
    client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    coll = db[collection_name]

    current_count = coll.count_documents({})
    print(f"MongoDB '{db_name}.{collection_name}' current document count: {current_count:,}")

    if current_count not in (0, target_count):
        raise RuntimeError(f'Nonempty partial/oversized MongoDB dataset ({current_count}); refusing duplicate import')
    if current_count < target_count:
        print(f"Dataset incomplete ({current_count} < {target_count}). Executing mongoimport...")
        cmd = [
            "docker", "exec", container_name,
            "mongoimport",
            "--db", db_name,
            "--collection", collection_name,
            "--file", import_file,
            "--numInsertionWorkers", "4"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("mongoimport output:", result.stdout)
        current_count = coll.count_documents({})
        if current_count != target_count:
            raise RuntimeError(f"mongoimport failed: expected {target_count}, got {current_count}")

    # Enforce permitted indexes (FR-02)
    print("Ensuring authorized indexes in MongoDB...")
    coll.create_index([("timestamp", 1)], background=False)
    coll.create_index([("order_id", 1)], background=False)

    indexes = coll.list_indexes()
    index_names = [idx["name"] for idx in indexes]
    print(f"Active MongoDB indexes: {index_names}")

    # Guardrail check: prohibit index on payment.final_total
    for idx in coll.list_indexes():
        keys = list(idx["key"].keys())
        if "payment.final_total" in keys:
            raise RuntimeError("Prohibited index on payment.final_total detected in MongoDB!")

    print("MongoDB dataset and indexes verified successfully.")


def check_and_load_postgresql(
    pg_host: str = "localhost",
    pg_port: int = 5434,
    pg_db: str = "benchmark",
    pg_user: str = "postgres",
    pg_password: str = "postgres",
    container_name: str = "benchmark_postgresql",
    import_file: str = "/data/import/orders.jsonl",
    target_count: int = 500_000
) -> None:
    """
    Ensure PostgreSQL contains table orders(id UUID PRIMARY KEY, data JSONB) with
    target_count records and authorized indexes only.
    """
    print(f"Connecting to PostgreSQL at {pg_host}:{pg_port}/{pg_db}...")
    conn = psycopg2.connect(
        host=pg_host,
        port=pg_port,
        dbname=pg_db,
        user=pg_user,
        password=pg_password
    )
    conn.autocommit = True
    cursor = conn.cursor()

    # Create target table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id UUID PRIMARY KEY,
            data JSONB NOT NULL
        );
    """)

    cursor.execute("SELECT COUNT(*) FROM orders;")
    current_count = cursor.fetchone()[0]
    print(f"PostgreSQL 'orders' current row count: {current_count:,}")

    if current_count < target_count:
        print(f"Dataset incomplete ({current_count} < {target_count}). Ingesting via staging COPY...")
        cursor.execute("CREATE UNLOGGED TABLE IF NOT EXISTS staging_orders (line text);")
        cursor.execute("TRUNCATE staging_orders;")
        cursor.execute(f"COPY staging_orders FROM '{import_file}' WITH (FORMAT csv, QUOTE e'\\x01', DELIMITER e'\\x02');")
        print("Staging rows loaded. Parsing JSONB and inserting into orders table...")
        cursor.execute("""
            INSERT INTO orders (id, data)
            SELECT (line::jsonb->>'order_id')::uuid, line::jsonb
            FROM staging_orders
            ON CONFLICT (id) DO NOTHING;
        """)
        cursor.execute("DROP TABLE staging_orders;")
        cursor.execute("SELECT COUNT(*) FROM orders;")
        current_count = cursor.fetchone()[0]
        if current_count != target_count:
            raise RuntimeError(f"PostgreSQL ingestion incomplete: expected {target_count}, got {current_count}")

    if current_count != target_count:
        raise RuntimeError(f'PostgreSQL count mismatch: {current_count} != {target_count}')
    # Enforce permitted indexes (FR-02)
    print("Ensuring authorized indexes in PostgreSQL...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp_text ON orders ((data->>'timestamp'));")
    
    # Run VACUUM ANALYZE to update statistics
    print("Updating PostgreSQL optimizer statistics (VACUUM ANALYZE)...")
    cursor.execute("VACUUM ANALYZE orders;")

    cursor.execute("""
        SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'orders';
    """)
    indexes = cursor.fetchall()
    print("Active PostgreSQL indexes:")
    for name, definition in indexes:
        print(f"  - {name}: {definition}")
        if "final_total" in definition:
            raise RuntimeError("Prohibited index on payment.final_total detected in PostgreSQL!")

    cursor.close()
    conn.close()
    print("PostgreSQL dataset and indexes verified successfully.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ingest and verify benchmark datasets.")
    parser.add_argument("--engine", choices=["mongodb", "postgresql", "both"], default="both")
    args = parser.parse_args()

    if args.engine in ("mongodb", "both"):
        try:
            check_and_load_mongodb()
        except Exception as e:
            raise SystemExit(f"MongoDB verification error: {e}")

    if args.engine in ("postgresql", "both"):
        try:
            check_and_load_postgresql()
        except Exception as e:
            raise SystemExit(f"PostgreSQL verification error: {e}")
