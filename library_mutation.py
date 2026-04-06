from __future__ import annotations

import sqlite3

from fastapi import HTTPException


def toggle_album_favorite(db: sqlite3.Connection, album_id: int) -> dict:
    album = db.execute("SELECT is_favorite FROM albums WHERE id=?", (album_id,)).fetchone()
    if not album:
        raise HTTPException(404, "Album not found")

    current_value = album["is_favorite"] if isinstance(album, sqlite3.Row) else album[0]
    new_status = 1 if current_value == 0 else 0
    db.execute("UPDATE albums SET is_favorite=? WHERE id=?", (new_status, album_id))
    db.commit()
    return {"success": True, "is_favorite": new_status}


def fetch_playlists(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT p.*,
               COUNT(pt.track_id) as track_count
        FROM playlists p
        LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
        GROUP BY p.id
        ORDER BY p.updated_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def create_playlist(db: sqlite3.Connection, name: str, description: str) -> dict:
    db.execute(
        "INSERT INTO playlists (name, description) VALUES (?, ?)",
        (name, description),
    )
    db.commit()
    playlist_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": playlist_id, "name": name, "description": description}


def fetch_playlist_detail(db: sqlite3.Connection, playlist_id: int) -> dict:
    playlist = db.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
    if not playlist:
        raise HTTPException(404, "Playlist not found")

    tracks = db.execute(
        """
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id,
               pt.position, pt.added_at
        FROM playlist_tracks pt
        JOIN tracks t ON pt.track_id = t.id
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        WHERE pt.playlist_id = ?
        ORDER BY pt.position
        """,
        (playlist_id,),
    ).fetchall()
    return {**dict(playlist), "tracks": [dict(track) for track in tracks]}


def add_track_to_playlist(db: sqlite3.Connection, playlist_id: int, track_id: int) -> dict:
    max_pos = db.execute(
        "SELECT MAX(position) FROM playlist_tracks WHERE playlist_id=?",
        (playlist_id,),
    ).fetchone()[0] or 0

    try:
        db.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
            (playlist_id, track_id, max_pos + 1),
        )
        _touch_playlist(db, playlist_id)
        db.commit()
        return {"success": True}
    except sqlite3.IntegrityError:
        db.rollback()
        return {"success": False}


def remove_track_from_playlist(db: sqlite3.Connection, playlist_id: int, track_id: int) -> dict:
    db.execute(
        "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
        (playlist_id, track_id),
    )
    _resequence_playlist_tracks(db, playlist_id)
    _touch_playlist(db, playlist_id)
    db.commit()
    return {"success": True}


def delete_playlist(db: sqlite3.Connection, playlist_id: int) -> dict:
    db.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
    db.commit()
    return {"success": True}


def update_playlist(
    db: sqlite3.Connection,
    playlist_id: int,
    name: str | None = None,
    description: str | None = None,
) -> dict:
    if name is not None:
        db.execute(
            "UPDATE playlists SET name=?, updated_at=strftime('%s', 'now') WHERE id=?",
            (name, playlist_id),
        )
    if description is not None:
        db.execute(
            "UPDATE playlists SET description=?, updated_at=strftime('%s', 'now') WHERE id=?",
            (description, playlist_id),
        )
    if name is None and description is None:
        db.execute(
            "UPDATE playlists SET updated_at=strftime('%s', 'now') WHERE id=?",
            (playlist_id,),
        )
    db.commit()
    return {"success": True}


def _touch_playlist(db: sqlite3.Connection, playlist_id: int) -> None:
    db.execute(
        "UPDATE playlists SET updated_at=strftime('%s', 'now') WHERE id=?",
        (playlist_id,),
    )


def _resequence_playlist_tracks(db: sqlite3.Connection, playlist_id: int) -> None:
    rows = db.execute(
        """
        SELECT id
        FROM playlist_tracks
        WHERE playlist_id = ?
        ORDER BY position, added_at, id
        """,
        (playlist_id,),
    ).fetchall()

    for index, row in enumerate(rows, start=1):
        track_row_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        db.execute(
            "UPDATE playlist_tracks SET position=? WHERE id=?",
            (index, track_row_id),
        )
