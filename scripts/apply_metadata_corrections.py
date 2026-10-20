"""Apply a reviewed metadata correction list to library.db (and file tags).

The list is a UTF-8 CSV with the columns

    kind,id,current,proposed,merge_ids,aliases,source,check,approve

kind is ``artist``, ``album`` or ``track``; only rows whose ``approve``
column is Y/yes/1/是 are applied. An artist row renames artist ``id`` to
``proposed`` and merges the space-separated ``merge_ids`` into it; every
replaced name stays as an alias. Album and track rows retitle that row when
its title still equals ``current``.

The optional ``aliases`` column (pipe-separated) attaches further names to
the same artist without renaming it. This is the path for a singer who is
already stored under a Chinese name but also records under an English or a
romanised one, and for a singer stored under their English name whose
Chinese name is only known as an alias. Every attached name is also mapped
back to the display name when the files' tags are rewritten, so a re-import
on another machine produces one artist row instead of several.

Nothing is written without --apply. Before writing, the database is copied
with the SQLite backup API; with --write-tags every changed file's old tag
values go to a JSONL undo log first. Stop VELVET while this runs.

    python scripts/apply_metadata_corrections.py corrections.csv --db velvet_data/library.db
    python scripts/apply_metadata_corrections.py corrections.csv --db ... --apply --write-tags
    python scripts/apply_metadata_corrections.py --undo-tags velvet_data/metadata_cleanup_<ts>.tags.backup.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from artist_aliases import add_alias, merge_artists, rename_artist  # noqa: E402
from artist_alias_provenance import mark_alias_source  # noqa: E402
from db_schema import apply_migrations  # noqa: E402
from path_compat import filesystem_path  # noqa: E402

APPROVED = {"y", "yes", "1", "true", "是", "✓"}
TAG_KEYS = ("title", "album", "artist", "albumartist")
_ID3_FRAMES = {"title": "TIT2", "album": "TALB", "artist": "TPE1", "albumartist": "TPE2"}
# scanner_service.split_artists separators (plus ';'), kept in the output.
_ARTIST_SEPARATORS = re.compile(r"(\s*&\s*|\s*/\s*|\s*、\s*|\s*feat\.\s*|\s*ft\.\s*|\s+and\s+|\s*,\s*|\s*;\s*)", re.IGNORECASE)


def read_corrections(path: Path) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {"artist": [], "album": [], "track": []}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("approve") or "").strip().lower() not in APPROVED:
                continue
            kind = row["kind"].strip()
            if kind not in rows:
                raise ValueError(f"unknown kind {kind!r}")
            row["id"] = int(row["id"])
            row["merge_ids"] = [int(v) for v in (row.get("merge_ids") or "").split()]
            row["proposed"] = (row.get("proposed") or "").strip()
            row["alias_names"] = [
                name.strip()
                for name in (row.get("aliases") or "").split("|")
                if name.strip()
            ]
            rows[kind].append(row)
    return rows


def backup_database(db_path: Path, out_path: Path) -> None:
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(out_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def apply_to_database(db: sqlite3.Connection, corrections: dict[str, list[dict]]) -> dict:
    """Apply corrections in the open transaction; returns what changed.

    ``renamed_names`` maps every replaced or attached name to the display
    name, so file tags can be rewritten the same way.
    """
    report: dict = {"artists": [], "albums": [], "tracks": [], "skipped": [],
                    "renamed_names": {}}
    for row in corrections["artist"]:
        artist = db.execute("SELECT name FROM artists WHERE id = ?", (row["id"],)).fetchone()
        if artist is None or artist[0] != row["current"]:
            report["skipped"].append({**row, "reason": "artist changed since the list was made"})
            continue
        old_names = [artist[0]]
        missing_merges = []
        for merge_id in row["merge_ids"]:
            merged = db.execute("SELECT name FROM artists WHERE id = ?", (merge_id,)).fetchone()
            if merged is not None:
                old_names.append(merged[0])
            else:
                # The row was already folded into another one, most likely by
                # an earlier run of this list. Merging it again would fail.
                missing_merges.append(merge_id)
        if missing_merges:
            report["skipped"].append({
                **row, "reason": f"already merged: {missing_merges}"})
            continue
        target_id = row["id"]
        # merge_artists also folds the target's same-titled albums, so it
        # runs even when there is nothing to merge.
        target_id = merge_artists(db, row["id"], row["merge_ids"])["artist_id"]
        # An empty ``proposed`` keeps the stored name: the row exists to
        # attach aliases, not to rename.
        proposed = row["proposed"] or row["current"]
        if proposed != row["current"]:
            target_id = rename_artist(db, target_id, proposed)["artist_id"]
        display = db.execute(
            "SELECT name FROM artists WHERE id = ?", (target_id,)
        ).fetchone()[0]

        attached: list[str] = []
        for name in row["alias_names"]:
            if name.casefold() == display.casefold():
                continue
            if add_alias(db, target_id, name):
                attached.append(name)
        for name in attached:
            # The alias was checked against a source before it reached this
            # list, so record where it came from instead of leaving it
            # unprovenanced for the legacy audit.
            try:
                mark_alias_source(
                    db, target_id, name, "user_confirmed",
                    evidence=str(row.get("source") or "")[:500],
                )
            except (sqlite3.Error, ValueError):
                pass
        for name in old_names + attached:
            if name.casefold() != display.casefold():
                report["renamed_names"][name] = display
        report["artists"].append({
            "id": target_id, "name": display, "from": old_names,
            "aliases_added": attached,
        })

    for kind, table in (("album", "albums"), ("track", "tracks")):
        for row in corrections[kind]:
            current = db.execute(f"SELECT title FROM {table} WHERE id = ?", (row["id"],)).fetchone()
            if current is None or current[0] != row["current"]:
                report["skipped"].append({**row, "reason": f"{kind} changed since the list was made"})
                continue
            db.execute(f"UPDATE {table} SET title = ? WHERE id = ?", (row["proposed"], row["id"]))
            report[f"{kind}s"].append({"id": row["id"], "from": row["current"], "to": row["proposed"]})
    return report


def rewrite_artist_value(value: str, renamed: dict[str, str]) -> str:
    folded = {name.casefold(): new for name, new in renamed.items()}
    whole = folded.get(value.strip().casefold())
    if whole is not None:
        return whole
    parts = _ARTIST_SEPARATORS.split(value)
    return "".join(
        part if index % 2 else folded.get(part.strip().casefold(), part)
        for index, part in enumerate(parts)
    )


def planned_tag_changes(db: sqlite3.Connection, report: dict) -> dict[str, dict[str, str]]:
    """file path -> {tag key: new value} for title/album changes; files of
    renamed artists get an empty entry (their artist tags are rewritten)."""
    wanted: dict[str, dict[str, str]] = {}
    for change in report["tracks"]:
        row = db.execute("SELECT file_path FROM tracks WHERE id = ?", (change["id"],)).fetchone()
        if row:
            wanted.setdefault(row[0], {})["title"] = change["to"]
    for change in report["albums"]:
        for (path,) in db.execute("SELECT file_path FROM tracks WHERE album_id = ?", (change["id"],)):
            wanted.setdefault(path, {})["album"] = change["to"]
    if report["renamed_names"]:
        artist_ids = [entry["id"] for entry in report["artists"]]
        marks = ",".join("?" * len(artist_ids))
        for (path,) in db.execute(
            f"""
            SELECT t.file_path FROM tracks t WHERE t.artist_id IN ({marks})
            UNION
            SELECT t.file_path FROM tracks t JOIN track_artists ta ON ta.track_id = t.id
            WHERE ta.artist_id IN ({marks})
            """,
            artist_ids * 2,
        ):
            wanted.setdefault(path, {})
    return wanted


class TagFile:
    """Read/write title, album, artist and albumartist of one file."""

    def __init__(self, path: str):
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3

        self.path = path
        self.audio = MutagenFile(filesystem_path(path), easy=True)
        if self.audio is None:
            raise ValueError("unsupported file")
        if self.audio.tags is None:
            self.audio.add_tags()
        self.id3 = isinstance(self.audio.tags, ID3)

    def get(self, key: str) -> list[str]:
        if self.id3:
            frame = self.audio.tags.get(_ID3_FRAMES[key])
            return [str(v) for v in frame.text] if frame is not None else []
        return [str(v) for v in self.audio.tags.get(key, [])]

    def set(self, key: str, values: list[str]) -> None:
        if self.id3:
            from mutagen.id3 import Frames

            frame_id = _ID3_FRAMES[key]
            self.audio.tags.delall(frame_id)
            if values:
                self.audio.tags.add(Frames[frame_id](encoding=3, text=values))
        elif values:
            self.audio.tags[key] = values
        elif key in self.audio.tags:
            del self.audio.tags[key]

    def save(self) -> None:
        self.audio.save()


def write_tags(wanted: dict[str, dict[str, str]], renamed: dict[str, str], backup_log: Path) -> dict:
    stats = {"written": 0, "unchanged": 0, "failed": []}
    with backup_log.open("a", encoding="utf-8") as log:
        for path, fields in sorted(wanted.items()):
            try:
                tags = TagFile(path)
                changes = []
                for key in TAG_KEYS:
                    old = tags.get(key)
                    new = list(old)
                    if key in fields:
                        # The row's title came from this file, so the tag
                        # takes the corrected title even if it was spelled
                        # differently (ad marks the scanner strips, etc.).
                        new = [fields[key]]
                    if key in ("artist", "albumartist") and renamed:
                        new = [rewrite_artist_value(value, renamed) for value in new]
                    if new != old:
                        changes.append([key, old, new])
                if not changes:
                    stats["unchanged"] += 1
                    continue
                log.write(json.dumps({"path": path, "changes": changes}, ensure_ascii=False) + "\n")
                log.flush()
                for key, _old, new in changes:
                    tags.set(key, new)
                tags.save()
                stats["written"] += 1
            except Exception as exc:  # report and keep going; the log has what was written
                stats["failed"].append({"path": path, "error": str(exc)})
    return stats


def undo_tags(backup_log: Path) -> dict:
    stats = {"restored": 0, "failed": []}
    entries = [json.loads(line) for line in backup_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry in reversed(entries):
        try:
            tags = TagFile(entry["path"])
            for key, old, _new in entry["changes"]:
                tags.set(key, old)
            tags.save()
            stats["restored"] += 1
        except Exception as exc:
            stats["failed"].append({"path": entry["path"], "error": str(exc)})
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corrections", nargs="?", type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "velvet_data" / "library.db")
    parser.add_argument("--apply", action="store_true", help="write the database (default: dry run)")
    parser.add_argument("--write-tags", action="store_true", help="also rewrite the files' tags")
    parser.add_argument("--out-dir", type=Path, help="where backups and reports go (default: next to the db)")
    parser.add_argument("--undo-tags", type=Path, help="restore tags from a .tags.backup.jsonl log")
    args = parser.parse_args(argv)

    if args.undo_tags:
        print(json.dumps(undo_tags(args.undo_tags), ensure_ascii=False, indent=1))
        return 0
    if not args.corrections:
        parser.error("corrections CSV is required")

    corrections = read_corrections(args.corrections)
    out_dir = args.out_dir or args.db.parent
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = out_dir / f"metadata_cleanup_{stamp}"

    if args.apply:
        backup_path = out_dir / f"library.backup-before-metadata-cleanup-{stamp}.db"
        backup_database(args.db, backup_path)
        print(f"database backup: {backup_path}")

    db = sqlite3.connect(args.db, timeout=30)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = sqlite3.Row
        apply_migrations(db)
        db.row_factory = None
        db.execute("BEGIN IMMEDIATE")
        report = apply_to_database(db, corrections)
        wanted = planned_tag_changes(db, report)
        if not args.apply:
            db.rollback()
            print(json.dumps({
                "dry_run": True,
                "artists": len(report["artists"]), "albums": len(report["albums"]),
                "tracks": len(report["tracks"]), "skipped": report["skipped"],
                "files_to_check": len(wanted),
            }, ensure_ascii=False, indent=1))
            return 0
        db.commit()
    finally:
        db.close()

    report["files_to_check"] = len(wanted)
    if args.write_tags:
        report["tags"] = write_tags(wanted, report["renamed_names"], Path(f"{prefix}.tags.backup.jsonl"))
    Path(f"{prefix}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report: {prefix}.report.json")
    print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in report.items()},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
