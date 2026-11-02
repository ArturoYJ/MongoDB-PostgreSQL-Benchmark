#!/usr/bin/env python3
"""Create or verify provenance metadata for the generated orders dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

from synthetic_data_generator import DEFAULT_BATCH_SIZE, DEFAULT_COUNT, DEFAULT_SEED


ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = ROOT / "data/orders.jsonl"
DEFAULT_MANIFEST = ROOT / "data/orders.manifest.json"
GENERATOR = ROOT / "synthetic_data_generator.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_jsonl_records(path: Path) -> int:
    count = 0
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"Blank JSONL record at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected a JSON object at line {line_number}")
            count += 1
    return count


def build_manifest(dataset: Path, generator: Path) -> dict[str, Any]:
    def display_path(path: Path) -> str:
        try:
            return path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return path.name

    return {
        "dataset": {
            "path": display_path(dataset),
            "format": "JSON Lines (one order object per line)",
            "record_count": count_jsonl_records(dataset),
            "size_bytes": dataset.stat().st_size,
            "sha256": sha256_file(dataset),
        },
        "generator": {
            "path": display_path(generator),
            "sha256": sha256_file(generator),
            "reproduction_defaults": {
                "count": DEFAULT_COUNT,
                "seed": DEFAULT_SEED,
                "batch_size": DEFAULT_BATCH_SIZE,
            },
            "original_invocation_captured": False,
        },
        "manifest_environment": {
            "python_version": platform.python_version(),
            "note": "Environment at manifest creation; original generation environment was not recorded.",
        },
    }


def verify_manifest(dataset: Path, generator: Path, manifest_path: Path) -> None:
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = build_manifest(dataset, generator)
    if actual != expected:
        expected_dataset = expected.get("dataset", {})
        actual_dataset = actual["dataset"]
        changed = [
            key for key in ("record_count", "size_bytes", "sha256")
            if expected_dataset.get(key) != actual_dataset.get(key)
        ]
        if expected.get("generator", {}).get("sha256") != actual["generator"]["sha256"]:
            changed.append("generator.sha256")
        if expected.get("manifest_environment") != actual["manifest_environment"]:
            # The Python version is contextual metadata and can differ on a new host.
            if expected.get("manifest_environment", {}).get("note") != actual["manifest_environment"]["note"]:
                changed.append("manifest_environment.note")
        if changed:
            raise ValueError("Manifest mismatch: " + ", ".join(changed))
    print(f"Manifest verified: {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Write or replace the manifest")
    mode.add_argument("--check", action="store_true", help="Verify dataset and generator against the manifest")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--generator", type=Path, default=GENERATOR)
    args = parser.parse_args()

    for path in (args.dataset, args.generator):
        if not path.is_file():
            raise FileNotFoundError(path)

    if args.write:
        manifest = build_manifest(args.dataset, args.generator)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote manifest: {args.manifest}")
    else:
        if not args.manifest.is_file():
            raise FileNotFoundError(args.manifest)
        verify_manifest(args.dataset, args.generator, args.manifest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
