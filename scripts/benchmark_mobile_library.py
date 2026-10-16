#!/usr/bin/env python3
"""Read-only Windows library paging baseline. Never writes to the music DB."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import quote

# Run as `python scripts\benchmark_mobile_library.py`: the project root (not
# scripts\) must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from library_paging import fetch_album_page, fetch_artist_page  # noqa: E402


def timing(db: sqlite3.Connection, fn, offsets=(0, 60, 120), repeats=4):
    samples = []
    transferred = []
    total = 0
    for offset in offsets:
        for _ in range(repeats):
            t0 = time.perf_counter()
            result = fn(db, limit=60, offset=offset)
            samples.append((time.perf_counter() - t0) * 1000)
            transferred.append(len(json.dumps(result, default=str).encode("utf-8")))
            total = result["total"]
    ordered = sorted(samples)
    return {
        "total_records": total,
        "samples": len(ordered),
        "median_query_ms": round(statistics.median(ordered), 2),
        "p95_query_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 2),
        "median_raw_payload_kb": round(statistics.median(transferred) / 1024, 2),
        "note": "Server-side SQL time and raw JSON size only; not Tailscale latency or compressed bytes.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to a COPY of velvet_data/library.db")
    args = parser.parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.is_file():
        parser.error("Database file not found")
    uri = "file:" + quote(db_path.as_posix(), safe="/:") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        albums = timing(db, fetch_album_page)
        artists = timing(db, fetch_artist_page)
    finally:
        db.close()
    print(json.dumps({"albums": albums, "artists": artists}, indent=2))


if __name__ == "__main__":
    main()
