from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from fastapi import HTTPException

from artist_aliases import fetch_aliases


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
    result["aliases"] = fetch_aliases(db, artist_id)
    return result


_TRACK_COLUMNS = """
    t.id, t.title, t.duration, t.format, t.track_number,
    t.composer, t.work_title, t.performer, t.conductor, t.ensemble,
    ar_primary.name AS artist_name, al.title AS album_title,
    al.id AS album_id, al.year
"""
_TRACK_JOINS = """
    FROM tracks t
    JOIN artists ar_primary ON t.artist_id = ar_primary.id
    JOIN albums al ON t.album_id = al.id
"""
def search_tracks(
    db: sqlite3.Connection,
    q: str,
    limit: int,
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> dict:
    """Global search using one OpenCC search key for all query paths.

    The callable arguments are retained for backward API compatibility; all
    production paths must use the identical converter for index and query.
    Exact raw spellings are included in the index, preventing loss when a
    phrase-level conversion changes how a substring is segmented.
    """
    from library_search_match import (
        ids_json,
        match_track_ids,
        normalize_query,
        rank_order_by,
        rank_params,
    )

    q = (q or "").strip()
    if not q or limit < 1:
        return {"tracks": []}
    ids = match_track_ids(db, q, reverse=True)
    if not ids:
        return {"tracks": []}
    _, folded = normalize_query(q)
    # Rank on the stored folded title. Sorting by raw LOWER(title) cannot see
    # that a Simplified query equals a Traditional title, so 愛 (matched only
    # by the reverse-substring clause) used to outrank the exact match 愛你.
    if _has_title_key(db):
        rank = rank_order_by("ti")
        # LEFT, not INNER: a track whose index row has not been built yet must
        # still appear, ranked last. An inner join silently dropped every
        # unindexed track.
        join_rank = "LEFT JOIN track_search_index ti ON ti.track_id = t.id"
        rank_args = rank_params(folded)
    else:
        rank = "(LOWER(t.title)=?) DESC, LOWER(t.title), t.id"
        join_rank = ""
        rank_args = (q.lower(),)
    rows = db.execute(
        f"""SELECT {_TRACK_COLUMNS} {_TRACK_JOINS} {join_rank}
            WHERE t.id IN (SELECT value FROM json_each(?))
            ORDER BY {rank}
            LIMIT ?""",
        (ids_json(ids), *rank_args, limit),
    ).fetchall()
    return {"tracks": [dict(row) for row in rows]}


_title_key_available: bool | None = None


def _has_title_key(db: sqlite3.Connection) -> bool:
    """title_key arrived in migration 13; a pre-migration library lacks it.

    Cached per process: the schema only changes when migrations run, which is
    before any search.
    """
    global _title_key_available
    if _title_key_available is None:
        try:
            db.execute("SELECT title_key FROM track_search_index LIMIT 1").fetchone()
            _title_key_available = True
        except sqlite3.OperationalError:
            _title_key_available = False
    return _title_key_available


def search_library(
    db: sqlite3.Connection,
    q: str,
    limit: int,
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
    *,
    artist_limit: int = 12,
    album_limit: int = 24,
) -> dict:
    """One offline, server-side search entry point for artists/albums/tracks.

    The existing `tracks` field remains backward compatible with /api/search.
    Artist and album records are bounded server-side; no complete catalog is
    transferred to a phone merely to find a matching name or alias.
    """
    q = (q or "").strip()
    if not q:
        return {
            "artists": [], "albums": [], "tracks": [],
            "total_artists": 0, "total_albums": 0,
        }
    from library_paging import fetch_album_page, fetch_artist_page

    artists = fetch_artist_page(
        db, search=q, limit=artist_limit, offset=0,
    )
    albums = fetch_album_page(
        db, search=q, limit=album_limit, offset=0,
    )
    tracks = search_tracks(
        db, q, limit, get_all_variants, to_simplified, to_traditional,
    )
    return {
        "artists": artists["artists"],
        "albums": albums["albums"],
        "tracks": tracks["tracks"],
        "total_artists": artists["total"],
        "total_albums": albums["total"],
    }


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
