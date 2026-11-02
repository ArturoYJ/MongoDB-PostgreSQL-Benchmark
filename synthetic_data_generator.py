#!/usr/bin/env python3
"""
Synthetic Dataset Generator for Database Contention Benchmark
=============================================================
Course: Bases de Datos en la Nube (IT2iD - 2026)
Reference: playbook.pdf (Section 4) & playbook_sesion.pdf (Slide 3)

Generates 500,000 reproducible e-commerce orders adhering strictly to
the canonical schema, nesting levels (~3), and field types required by
queries Q1 (CPU saturation) and Q2 (buffer stress & Top-K).

Output: Newline-delimited JSON (NDJSON / .jsonl) for streaming ingestion.
"""

import argparse
import datetime
import json
import os
import random
import sys
import time
import uuid
from typing import Dict, Any, List

# Fixed seed for epistemic determinism and reproducibility
DEFAULT_SEED = 42
DEFAULT_COUNT = 500_000
DEFAULT_BATCH_SIZE = 10_000

COUNTRIES = ["US", "MX", "CA", "ES", "DE", "FR", "UK", "BR"]

GEO_BY_COUNTRY = {
    "US": [
        {"state": "CA", "city": "Los Angeles", "zip": "90001", "lat": 34.0522, "lon": -118.2437},
        {"state": "NY", "city": "New York", "zip": "10001", "lat": 40.7128, "lon": -74.0060},
        {"state": "TX", "city": "Austin", "zip": "73301", "lat": 30.2672, "lon": -97.7431},
    ],
    "MX": [
        {"state": "CDMX", "city": "Ciudad de Mexico", "zip": "01000", "lat": 19.4326, "lon": -99.1332},
        {"state": "JAL", "city": "Guadalajara", "zip": "44100", "lat": 20.6597, "lon": -103.3496},
        {"state": "NL", "city": "Monterrey", "zip": "64000", "lat": 25.6866, "lon": -100.3161},
    ],
    "CA": [
        {"state": "ON", "city": "Toronto", "zip": "M5H2N2", "lat": 43.6532, "lon": -79.3832},
        {"state": "BC", "city": "Vancouver", "zip": "V6B1A1", "lat": 49.2827, "lon": -123.1207},
    ],
    "ES": [
        {"state": "MD", "city": "Madrid", "zip": "28001", "lat": 40.4168, "lon": -3.7038},
        {"state": "CT", "city": "Barcelona", "zip": "08001", "lat": 41.3851, "lon": 2.1734},
    ],
    "DE": [
        {"state": "BE", "city": "Berlin", "zip": "10115", "lat": 52.5200, "lon": 13.4050},
        {"state": "BY", "city": "Munich", "zip": "80331", "lat": 48.1351, "lon": 11.5820},
    ],
    "FR": [
        {"state": "IDF", "city": "Paris", "zip": "75001", "lat": 48.8566, "lon": 2.3522},
        {"state": "ARA", "city": "Lyon", "zip": "69001", "lat": 45.7640, "lon": 4.8357},
    ],
    "UK": [
        {"state": "ENG", "city": "London", "zip": "EC1A1BB", "lat": 51.5074, "lon": -0.1278},
        {"state": "MAN", "city": "Manchester", "zip": "M11AE", "lat": 53.4808, "lon": -2.2426},
    ],
    "BR": [
        {"state": "SP", "city": "Sao Paulo", "zip": "01000-000", "lat": -23.5505, "lon": -46.6333},
        {"state": "RJ", "city": "Rio de Janeiro", "zip": "20000-000", "lat": -22.9068, "lon": -43.1729},
    ],
}

TIERS = ["bronze", "silver", "gold", "platinum"]
SEGMENTS = ["consumer", "corporate", "small_business"]

CATEGORIES = [
    "Electronics", "Home", "Apparel", "Books",
    "Automotive", "Garden", "Toys", "Health"
]

BRANDS = [
    "Apex", "NovaCore", "Vanguard", "OmniGear",
    "AeroTech", "Lumina", "TerraPrime", "Zenith"
]

PAYMENT_METHODS = ["credit_card", "debit_card", "paypal", "bank_transfer"]
# Status distribution: 50% completed to provide sufficient volume for Q2 Top-K
PAYMENT_STATUSES = ["completed", "completed", "completed", "pending", "failed", "cancelled"]

CHANNELS = ["web", "mobile_app", "pos"]
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.193 Mobile Safari/537.36"
]

