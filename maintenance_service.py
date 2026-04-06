from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import HTTPException


def refresh_album_from_files(
    db: sqlite3.Connection,
    album_id: int,
    *,
    extract_metadata: Callable[[str], dict],
    write_album_cover: Callable[[int, bytes], str],
) -> dict:
    album = db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if not album:
        raise HTTPException(404, "Album not found")

    tracks = db.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY track_number", (album_id,)).fetchall()
    updated_tracks = 0
    errors: list[str] = []

    for track in tracks:
        track_data = dict(track)
        file_path = track_data.get("file_path")
        if not file_path or not os.path.exists(file_path):
            errors.append(f"File not found: {file_path}")
            continue

        try:
            meta = extract_metadata(file_path)
            db.execute(
                """
                UPDATE tracks SET
                    title = ?,
                    composer = ?,
                    work_title = ?,
                    performer = ?,
                    conductor = ?,
                    ensemble = ?,
                    track_number = ?,
                    disc_number = ?,
                    duration = ?,
                    sample_rate = ?,
                    channels = ?,
                    bit_depth = ?
                WHERE id = ?
                """,
                (
                    meta["title"],
                    meta["composer"],
                    meta["work_title"],
                    meta["performer"],
                    meta["conductor"],
                    meta["ensemble"],
                    meta["track_number"],
                    meta["disc_number"],
                    meta["duration"],
                    meta["sample_rate"],
                    meta["channels"],
                    meta["bit_depth"],
                    track_data["id"],
                ),
            )
            updated_tracks += 1

            if meta["cover_data"]:
                cover_path = write_album_cover(album_id, meta["cover_data"])
                db.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_path, album_id))
        except Exception as exc:
            errors.append(f"{os.path.basename(file_path)}: {exc}")

    db.commit()
    return {
        "success": True,
        "message": f"Refreshed {updated_tracks} tracks",
        "errors": errors if errors else None,
    }


def refresh_artist_from_files(
    db: sqlite3.Connection,
    artist_id: int,
    *,
    extract_metadata: Callable[[str], dict],
) -> dict:
    artist = db.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()
    if not artist:
        raise HTTPException(404, "Artist not found")

    artist_name = dict(artist)["name"]
    album_ids = [
        row["id"] if isinstance(row, sqlite3.Row) else row[0]
        for row in db.execute("SELECT id FROM albums WHERE artist_id=?", (artist_id,)).fetchall()
    ]

    total_updated = 0
    all_errors: list[str] = []
    new_artist_name = None

    for album_id in album_ids:
        tracks = db.execute("SELECT * FROM tracks WHERE album_id=?", (album_id,)).fetchall()
        for track in tracks:
            track_data = dict(track)
            file_path = track_data.get("file_path")
            if not file_path or not os.path.exists(file_path):
                all_errors.append(f"File not found: {file_path}")
                continue

            try:
                meta = extract_metadata(file_path)
                if new_artist_name is None and meta.get("artist"):
                    new_artist_name = meta["artist"]

                db.execute(
                    """
                    UPDATE tracks SET
                        title = ?,
                        composer = ?,
                        work_title = ?,
                        performer = ?,
                        conductor = ?,
                        ensemble = ?,
                        track_number = ?,
                        disc_number = ?
                    WHERE id = ?
                    """,
                    (
                        meta["title"],
                        meta["composer"],
                        meta["work_title"],
                        meta["performer"],
                        meta["conductor"],
                        meta["ensemble"],
                        meta["track_number"],
                        meta["disc_number"],
                        track_data["id"],
                    ),
                )
                total_updated += 1
            except Exception as exc:
                all_errors.append(f"{os.path.basename(file_path)}: {exc}")

    if new_artist_name and new_artist_name != artist_name:
        db.execute("UPDATE artists SET name=? WHERE id=?", (new_artist_name, artist_id))
        artist_name_changed = new_artist_name
    else:
        artist_name_changed = None

    db.commit()

    message = f"Refreshed {total_updated} tracks from {len(album_ids)} albums"
    if artist_name_changed:
        message = f"Artist renamed to '{artist_name_changed}'. {message}"

    return {
        "success": True,
        "message": message,
        "artist_name_changed": artist_name_changed,
        "errors": all_errors if all_errors else None,
    }


def rescan_local_album_covers(
    db: sqlite3.Connection,
    *,
    save_cover: Callable[[bytes, str], str],
) -> dict:
    albums = db.execute(
        """
        SELECT al.id, al.title, MIN(t.file_path) as file_path
        FROM albums al
        JOIN tracks t ON al.id = t.album_id
        WHERE al.cover_path IS NULL OR al.cover_path = ''
        GROUP BY al.id, al.title
        """
    ).fetchall()

    updated = 0
    for album in albums:
        album_id = album["id"]
        file_path = album["file_path"]
        folder = Path(file_path).parent
        local_cover = None

        for name in [
            "cover.jpg",
            "folder.jpg",
            "front.jpg",
            "albumart.jpg",
            "album.jpg",
            "cover.png",
            "folder.png",
        ]:
            path = folder / name
            if path.exists():
                local_cover = path
                break

        if local_cover:
            try:
                cover_data = local_cover.read_bytes()
                cover_path = save_cover(cover_data, f"album_{album_id}")
                db.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_path, album_id))
                updated += 1
            except Exception:
                continue

    db.commit()
    return {"updated": updated, "message": f"Updated {updated} covers from local files"}


async def enrich_missing_album_covers(
    *,
    get_db: Callable[[], sqlite3.Connection],
    http_client_factory: Callable[[], object],
    save_cover: Callable[[bytes, str], str],
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    limit: int = 50,
    logger=None,
) -> dict:
    db = None
    client = None
    processed = 0
    matched = 0
    updated = 0
    try:
        db = get_db()
        missing = db.execute(
            """
            SELECT al.id, al.title, ar.name as artist_name
            FROM albums al JOIN artists ar ON al.artist_id=ar.id
            WHERE al.cover_path IS NULL AND al.mbid IS NULL
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        db.close()
        db = None

        client = http_client_factory()
        for album in missing:
            processed += 1
            try:
                await sleep_fn(1.1)
                resp = await client.get(
                    "https://musicbrainz.org/ws/2/release",
                    params={
                        "query": f'release:"{album["title"]}" AND artist:"{album["artist_name"]}"',
                        "fmt": "json",
                        "limit": 1,
                    },
                )
                data = resp.json()
                releases = data.get("releases", [])
                if not releases:
                    continue

                mbid = releases[0].get("id")
                if not mbid:
                    continue

                db = get_db()
                db.execute("UPDATE albums SET mbid=? WHERE id=?", (mbid, album["id"]))
                db.commit()
                db.close()
                db = None
                matched += 1

                await sleep_fn(0.5)
                cover_resp = await client.get(
                    f"https://coverartarchive.org/release/{mbid}/front-250",
                    follow_redirects=True,
                )
                if getattr(cover_resp, "status_code", None) == 200 and getattr(cover_resp, "content", None):
                    cover_path = save_cover(cover_resp.content, f"mb_{mbid}")
                    db = get_db()
                    db.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_path, album["id"]))
                    db.commit()
                    db.close()
                    db = None
                    updated += 1
            except Exception as exc:
                if logger:
                    logger.debug(f"Cover fetch failed for {album['title']}: {exc}")
                if db:
                    try:
                        db.close()
                    except Exception:
                        pass
                    db = None
                continue
    finally:
        if client:
            await client.aclose()
        if db:
            try:
                db.close()
            except Exception:
                pass

    return {"processed": processed, "matched": matched, "updated": updated}
