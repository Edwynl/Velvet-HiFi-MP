from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from fastapi import HTTPException


def fetch_artist_detail_summary(db: sqlite3.Connection, artist_id: int) -> dict:
    artist = db.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()
    if not artist:
        raise HTTPException(404, "Artist not found")

    stats = db.execute(
        """
        SELECT
            COUNT(DISTINCT t.album_id) as album_count,
            COUNT(DISTINCT t.id) as track_count,
            SUM(t.duration) as total_duration
        FROM tracks t
        JOIN track_artists ta ON t.id = ta.track_id
        WHERE ta.artist_id = ?
        """,
        (artist_id,),
    ).fetchone()

    genres = db.execute(
        """
        SELECT genre, COUNT(*) as count
        FROM albums
        WHERE artist_id = ? AND genre IS NOT NULL AND genre != ''
        GROUP BY genre
        ORDER BY count DESC
        LIMIT 5
        """,
        (artist_id,),
    ).fetchall()

    play_count = db.execute(
        """
        SELECT COUNT(*) as plays
        FROM play_history ph
        JOIN tracks t ON ph.track_id = t.id
        JOIN track_artists ta ON t.id = ta.track_id
        WHERE ta.artist_id = ?
        """,
        (artist_id,),
    ).fetchone()

    result = dict(artist)
    result["album_count"] = stats["album_count"] or 0
    result["track_count"] = stats["track_count"] or 0
    result["total_duration_hours"] = round((stats["total_duration"] or 0) / 3600, 1)
    result["play_count"] = play_count["plays"] or 0 if play_count else 0
    result["genres"] = [dict(row) for row in genres]
    return result