# Base date range spanning 2025-06-01 to 2026-06-30 (13 months)
START_TIMESTAMP = datetime.datetime(2025, 6, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
END_TIMESTAMP = datetime.datetime(2026, 6, 30, 23, 59, 59, tzinfo=datetime.timezone.utc)
TOTAL_SPAN_SECONDS = int((END_TIMESTAMP - START_TIMESTAMP).total_seconds())


def generate_single_order(rng: random.Random) -> Dict[str, Any]:
    """
    Synthesize one e-commerce order adhering strictly to the Section 4 schema.
    Items per order range from 1 to 5 (average = 3.0 items).
    """
    order_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    
    # Generate timestamp in ISO-8601 UTC format
    offset_seconds = rng.randint(0, TOTAL_SPAN_SECONDS)
    order_dt = START_TIMESTAMP + datetime.timedelta(seconds=offset_seconds)
    timestamp_iso = order_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Customer object
    country = rng.choice(COUNTRIES)
    geo_pool = GEO_BY_COUNTRY[country]
    geo_loc = rng.choice(geo_pool)
    customer_id = f"CUST-{rng.randint(100000, 999999)}"
    
    customer = {
        "id": customer_id,
        "tier": rng.choice(TIERS),
        "segment": rng.choice(SEGMENTS),
        "geo": {
            "country": country,
            "state": geo_loc["state"],
            "city": geo_loc["city"],
            "zip": geo_loc["zip"],
            "coordinates": {
                "lat": geo_loc["lat"] + round(rng.uniform(-0.05, 0.05), 4),
                "lon": geo_loc["lon"] + round(rng.uniform(-0.05, 0.05), 4),
            }
        }
    }

    # Items array: random length [1, 5] yields an exact expected mean of 3.0 items
    num_items = rng.randint(1, 5)
    items: List[Dict[str, Any]] = []
    subtotal = 0.0
    discount_total = 0.0
    tax_total = 0.0

    for idx in range(num_items):
        sku = f"SKU-{rng.randint(10000, 99999)}"
        category = rng.choice(CATEGORIES)
        brand = rng.choice(BRANDS)
        qty = rng.randint(1, 8)
        # Price: 60% probability > 50.0 to satisfy Q1 filter selectivity
        if rng.random() < 0.60:
            price = round(rng.uniform(50.01, 450.00), 2)
        else:
            price = round(rng.uniform(5.00, 50.00), 2)
        
        tax_pct = 0.16
        discount = round(rng.choice([0.0, 0.0, 5.0, 10.0]), 2)
        
        item_subtotal = round(qty * price, 2)
        item_tax = round(item_subtotal * tax_pct, 2)
        
        subtotal += item_subtotal
        discount_total += discount
        tax_total += item_tax
        
        items.append({
            "sku": sku,
            "category": category,
            "brand": brand,
            "qty": qty,
            "price": price,
            "tax_pct": tax_pct,
            "discounts": discount
        })

    subtotal = round(subtotal, 2)
    tax_total = round(tax_total, 2)
    discount_total = round(discount_total, 2)
    final_total = max(0.0, round(subtotal + tax_total - discount_total, 2))

    # Payment object
    payment_method = rng.choice(PAYMENT_METHODS)
    payment_status = rng.choice(PAYMENT_STATUSES)
    tx_id = f"TX-{rng.getrandbits(48):012X}"
    gateway_latency_ms = rng.randint(15, 380)

    payment = {
        "method": payment_method,
        "status": payment_status,
        "transaction_id": tx_id,
        "gateway_latency_ms": gateway_latency_ms,
        "final_total": final_total,
        "totals": {
            "subtotal": subtotal,
            "tax": tax_total,
            "discount_total": discount_total,
            "final_total": final_total
        }
    }

    # Metadata object
    metadata = {
        "channel": rng.choice(CHANNELS),
        "user_agent": rng.choice(USER_AGENTS),
        "session_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "flags": {
            "is_gift": rng.random() < 0.12,
            "express_delivery": rng.random() < 0.25,
            "loyalty_applied": rng.random() < 0.35
        }
    }

    return {
        "order_id": order_id,
        "timestamp": timestamp_iso,
        "customer": customer,
        "items": items,
        "payment": payment,
        "metadata": metadata
    }


def generate_dataset(
    output_path: str,
    total_records: int = DEFAULT_COUNT,
    batch_size: int = DEFAULT_BATCH_SIZE,
    seed: int = DEFAULT_SEED
) -> None:
    """
    Generate dataset in batches and stream to an NDJSON (.jsonl) file.
    """
    rng = random.Random(seed)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    start_time = time.time()
    records_written = 0
    total_items = 0

    print(f"Starting synthetic dataset generation...")
    print(f"Target file: {output_path}")
    print(f"Target records: {total_records:,}")
    print(f"RNG seed: {seed}")
    print(f"Batch size: {batch_size:,}")

    with open(output_path, "w", encoding="utf-8") as f:
        while records_written < total_records:
            current_batch = min(batch_size, total_records - records_written)
            batch_lines = []
            
            for _ in range(current_batch):
                order = generate_single_order(rng)
                total_items += len(order["items"])
                batch_lines.append(json.dumps(order, ensure_ascii=False) + "\n")
            
            f.writelines(batch_lines)
            records_written += current_batch

            elapsed = time.time() - start_time
            rate = records_written / elapsed if elapsed > 0 else 0
            percent = (records_written / total_records) * 100
            print(
                f"\rProgress: {records_written:,}/{total_records:,} ({percent:.1f}%) | "
                f"Rate: {rate:,.0f} records/s | Elapsed: {elapsed:.1f}s",
                end="",
                flush=True
            )

    total_time = time.time() - start_time
    avg_items = total_items / records_written if records_written > 0 else 0
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

    print()
    print("=" * 60)
    print(f"Generation completed successfully.")
    print(f"Total records: {records_written:,}")
    print(f"Average items per record: {avg_items:.2f} (Target: ~3.0)")
    print(f"Output file size: {file_size_mb:.2f} MB")
    print(f"Total time elapsed: {total_time:.2f} seconds")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate reproducible synthetic dataset for database contention benchmark."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="data/orders.jsonl",
        help="Target output file path (default: data/orders.jsonl)"
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=DEFAULT_COUNT,
        help=f"Number of records to generate (default: {DEFAULT_COUNT})"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for disk streaming (default: {DEFAULT_BATCH_SIZE})"
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=DEFAULT_SEED,
        help=f"PRNG seed for deterministic reproducibility (default: {DEFAULT_SEED})"
    )

    args = parser.parse_args()
    generate_dataset(
        output_path=args.output,
        total_records=args.count,
        batch_size=args.batch_size,
        seed=args.seed
    )


if __name__ == "__main__":
    main()

