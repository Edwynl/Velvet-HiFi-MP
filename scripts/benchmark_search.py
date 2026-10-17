#!/usr/bin/env python3
"""Time /api/search on a copy of the library. Never writes to the source DB.

The source is opened read-only and copied with SQLite's backup API; the copy
is migrated and indexed, then reopened read-only for timing. With --verify,
every query is first answered by the exact path (unindexed copy) and the
indexed answer must be identical.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import quote

# Run as `python scripts\benchmark_search.py`: the project root (not
# scripts\) must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chinese_variants import get_all_variants, to_simplified, to_traditional  # noqa: E402
from db_schema import apply_migrations  # noqa: E402
from library_discovery import search_tracks  # noqa: E402
from library_search_index import refresh_track_search_index  # noqa: E402

DEFAULT_QUERIES = [
    "钢琴", "鋼琴", "協奏曲", "协奏曲", "贝多芬", "巴赫", "交响曲", "愛", "华",
    "zzqx不存在的东西", "zzqx-no-such-track", "piano", "Bach", "the", "a", "love",
    "快乐", "邓丽君", "鄧麗君", "周杰倫", "周杰伦", "郎朗", "卡拉扬 柏林爱乐 贝多芬",
    "Symphony No. 9", "Op. 27", "100%", "track_01",
]


def _readonly(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _search(db: sqlite3.Connection, q: str, limit: int) -> list[int]:
    result = search_tracks(db, q, limit, get_all_variants, to_simplified, to_traditional)
    return [track["id"] for track in result["tracks"]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="velvet_data/library.db (opened read-only)")
    parser.add_argument("--copy", required=True, help="Scratch path for the benchmark copy (overwritten)")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--verify", action="store_true", help="Compare with the exact path")
    parser.add_argument("--query", action="append", help="Query to time (repeatable)")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    copy = Path(args.copy).expanduser().resolve()
    if not source.is_file():
        parser.error("Source database not found")
    if copy == source:
        parser.error("--copy must not be the source database")
    copy.unlink(missing_ok=True)
    src = _readonly(source)
    dst = sqlite3.connect(copy)
    try:
        src.backup(dst)
    finally:
        src.close()
    dst.row_factory = sqlite3.Row
    apply_migrations(dst)
    dst.close()

    queries = args.query or DEFAULT_QUERIES
    expected = {}
    if args.verify:
        db = _readonly(copy)
        try:
            for q in queries:
                expected[q] = _search(db, q, args.limit)
        finally:
            db.close()

    dst = sqlite3.connect(copy)
    started = time.perf_counter()
    indexed = refresh_track_search_index(dst)
    dst.commit()
    dst.close()
    build_seconds = time.perf_counter() - started

    report = {"tracks_indexed": indexed, "index_build_seconds": round(build_seconds, 2), "queries": {}}
    mismatches = []
    db = _readonly(copy)
    try:
        for q in queries:
            samples = []
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                ids = _search(db, q, args.limit)
                samples.append((time.perf_counter() - t0) * 1000)
            report["queries"][q] = {
                "results": len(ids),
                "median_ms": round(statistics.median(samples), 2),
                "max_ms": round(max(samples), 2),
            }
            if args.verify and ids != expected[q]:
                mismatches.append(q)
    finally:
        db.close()
    report["worst_median_ms"] = max(r["median_ms"] for r in report["queries"].values())
    if args.verify:
        report["verified_identical"] = not mismatches
        report["mismatches"] = mismatches
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if mismatches:
        sys.exit(1)


if __name__ == "__main__":
    main()