def search_tracks(
    db: sqlite3.Connection,
    q: str,
    limit: int,
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> dict:
    variants = get_all_variants(q)
    conditions = []
    params = []
    for variant in variants:
        lower_variant = variant.lower()
        like_pattern = f"%{lower_variant}%"
        conditions.extend([
            "LOWER(t.title) LIKE ?",
            "LOWER(ar_all.name) LIKE ?",
            "LOWER(al.title) LIKE ?",
            "LOWER(COALESCE(t.composer, '')) LIKE ?",
            "LOWER(COALESCE(t.work_title, '')) LIKE ?",
            "LOWER(COALESCE(t.performer, '')) LIKE ?",
            "LOWER(COALESCE(t.conductor, '')) LIKE ?",
            "LOWER(COALESCE(t.ensemble, '')) LIKE ?",
        ])
        params.extend([like_pattern] * 8)

    where_clause = " OR ".join(conditions)
    tracks = db.execute(
        f"""
        SELECT DISTINCT t.id, t.title, t.duration, t.format, t.track_number,
               t.composer, t.work_title, t.performer, t.conductor, t.ensemble,
               ar_primary.name as artist_name, al.title as album_title,
               al.id as album_id, al.year
        FROM tracks t
        JOIN artists ar_primary ON t.artist_id = ar_primary.id
        JOIN albums al ON t.album_id = al.id
        LEFT JOIN track_artists ta ON t.id = ta.track_id
        LEFT JOIN artists ar_all ON ta.artist_id = ar_all.id
        WHERE {where_clause}
        ORDER BY (LOWER(t.title) = ?) DESC, LOWER(t.title)
        LIMIT ?
        """,
        (*params, q.lower(), limit),
    ).fetchall()

    matched_track_ids = {row["id"] for row in tracks}

    if len(tracks) < limit:
        all_tracks: list[sqlite3.Row] = db.execute(
            """
            SELECT DISTINCT t.id, t.title, t.duration, t.format, t.track_number,
                   t.composer, t.work_title, t.performer, t.conductor, t.ensemble,
                   ar_primary.name as artist_name, al.title as album_title,
                   al.id as album_id, al.year
            FROM tracks t
            JOIN artists ar_primary ON t.artist_id = ar_primary.id
            JOIN albums al ON t.album_id = al.id
            LEFT JOIN track_artists ta ON t.id = ta.track_id
            LEFT JOIN artists ar_all ON ta.artist_id = ar_all.id
            """
        ).fetchall()

        for track_row in all_tracks:
            track = dict(track_row)
            if track["id"] in matched_track_ids:
                continue
            for variant in variants:
                variant_simp = to_simplified(variant)
                variant_trad = to_traditional(variant)
                for field in [
                    track["title"],
                    track["artist_name"],
                    track["album_title"],
                    track.get("composer"),
                    track.get("work_title"),
                    track.get("performer"),
                    track.get("conductor"),
                    track.get("ensemble"),
                ]:
                    if not field:
                        continue
                    field_simp = to_simplified(field)
                    field_trad = to_traditional(field)
                    if (
                        variant in field
                        or variant_simp in field
                        or variant_trad in field
                        or field in variant
                        or field_simp in variant
                        or field_trad in variant
                    ):
                        matched_track_ids.add(track["id"])
                        break
                if track["id"] in matched_track_ids:
                    break

        matched_tracks = [dict(row) for row in all_tracks if row["id"] in matched_track_ids]
        matched_tracks.sort(key=lambda item: (0 if q.lower() in item["title"].lower() else 1, item["title"].lower()))
        return {"tracks": matched_tracks[:limit]}

    return {"tracks": [dict(row) for row in tracks]}


def fetch_genres(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT genre, COUNT(*) as count FROM albums
        WHERE genre IS NOT NULL AND genre != ''
        GROUP BY genre ORDER BY count DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_recently_added(db: sqlite3.Connection, limit: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT al.*, ar.name as artist_name,
               MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
        FROM albums al 
        JOIN artists ar ON al.artist_id=ar.id
        LEFT JOIN tracks t ON t.album_id = al.id
        GROUP BY al.id
        ORDER BY al.created_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_discover(db: sqlite3.Connection, limit: int) -> dict:
    favorites = db.execute(
        """
        SELECT al.*, ar.name as artist_name, COUNT(t.id) as track_count,
               MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        LEFT JOIN tracks t ON t.album_id = al.id
        WHERE al.is_favorite = 1
        GROUP BY al.id
        ORDER BY al.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    hi_res = db.execute(
        """
        SELECT al.*, ar.name as artist_name,
               MAX(COALESCE(t.sample_rate, 0)) as max_sample_rate,
               MAX(COALESCE(t.bit_depth, 0)) as max_bit_depth,
               MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        JOIN tracks t ON t.album_id = al.id
        GROUP BY al.id
        HAVING MAX(COALESCE(t.sample_rate, 0)) >= 96000
           OR MAX(COALESCE(t.bit_depth, 0)) >= 24
        ORDER BY max_sample_rate DESC, max_bit_depth DESC, al.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    rediscover = db.execute(
        """
        SELECT al.*, ar.name as artist_name,
               COUNT(ph.id) as play_count,
               MAX(ph.played_at) as last_played,
               MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        LEFT JOIN tracks t ON t.album_id = al.id
        LEFT JOIN play_history ph ON ph.track_id = t.id
        GROUP BY al.id
        HAVING COUNT(ph.id) > 0
        ORDER BY last_played ASC, play_count ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    trending_artists = db.execute(
        """
        SELECT ar.*,
               COUNT(ph.id) as play_count,
               COUNT(DISTINCT t.album_id) as album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN play_history ph ON ph.track_id = t.id
        GROUP BY ar.id
        ORDER BY play_count DESC, ar.sort_name
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    recent_tracks = db.execute(
        """
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id
        FROM play_history ph
        JOIN tracks t ON ph.track_id = t.id
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        ORDER BY ph.played_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return {
        "favorites": [dict(row) for row in favorites],
        "hi_res": [dict(row) for row in hi_res],
        "rediscover": [dict(row) for row in rediscover],
        "trending_artists": [dict(row) for row in trending_artists],
        "recent_tracks": [dict(row) for row in recent_tracks],
    }


def _get_radio_candidates(
    db: sqlite3.Connection,
    seed_artist_id: Optional[int],
    seed_album_id: Optional[int],
    seed_genre: str,
    seed_year: Optional[int],
    limit: int,
) -> list[dict]:
    rows = db.execute(
        """
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id,
               CASE
                 WHEN t.album_id = ? THEN 3
                 WHEN t.artist_id = ? THEN 2
                 WHEN COALESCE(al.genre, '') = COALESCE(?, '') AND COALESCE(al.genre, '') != '' THEN 1
                 ELSE 0
               END as affinity,
               ABS(COALESCE(al.year, 0) - COALESCE(?, 0)) as year_distance,
               al.is_favorite
        FROM tracks t
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        ORDER BY affinity DESC, al.is_favorite DESC, year_distance ASC,
                 COALESCE(t.sample_rate, 0) DESC, COALESCE(t.bit_depth, 0) DESC, RANDOM()
        LIMIT ?
        """,
        (seed_album_id or -1, seed_artist_id or -1, seed_genre or "", seed_year or 0, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _dedupe_radio_tracks(rows: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for row in rows:
        track_id = row["id"]
        if track_id in seen:
            continue
        seen.add(track_id)
        deduped.append(row)
    return deduped


def fetch_album_radio(db: sqlite3.Connection, album_id: int, limit: int) -> dict:
    album = db.execute(
        """
        SELECT al.*, ar.name as artist_name
        FROM albums al
        JOIN artists ar ON ar.id = al.artist_id
        WHERE al.id = ?
        """,
        (album_id,),
    ).fetchone()
    if not album:
        raise HTTPException(404, "Album not found")

    seed_tracks = db.execute(
        """
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id
        FROM tracks t
        JOIN artists ar ON ar.id = t.artist_id
        JOIN albums al ON al.id = t.album_id
        WHERE t.album_id = ?
        ORDER BY t.disc_number, t.track_number, t.title
        """,
        (album_id,),
    ).fetchall()

    candidate_rows = _get_radio_candidates(
        db,
        album["artist_id"],
        album_id,
        album["genre"],
        album["year"],
        limit + len(seed_tracks),
    )

    queue = _dedupe_radio_tracks([dict(row) for row in seed_tracks] + candidate_rows)
    return {"mode": "album_radio", "seed": dict(album), "tracks": queue[:limit]}


def fetch_artist_radio(db: sqlite3.Connection, artist_id: int, limit: int) -> dict:
    artist = db.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if not artist:
        raise HTTPException(404, "Artist not found")

    genre_row = db.execute(
        """
        SELECT genre
        FROM albums
        WHERE artist_id = ? AND genre IS NOT NULL AND genre != ''
        GROUP BY genre
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """,
        (artist_id,),
    ).fetchone()

    top_artist_tracks = db.execute(
        """
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id
        FROM tracks t
        JOIN artists ar ON ar.id = t.artist_id
        JOIN albums al ON al.id = t.album_id
        WHERE t.artist_id = ?
        ORDER BY COALESCE(t.sample_rate, 0) DESC, COALESCE(t.bit_depth, 0) DESC, t.title
        LIMIT ?
        """,
        (artist_id, min(limit, 20)),
    ).fetchall()

    candidate_rows = _get_radio_candidates(
        db,
        artist_id,
        None,
        genre_row["genre"] if genre_row else "",
        None,
        limit + len(top_artist_tracks),
    )

    queue = _dedupe_radio_tracks([dict(row) for row in top_artist_tracks] + candidate_rows)
    return {"mode": "artist_radio", "seed": dict(artist), "tracks": queue[:limit]}
