#!/usr/bin/env python3
"""Split synthetic 'A_B' co-billed artists into real per-artist credits.

A release imported with a combined credit creates a fake artist row such as
'陶喆_蔡健雅'. That row owns the track's primary artist field, so searching for
陶喆 surfaces a bogus '陶喆_蔡健雅' entry, while searching 蔡健雅 misses the very
song she sings on.

This resolves each half to its real artist row (exact name, then alias, then
script variant), points the track's primary artist at the album's real artist,
rewrites the credit as the two real artists, and finally removes the now-empty
fake rows. Safety: dry-run by default, refuses to delete anything whose parts
cannot all be resolved.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "velvet_data" / "library.db"

SPLIT = re.compile(r"\s*[_]\s*")

# Script-variant aliases the library is missing. chinese_variants.to_traditional
# is a no-op for common surnames (關, 張, 呂 are absent from its table), so the
# spellings a release can import with cannot be derived - they are listed here.
# Same convention as the existing 盧廣仲/卢广仲 pair.
VARIANT_ALIASES = {
    216: ["关诗敏"],
    262: ["Tanya Chua"],
}


def resolve(con, part: str) -> tuple[int, str] | None:
    """Find the real artist row for one half of a co-billed name."""
    part = part.strip()
    if not part:
        return None
    row = con.execute("SELECT id,name FROM artists WHERE name=?", (part,)).fetchone()
    if row:
        return row["id"], "exact"
    row = con.execute(
        "SELECT artist_id, name FROM artist_aliases WHERE name=?", (part,)).fetchone()
    if row:
        return row["artist_id"], "alias"
    for aid, variants in VARIANT_ALIASES.items():
        if part in variants:
            return aid, f"pending alias {part}"
        live = con.execute("SELECT name FROM artists WHERE id=?", (aid,)).fetchone()
        if live and part == live["name"]:
            return aid, "exact"
    from chinese_variants import to_simplified, to_traditional
    for variant in (to_simplified(part), to_traditional(part)):
        if variant == part:
            continue
        row = con.execute("SELECT id,name FROM artists WHERE name=?", (variant,)).fetchone()
        if row:
            return row["id"], f"script->{variant}"
        row = con.execute(
            "SELECT artist_id, name FROM artist_aliases WHERE name=?", (variant,)).fetchone()
        if row:
            return row["artist_id"], f"alias script->{variant}"
    return None


def plan(con) -> tuple[list[dict], list[str]]:
    steps: list[dict] = []
    problems: list[str] = []
    synthetics = [r for r in con.execute(
        "SELECT id, name FROM artists") if SPLIT.search(r["name"] or "")]
    for row in synthetics:
        sid, name = row["id"], row["name"]
        parts = [p for p in SPLIT.split(name) if p.strip()]
        resolved = []
        for p in parts:
            got = resolve(con, p)
            if not got:
                problems.append(f"[{sid}] {name}: cannot resolve '{p}'")
            else:
                resolved.append({"part": p, "id": got[0], "how": got[1]})
        tracks = con.execute(
            "SELECT t.id, t.title, t.album_id, al.artist_id AS album_artist_id, "
            "       ar.name AS album_artist "
            "FROM tracks t JOIN albums al ON t.album_id = al.id "
            "LEFT JOIN artists ar ON ar.id = al.artist_id "
            "WHERE t.artist_id = ?", (sid,)).fetchall()
        credits = con.execute(
            "SELECT track_id FROM track_artists WHERE artist_id = ?", (sid,)).fetchall()
        if not tracks and not credits:
            problems.append(f"[{sid}] {name}: no rows reference it, review manually")
        steps.append({
            "id": sid, "name": name, "parts": resolved,
            "tracks": [dict(t) for t in tracks],
            "credits": [c["track_id"] for c in credits],
        })
    return steps, problems


def report(steps, problems) -> None:
    print("=== PLAN ===")
    for s in steps:
        print(f"\n[{s['id']}] {s['name']}")
        for p in s["parts"]:
            print(f"    {p['part']:<12} -> id {p['id']:<6} via {p['how']}")
        for t in s["tracks"]:
            new_primary = t["album_artist_id"]
            print(f"    track [{t['id']}] {t['title']!r}")
            print(f"        primary artist: {s['id']} -> {new_primary} ({t['album_artist']!r})")
        for tid in s["credits"]:
            print(f"    credit track_id {tid}: {s['id']} -> {[p['id'] for p in s['parts']]}")
        print(f"    then DELETE artist row {s['id']}")
    if problems:
        print("\n=== PROBLEMS ===")
        for p in problems:
            print(f"  ! {p}")
    else:
        print("\nno problems")


def apply(con, steps) -> dict:
    stats = {"tracks": 0, "credits": 0, "artists": 0}
    for s in steps:
        ids = [p["id"] for p in s["parts"]]
        for t in s["tracks"]:
            if t["album_artist_id"] == s["id"]:
                continue
            con.execute("UPDATE tracks SET artist_id=? WHERE id=?",
                        (t["album_artist_id"], t["id"]))
            stats["tracks"] += 1
        for tid in s["credits"]:
            for aid in ids:
                con.execute(
                    "INSERT OR IGNORE INTO track_artists(track_id, artist_id) VALUES (?,?)",
                    (tid, aid))
            con.execute("DELETE FROM track_artists WHERE track_id=? AND artist_id=?",
                        (tid, s["id"]))
            stats["credits"] += 1
        leftovers = con.execute(
            "SELECT (SELECT COUNT(*) FROM tracks WHERE artist_id=?) + "
            "(SELECT COUNT(*) FROM track_artists WHERE artist_id=?) + "
            "(SELECT COUNT(*) FROM albums WHERE artist_id=?) + "
            "(SELECT COUNT(*) FROM artist_aliases WHERE artist_id=?)",
            (s["id"], s["id"], s["id"], s["id"])).fetchone()[0]
        if leftovers:
            raise RuntimeError(f"[{s['id']}] still referenced by {leftovers} rows; aborting")
        con.execute("DELETE FROM artists WHERE id=?", (s["id"],))
        stats["artists"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--backup", action="store_true", help="copy library.db first")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    steps, problems = plan(con)
    report(steps, problems)
    if not args.apply:
        print("\nDRY RUN - pass --apply to write")
        return 1

    blocking = [p for p in problems if "cannot resolve" in p]
    if blocking:
        print("\nABORT: unresolved names, nothing written")
        return 1

    if args.backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = DB.with_name(f"library.db.bak-{stamp}")
        shutil.copy2(DB, dest)
        print(f"\nbackup: {dest}")

    try:
        con.execute("BEGIN IMMEDIATE")
        added = 0
        for aid, variants in VARIANT_ALIASES.items():
            for nm in variants:
                owner = con.execute(
                    "SELECT artist_id FROM artist_aliases WHERE name=?", (nm,)).fetchone()
                if owner is not None and owner["artist_id"] != aid:
                    print(f"  ! alias '{nm}' already belongs to artist {owner['artist_id']}, skipped")
                    continue
                if con.execute("SELECT COUNT(*) FROM artist_aliases WHERE artist_id=? AND name=?",
                               (aid, nm)).fetchone()[0]:
                    continue
                con.execute("INSERT INTO artist_aliases(artist_id,name) VALUES (?,?)", (aid, nm))
                print(f"  + alias artist {aid} += {nm}")
                added += 1
        stats = apply(con, steps)
        con.commit()
    except Exception as exc:
        con.rollback()
        print(f"\nROLLED BACK: {exc}")
        return 1

    print(f"\naliases added: {added}")
    from library_search_index import refresh_track_search_index
    print(f"search index refreshed: {refresh_track_search_index(con)} tracks")
    print(f"\nwrote: {stats}")
    print(f"integrity: {con.execute('PRAGMA integrity_check').fetchone()[0]}")
    print(f"fk violations: {len(con.execute('PRAGMA foreign_key_check').fetchall())}")
    print(f"remaining underscore names: "
          f"{sum(1 for r in con.execute('SELECT name FROM artists') if '_' in (r['name'] or ''))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())