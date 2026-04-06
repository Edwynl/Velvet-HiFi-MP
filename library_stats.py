from __future__ import annotations

import sqlite3


def fetch_library_stats(db: sqlite3.Connection, music_dir: str) -> dict:
    artists = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    albums = db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    duration = db.execute("SELECT SUM(duration) FROM tracks").fetchone()[0] or 0
    no_cover = db.execute("SELECT COUNT(*) FROM albums WHERE cover_path IS NULL").fetchone()[0]
    return {
        "artists": artists,
        "albums": albums,
        "tracks": tracks,
        "total_duration_hours": round(duration / 3600, 1),
        "albums_missing_cover": no_cover,
        "music_dir": music_dir,
    }


def fetch_genre_albums(
    db: sqlite3.Connection,
    genre: str,
    limit: int,
    offset: int,
    sort: str,
) -> dict:
    order_by = {
        "title": "al.title COLLATE NOCASE ASC",
        "artist": "ar.name COLLATE NOCASE ASC, al.title COLLATE NOCASE ASC",
        "added": "al.created_at DESC",
        "year": "al.year DESC, al.title COLLATE NOCASE ASC",
    }.get(sort, "al.year DESC, al.title COLLATE NOCASE ASC")

    total = db.execute(
        """
        SELECT COUNT(*)
        FROM albums
        WHERE genre = ?
        """,
        (genre,),
    ).fetchone()[0]

    rows = db.execute(
        f"""
        SELECT al.*, ar.name as artist_name
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        WHERE al.genre = ?
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        (genre, limit, offset),
    ).fetchall()

    return {"genre": genre, "albums": [dict(row) for row in rows], "total": total}


def fetch_history(db: sqlite3.Connection, limit: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT ph.id, ph.played_at, ph.duration_played,
               t.id as track_id, t.title, t.duration,
               ar.name as artist_name,
               al.title as album_title, al.id as album_id
        FROM play_history ph
        JOIN tracks t ON ph.track_id = t.id
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        ORDER BY ph.played_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_most_played(db: sqlite3.Connection, limit: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT t.id, t.title, t.duration, t.format,
               ar.name as artist_name,
               al.title as album_title, al.id as album_id,
               COUNT(ph.id) as play_count,
               MAX(ph.played_at) as last_played
        FROM play_history ph
        JOIN tracks t ON ph.track_id = t.id
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        GROUP BY t.id
        ORDER BY play_count DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_listening_stats(db: sqlite3.Connection) -> dict:
    total_plays = db.execute("SELECT COUNT(*) FROM play_history").fetchone()[0]
    total_time = db.execute("SELECT SUM(duration_played) FROM play_history").fetchone()[0] or 0
    today_plays = db.execute(
        "SELECT COUNT(*) FROM play_history WHERE date(played_at,'unixepoch') = date('now')"
    ).fetchone()[0]
    unique_artists = db.execute(
        "SELECT COUNT(DISTINCT t.artist_id) FROM play_history ph JOIN tracks t ON ph.track_id=t.id"
    ).fetchone()[0]
    return {
        "total_plays": total_plays,
        "total_hours_listened": round(total_time / 3600, 1),
        "plays_today": today_plays,
        "unique_artists_played": unique_artists,
    }
