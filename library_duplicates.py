"""Read-only, content-exact duplicate detector for existing music libraries.

Candidates are grouped by identical file size first. Only candidate files
are SHA-256 hashed in fixed memory, and byte hashes are compared only for
unchanged snapshots. No tracks, playlists or audio files are deleted.
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
                digest = known_hash
                cached += 1
            else:
                h = hashlib.sha256()
                with resolved.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        h.update(block)
                after = resolved.stat()
                if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                    continue  # Never present a hash for a concurrently changing file.
                digest = h.hexdigest()
                # Update hash metadata only for tracks already baselined by
                # the normal scanner. Never conceal a pending metadata change.
                # Commit each cache write at once: hashing a large library can
                # take hours and must not hold the SQLite write lock (history,
                # playlists and scans would fail with "database is locked").
                try:
                    db.execute(
                        """
                        UPDATE track_file_state
                        SET sha256=?, hash_size=?, hash_mtime_ns=?
                        WHERE track_id=?
                        """,
                        (digest, after.st_size, after.st_mtime_ns, track_id),
                    )
                    db.commit()
                except sqlite3.OperationalError:
                    db.rollback()  # Cache is optional; the digest is still valid.
            hashes.setdefault((before.st_size, digest), []).append(
                {"track_id": track_id, "file_path": str(resolved)}
            )
        except OSError:
            continue  # Offline removable drives are not duplicate evidence.
    groups = [
        {"size_bytes": size, "sha256": digest, "tracks": tracks, "count": len(tracks)}
        for (size, digest), tracks in hashes.items() if len(tracks) > 1
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
