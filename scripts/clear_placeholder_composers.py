#!/usr/bin/env python3
"""Clear placeholder values out of tracks.composer.

A release tag can carry "Unknown", "N/A", "None" or "Not Applicable" where the
real writer was never stated. Those strings are not people, but the Composers
shelf groups by the raw string, so the library grows four fictional "composers"
that own tracks nobody wrote. This writes NULL over the placeholders only.

Nothing here guesses at a real credit - a track tagged "Unknown" stays untitled
for its writer, which is what the tag said. It just stops being listed as a
person.

Safety: dry-run by default; --backup uses the SQLite backup API because almost
all of this library's data is still in library.db-wal, so copying library.db on
its own yields a hollow file.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "velvet_data" / "library.db"

# Matched case-insensitively after trimming. Keep this an explicit list: a
# broad regex would eventually eat a real name, and every entry here is a string
# no labeler writes as a name.
PLACEHOLDERS = ("unknown", "not applicable", "n/a", "na", "none", "unknown composer")


def placeholders(con) -> list[str]:
    """The placeholder spellings actually present, as stored."""
    lowered = [p.lower() for p in PLACEHOLDERS]
    marks = ",".join("?" * len(lowered))
    rows = con.execute(
        f"SELECT DISTINCT composer FROM tracks "
        f"WHERE LOWER(TRIM(COALESCE(composer,''))) IN ({marks})", lowered).fetchall()
    return sorted(r["composer"] for r in rows if r["composer"] is not None)


def plan(con, values: list[str]) -> list[dict]:
    lowered = [p.lower() for p in PLACEHOLDERS]
    marks = ",".join("?" * len(lowered))
    rows = con.execute(
        f"""SELECT t.id, t.title, t.composer, al.title AS album
            FROM tracks t LEFT JOIN albums al ON al.id = t.album_id
            WHERE LOWER(TRIM(COALESCE(t.composer,''))) IN ({marks})
            ORDER BY t.composer, t.id""", lowered).fetchall()
    return [dict(r) for r in rows]


def report(values: list[str], rows: list[dict]) -> None:
    print("=== PLAN ===")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["composer"]] = counts.get(r["composer"], 0) + 1
    for value in values:
        print(f"\n  {value!r}: {counts.get(value, 0)} tracks")
    print(f"\n  total: {len(rows)} tracks across {len(values)} placeholder values")
    if rows:
        print("\n  first 12:")
        for r in rows[:12]:
            print(f"    [{r['id']}] {r['composer']!r} -> NULL   "
                  f"{str(r['title'])[:34]!r} ({str(r['album'])[:26]!r})")


def backup(con) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = DB.with_name(f"library.db.bak-sentinel-{stamp}")
    target = sqlite3.connect(dest)
    try:
        con.backup(target)
    finally:
        target.close()
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--backup", action="store_true", help="snapshot library.db first")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")

    values = placeholders(con)
    rows = plan(con, values)
    report(values, rows)

    if not rows:
        print("\nnothing to clear")
        return 0
    if not args.apply:
        print("\nDRY RUN - pass --apply to write")
        return 1

    if args.backup:
        dest = backup(con)
        print(f"\nbackup: {dest}")

    lowered = [p.lower() for p in PLACEHOLDERS]
    marks = ",".join("?" * len(lowered))
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            f"UPDATE tracks SET composer=NULL "
            f"WHERE LOWER(TRIM(COALESCE(composer,''))) IN ({marks})", lowered)
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"\nROLLED BACK: {exc}")
        return 1

    print(f"\ncleared: {cur.rowcount} tracks")
    from library_search_index import refresh_track_search_index
    print(f"search index refreshed: {refresh_track_search_index(con)} tracks")

    left = placeholders(con)
    print(f"placeholder composers remaining: {left or 'none'}")
    print(f"integrity: {con.execute('PRAGMA integrity_check').fetchone()[0]}")
    print(f"fk violations: {len(con.execute('PRAGMA foreign_key_check').fetchall())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())