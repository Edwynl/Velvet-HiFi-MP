from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Callable, Optional


def sort_name(name: str) -> str:
    for article in ("The ", "A ", "An "):
        if name.startswith(article):
            return name[len(article):] + ", " + article.rstrip()
    return name


def split_artists(artist_string: str) -> list[str]:
    if not artist_string:
        return ["Unknown Artist"]
    separators = [
        r"\s*&\s*",
        r"\s*/\s*",
        r"\s*、\s*",
        r"\s*feat\.\s*",
        r"\s*ft\.\s*",
        r"\s+and\s+",
        r"\s*,\s*",
    ]
    pattern = "|".join(separators)
    parts = re.split(pattern, artist_string, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def discover_audio_files(paths: list[str], audio_extensions: set[str]) -> list[str]:
    all_files: list[str] = []
    for root_path in paths:
        if not os.path.exists(root_path):
            continue
        for root, dirs, files in os.walk(root_path):
            dirs[:] = sorted(directory for directory in dirs if not directory.startswith("."))
            for name in sorted(files):
                if name.startswith("._") or name.startswith("."):
                    continue
                if Path(name).suffix.lower() in audio_extensions:
                    all_files.append(os.path.join(root, name))
    return all_files


def _store_file_state(db: sqlite3.Connection, track_id: int, stat: os.stat_result) -> None:
    """Invalidate prior hashes whenever a file revision changes."""
    db.execute(
        """
        INSERT INTO track_file_state (track_id, size_bytes, mtime_ns, sha256, hash_size, hash_mtime_ns)
        VALUES (?, ?, ?, NULL, NULL, NULL)
        ON CONFLICT(track_id) DO UPDATE SET
            size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
            sha256=CASE WHEN size_bytes=excluded.size_bytes AND mtime_ns=excluded.mtime_ns
                THEN sha256 ELSE NULL END,
            hash_size=CASE WHEN size_bytes=excluded.size_bytes AND mtime_ns=excluded.mtime_ns
                THEN hash_size ELSE NULL END,
            hash_mtime_ns=CASE WHEN size_bytes=excluded.size_bytes AND mtime_ns=excluded.mtime_ns
                THEN hash_mtime_ns ELSE NULL END
        """,
        (track_id, stat.st_size, stat.st_mtime_ns),
    )


def _update_existing_track(
    db: sqlite3.Connection,
    track_id: int,
    metadata: dict,
    file_path: str,
    albums_cache: dict[tuple[int, str], int],
    artists_cache: dict[str, int],
    *,
    find_folder_cover: Callable[[str], Optional[bytes]],
    save_cover: Callable[[bytes, str], str],
) -> None:
    """Refresh metadata without changing the track ID or deleting history."""
    artist_ids, primary_artist = _resolve_artist_ids(db, metadata, artists_cache)
    album_id = _resolve_album_id(
        db, metadata, file_path, primary_artist, albums_cache,
        find_folder_cover=find_folder_cover, save_cover=save_cover,
        update_existing_cover_if_missing=True,
    )
    if album_id is None:
        raise ValueError("Cannot resolve album for changed track")
    columns = (
        "album_id", "artist_id", "title", "composer", "work_title",
        "performer", "conductor", "ensemble", "track_number", "disc_number",
        "duration", "format", "sample_rate", "bit_depth", "channels",
        "bitrate", "file_size",
    )
    values = (
        album_id, primary_artist, metadata["title"], metadata["composer"],
        metadata["work_title"], metadata["performer"], metadata["conductor"],
        metadata["ensemble"], metadata["track_number"], metadata["disc_number"],
        metadata["duration"], metadata["format"], metadata["sample_rate"],
        metadata["bit_depth"], metadata["channels"], metadata["bitrate"],
        metadata.get("file_size") if metadata.get("file_size") is not None
        else os.path.getsize(file_path),
    )
    assignments = ", ".join(f"{column}=?" for column in columns)
    db.execute(f"UPDATE tracks SET {assignments} WHERE id=?", (*values, track_id))
    db.execute("DELETE FROM track_artists WHERE track_id=?", (track_id,))
    for artist_id in artist_ids:
        db.execute(
            "INSERT OR IGNORE INTO track_artists(track_id, artist_id) VALUES (?, ?)",
            (track_id, artist_id),
        )


def scan_music_dirs(
    db: sqlite3.Connection,
    music_dirs: list[str],
    *,
    audio_extensions: set[str],
    scan_batch_size: int,
    extract_metadata: Callable[[str], dict],
    find_folder_cover: Callable[[str], Optional[bytes]],
    save_cover: Callable[[bytes, str], str],
    set_total: Callable[[int], None],
    on_progress: Callable[[int, str], None],
    on_track_added: Callable[[], dict | None],
    on_track_skipped: Callable[[], dict | None],
    on_error: Callable[[str], None],
    on_track_updated: Callable[[], dict | None] | None = None,
    force_reparse: bool = False,
) -> dict:
    """Incremental scan: refresh changed metadata while retaining track identity.

    Existing tracks added before migration 9 receive a *baseline* stat on
    their first scan, rather than being reprocessed without a trustworthy old
    mtime. Future scans detect changed size/mtime. Missing/deleted files are
    intentionally not pruned; a removable drive may be temporarily offline.
    """
    all_files = discover_audio_files(music_dirs, audio_extensions)
    set_total(len(all_files))
    existing = {}
    for row in db.execute(
        """
        SELECT t.id, t.file_path, s.size_bytes, s.mtime_ns
        FROM tracks t LEFT JOIN track_file_state s ON s.track_id=t.id
        """
    ):
        track_id, path, size, mtime = tuple(row)
        existing[path] = (track_id, size, mtime)

    # Backfill old multi-artist relations only where actually missing.
    needs_backfill = {
        row[0] for row in db.execute(
            "SELECT t.id FROM tracks t LEFT JOIN track_artists ta ON ta.track_id=t.id "
            "WHERE ta.track_id IS NULL"
        )
    }
    artists_cache: dict[str, int] = {}
    albums_cache: dict[tuple[int, str], int] = {}
    tracks_updated = 0
    pending_changes = 0
    commit_every = max(int(scan_batch_size), 1)

    for index, file_path in enumerate(all_files, start=1):
        on_progress(index, os.path.basename(file_path))
        try:
            file_stat = os.stat(file_path)
        except OSError as exc:
            on_error(f"File unavailable while scanning {file_path}: {exc}")
            continue

        known = existing.get(file_path)
        if known:
            track_id, old_size, old_mtime = known
            if (not force_reparse and old_size is not None and old_size == file_stat.st_size
                    and old_mtime == file_stat.st_mtime_ns):
                on_track_skipped()
                continue
            # Atomic per-file metadata replacement (no partial artist/album links).
            db.execute("SAVEPOINT velvet_scan_file")
            try:
                if (old_size is None or old_mtime is None) and not force_reparse:
                    # Legacy rows are not blindly reprocessed; establish baseline.
                    if track_id in needs_backfill:
                        _backfill_track_artists(db, file_path)
                else:
                    metadata = extract_metadata(file_path)
                    _update_existing_track(
                        db, track_id, metadata, file_path, albums_cache,
                        artists_cache, find_folder_cover=find_folder_cover,
                        save_cover=save_cover,
                    )
                _store_file_state(db, track_id, file_stat)
                db.execute("RELEASE SAVEPOINT velvet_scan_file")
            except Exception as exc:
                db.execute("ROLLBACK TO SAVEPOINT velvet_scan_file")
                db.execute("RELEASE SAVEPOINT velvet_scan_file")
                on_error(f"Incremental metadata refresh failed for {file_path}: {exc}")
                continue
            if (old_size is None or old_mtime is None) and not force_reparse:
                on_track_skipped()
            else:
                tracks_updated += 1
                if on_track_updated is not None:
                    on_track_updated()
            pending_changes += 1
        else:
            db.execute("SAVEPOINT velvet_scan_file")
            try:
                metadata = extract_metadata(file_path)
                individual_artist_ids, primary_artist_id = _resolve_artist_ids(
                    db, metadata, artists_cache,
                )
                album_id = _resolve_album_id(
                    db, metadata, file_path, primary_artist_id, albums_cache,
                    find_folder_cover=find_folder_cover, save_cover=save_cover,
                    update_existing_cover_if_missing=False,
                )
                if album_id is None:
                    raise ValueError("No album ID resolved")
                _insert_track_and_artists(
                    db, metadata, file_path, album_id,
                    primary_artist_id, individual_artist_ids,
                )
                new_id = db.execute(
                    "SELECT id FROM tracks WHERE file_path=?", (file_path,)
                ).fetchone()
                if new_id is None:
                    raise ValueError("Failed to insert new track")
                _store_file_state(db, new_id[0], file_stat)
                db.execute("RELEASE SAVEPOINT velvet_scan_file")
            except Exception as exc:
                db.execute("ROLLBACK TO SAVEPOINT velvet_scan_file")
                db.execute("RELEASE SAVEPOINT velvet_scan_file")
                on_error(f"Error adding track {file_path}: {exc}")
                continue
            on_track_added()
            existing[file_path] = (new_id[0], file_stat.st_size, file_stat.st_mtime_ns)
            pending_changes += 1

        if pending_changes >= commit_every:
            db.commit()
            pending_changes = 0

    _recount_album_totals(db)
    db.commit()
    return {"discovered_files": len(all_files), "tracks_updated": tracks_updated}


def refresh_folder_records(
    db: sqlite3.Connection,
    folder_path: str,
    *,
    audio_extensions: set[str],
    extract_metadata: Callable[[str], dict],
    find_folder_cover: Callable[[str], Optional[bytes]],
    save_cover: Callable[[bytes, str], str],
    set_total: Callable[[int], None],
    on_progress: Callable[[int, str], None],
    on_track_added: Callable[[], dict | None],
    on_error: Callable[[str], None],
    scan_batch_size: int = 50,
    on_track_updated: Callable[[], dict | None] | None = None,
) -> dict:
    """Force metadata re-read while retaining track, playlist and history IDs.

    Unlike the legacy delete/reinsert path, a folder refresh never removes
    records for temporarily missing or offline audio files.
    """
    result = scan_music_dirs(
        db, [folder_path], audio_extensions=audio_extensions,
        scan_batch_size=scan_batch_size, extract_metadata=extract_metadata,
        find_folder_cover=find_folder_cover, save_cover=save_cover,
        set_total=set_total, on_progress=on_progress,
        on_track_added=on_track_added, on_track_skipped=lambda: None,
        on_track_updated=on_track_updated, on_error=on_error,
        force_reparse=True,
    )
    return {"deleted_tracks": 0, **result}



def _backfill_track_artists(db: sqlite3.Connection, file_path: str) -> None:
    track_info = db.execute(
        "SELECT id, artist_id FROM tracks WHERE file_path=?",
        (file_path,),
    ).fetchone()
    if not track_info:
        return

    track_id = track_info["id"] if isinstance(track_info, sqlite3.Row) else track_info[0]
    artist_id = track_info["artist_id"] if isinstance(track_info, sqlite3.Row) else track_info[1]
    artist_name_row = db.execute("SELECT name FROM artists WHERE id=?", (artist_id,)).fetchone()
    if not artist_name_row:
        return

    artist_name = artist_name_row["name"] if isinstance(artist_name_row, sqlite3.Row) else artist_name_row[0]
    for name in split_artists(artist_name):
        db.execute(
            "INSERT OR IGNORE INTO artists (name, sort_name) VALUES (?, ?)",
            (name, sort_name(name)),
        )
        individual_artist_row = db.execute("SELECT id FROM artists WHERE name=?", (name,)).fetchone()
        if individual_artist_row:
            individual_artist_id = individual_artist_row["id"] if isinstance(individual_artist_row, sqlite3.Row) else individual_artist_row[0]
            db.execute(
                "INSERT OR IGNORE INTO track_artists (track_id, artist_id) VALUES (?, ?)",
                (track_id, individual_artist_id),
            )


def _resolve_artist_ids(
    db: sqlite3.Connection,
    metadata: dict,
    artists_cache: dict[str, int],
) -> tuple[list[int], int]:
    raw_artist = (metadata["album_artist"] or metadata["artist"]).strip() or "Unknown Artist"
    artist_names = split_artists(raw_artist)
    individual_artist_ids: list[int] = []

    for artist_name in artist_names:
        if artist_name not in artists_cache:
            existing = db.execute("SELECT id FROM artists WHERE name=?", (artist_name,)).fetchone()
            if existing:
                artist_id = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
            else:
                db.execute(
                    "INSERT INTO artists (name, sort_name) VALUES (?, ?)",
                    (artist_name, sort_name(artist_name)),
                )
                artist_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            artists_cache[artist_name] = artist_id
        individual_artist_ids.append(artists_cache[artist_name])

    return individual_artist_ids, individual_artist_ids[0]


def _resolve_album_id(
    db: sqlite3.Connection,
    metadata: dict,
    file_path: str,
    primary_artist_id: int,
    albums_cache: dict[tuple[int, str], int],
    *,
    find_folder_cover: Callable[[str], Optional[bytes]],
    save_cover: Callable[[bytes, str], str],
    update_existing_cover_if_missing: bool,
) -> int | None:
    album_key = (primary_artist_id, metadata["album"])
    if album_key in albums_cache:
        return albums_cache[album_key]

    cover_data = metadata["cover_data"] or find_folder_cover(file_path)
    cover_path = save_cover(cover_data, f"{primary_artist_id}_{metadata['album']}") if cover_data else None

    existing = db.execute(
        "SELECT id FROM albums WHERE artist_id=? AND title=?",
        (primary_artist_id, metadata["album"]),
    ).fetchone()
    if existing:
        album_id = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
        if update_existing_cover_if_missing and cover_path:
            current = db.execute("SELECT cover_path FROM albums WHERE id=?", (album_id,)).fetchone()
            current_cover = current["cover_path"] if isinstance(current, sqlite3.Row) else current[0]
            if not current_cover:
                db.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_path, album_id))
    else:
        db.execute(
            """
            INSERT INTO albums (artist_id, title, year, genre, label, cover_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                primary_artist_id,
                metadata["album"],
                metadata["year"],
                metadata["genre"],
                metadata["label"],
                cover_path,
            ),
        )
        album_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    albums_cache[album_key] = album_id
    return album_id


def _insert_track_and_artists(
    db: sqlite3.Connection,
    metadata: dict,
    file_path: str,
    album_id: int,
    primary_artist_id: int,
    individual_artist_ids: list[int],
) -> None:
    file_size = metadata.get("file_size")
    if file_size is None:
        file_size = os.path.getsize(file_path)

    db.execute(
        """
        INSERT OR IGNORE INTO tracks
        (album_id, artist_id, title, composer, work_title, performer, conductor, ensemble, track_number, disc_number,
         duration, file_path, format, sample_rate, bit_depth, channels, bitrate, file_size)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            album_id,
            primary_artist_id,
            metadata["title"],
            metadata["composer"],
            metadata["work_title"],
            metadata["performer"],
            metadata["conductor"],
            metadata["ensemble"],
            metadata["track_number"],
            metadata["disc_number"],
            metadata["duration"],
            file_path,
            metadata["format"],
            metadata["sample_rate"],
            metadata["bit_depth"],
            metadata["channels"],
            metadata["bitrate"],
            file_size,
        ),
    )

    track_row = db.execute("SELECT id FROM tracks WHERE file_path=?", (file_path,)).fetchone()
    if not track_row:
        return
    track_id = track_row["id"] if isinstance(track_row, sqlite3.Row) else track_row[0]

    for artist_id in individual_artist_ids:
        db.execute(
            "INSERT OR IGNORE INTO track_artists (track_id, artist_id) VALUES (?, ?)",
            (track_id, artist_id),
        )


def _recount_album_totals(db: sqlite3.Connection) -> None:
    db.execute(
        """
        UPDATE albums SET total_tracks = (
            SELECT COUNT(*) FROM tracks WHERE tracks.album_id = albums.id
        )
        """
    )
