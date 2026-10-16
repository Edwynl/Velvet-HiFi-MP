"""Read-only, content-exact duplicate detector for existing music libraries.

Candidates are grouped by identical file size first. Only candidate files
are SHA-256 hashed in fixed memory. Cached hashes may rule a file out, but
every reported duplicate is re-hashed from its current bytes. No tracks,
playlists or audio files are deleted.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Callable


def find_exact_duplicates(
    db: sqlite3.Connection,
    library_roots: list[Path],
    *,
    max_files: int = 5000,
    max_groups: int = 100,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    if max_files < 2 or max_groups < 1:
        raise ValueError("max_files must be >= 2 and max_groups >= 1")
    roots = [Path(root).resolve() for root in library_roots]
    sizes = db.execute(
        """
        SELECT file_size FROM tracks
        WHERE file_size > 0
        GROUP BY file_size HAVING COUNT(*) > 1
        ORDER BY file_size DESC
        """
    ).fetchall()
    candidates = []
    truncated = False
    for (size,) in sizes:
        rows = db.execute(
            """
            SELECT t.id, t.file_path, t.file_size, s.sha256, s.hash_size, s.hash_mtime_ns
            FROM tracks t LEFT JOIN track_file_state s ON s.track_id=t.id
            WHERE t.file_size=? ORDER BY t.id
            """,
            (size,),
        ).fetchall()
        if len(candidates) + len(rows) > max_files:
            truncated = True
            break  # Do not process partial size groups; results remain exact.
        candidates.extend(rows)
    hashes: dict[tuple[int, str], list[dict]] = {}
    examined = 0
    cached = 0
    for row in candidates:
        track_id, file_path, stored_size, known_hash, hash_size, hash_mtime = tuple(row)
        examined += 1
        if on_progress:
            on_progress(examined, len(candidates), Path(file_path).name)
        try:
            resolved = Path(file_path).resolve(strict=True)
            if not resolved.is_file() or not any(
                resolved == root or resolved.is_relative_to(root) for root in roots
            ):
                continue
            before = resolved.stat()
            if before.st_size != stored_size:
                continue
            if known_hash and hash_size == before.st_size and hash_mtime == before.st_mtime_ns:
                digest, verified = known_hash, False
                cached += 1
            else:
                digest = _hash_unchanging_file(resolved, before)
                if digest is None:
                    continue  # Never present a hash for a concurrently changing file.
                _cache_hash(db, track_id, digest, before)
                verified = True
        except OSError:
            continue  # Offline removable drives are not duplicate evidence.
        hashes.setdefault((before.st_size, digest), []).append(
            {"track_id": track_id, "path": resolved, "verified": verified}
        )
    _confirm_groups(db, hashes)
    groups = [
        {
            "size_bytes": size, "sha256": digest, "count": len(members),
            "tracks": [
                {"track_id": m["track_id"], "file_path": str(m["path"])}
                for m in sorted(members, key=lambda m: m["track_id"])
            ],
        }
        for (size, digest), members in hashes.items() if len(members) > 1
    ]
    groups.sort(key=lambda g: (-g["size_bytes"], g["tracks"][0]["file_path"]))
    if len(groups) > max_groups:
        truncated = True
    groups = groups[:max_groups]
    return {
        "groups": groups,
        "groups_found": len(groups),
        "candidate_files": len(candidates),
        "files_examined": examined,
        "cached_hashes": cached,
        "truncated": truncated,
        "definition": "Byte-identical files (SHA-256); different encodings of the same recording are not matched.",
    }


def _hash_unchanging_file(path: Path, before: os.stat_result) -> str | None:
    """SHA-256 of the file, or None if its size/mtime changed while reading."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    after = path.stat()
    if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        return None
    return h.hexdigest()


def _cache_hash(db: sqlite3.Connection, track_id: int, digest: str, stat: os.stat_result) -> None:
    # Update hash metadata only for tracks already baselined by the normal
    # scanner. Never conceal a pending metadata change.
    # Commit each cache write at once: hashing a large library can take hours
    # and must not hold the SQLite write lock (history, playlists and scans
    # would fail with "database is locked").
    try:
        db.execute(
            """
            UPDATE track_file_state
            SET sha256=?, hash_size=?, hash_mtime_ns=?
            WHERE track_id=?
            """,
            (digest, stat.st_size, stat.st_mtime_ns, track_id),
        )
        db.commit()
    except sqlite3.OperationalError:
        db.rollback()  # Cache is optional; the digest is still valid.


def _confirm_groups(db: sqlite3.Connection, hashes: dict[tuple[int, str], list[dict]]) -> None:
    """Re-hash every cached member of a would-be duplicate group.

    A cached digest is trusted only while size and mtime match, but a tag
    editor can rewrite a file in place (FLAC padding) and restore its mtime.
    Cached digests therefore only rule files out; anything reported as a
    duplicate is confirmed from the bytes on disk now. Re-hashing can move a
    file into another group, so repeat until every reported member is verified.
    """
    while True:
        pending = [
            (key, member)
            for key, members in hashes.items() if len(members) > 1
            for member in members if not member["verified"]
        ]
        if not pending:
            return
        for key, member in pending:
            size, cached_digest = key
            hashes[key].remove(member)
            if not hashes[key]:
                del hashes[key]
            try:
                before = member["path"].stat()
                if before.st_size != size:
                    continue
                digest = _hash_unchanging_file(member["path"], before)
            except OSError:
                continue
            if digest is None:
                continue
            if digest != cached_digest:
                _cache_hash(db, member["track_id"], digest, before)
            member["verified"] = True
            hashes.setdefault((size, digest), []).append(member)
