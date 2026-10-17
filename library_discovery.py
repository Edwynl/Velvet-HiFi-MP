from __future__ import annotations

import json
import re
import sqlite3
from typing import Callable, Optional

from fastapi import HTTPException

import chinese_variants
from artist_aliases import fetch_aliases
from library_browse import _matches_variants
from library_search_index import INDEX_SIGNATURE, read_index_meta, search_fold


def _needs_variant_fallback(
    q: str,
    variants: list[str],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> bool:
    """Only run Python-side fallback when variant expansion actually differs."""
    if not q or not variants:
        return False

    normalized = {q}
    normalized.update(variants)
    normalized.add(to_simplified(q))
    normalized.add(to_traditional(q))
    return len(normalized) > 1


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
_FALLBACK_BATCH = 512
# Up to this many distinct query substrings, reverse candidates come from term
# lookups (about 1 ms per 1,000); a longer query scans every term instead.
_MAX_TERM_LOOKUPS = 50_000


def _like_conditions(variants: list[str]) -> tuple[str, dict]:
    conditions = []
    params = {}
    for i, variant in enumerate(variants):
        params[f"like{i}"] = f"%{variant.lower()}%"
        p = f":like{i}"
        conditions.extend([
            f"LOWER(t.title) LIKE {p}",
            f"""EXISTS (
                SELECT 1
                FROM track_artists ta2
                JOIN artists ar_all ON ar_all.id = ta2.artist_id
                WHERE ta2.track_id = t.id
                  AND LOWER(ar_all.name) LIKE {p}
            )""",
            f"""EXISTS (
                SELECT 1
                FROM track_artists ta3
                JOIN artist_aliases aa ON aa.artist_id = ta3.artist_id
                WHERE ta3.track_id = t.id
                  AND LOWER(aa.name) LIKE {p}
            )""",
            f"LOWER(al.title) LIKE {p}",
            f"LOWER(COALESCE(t.composer, '')) LIKE {p}",
            f"LOWER(COALESCE(t.work_title, '')) LIKE {p}",
            f"LOWER(COALESCE(t.performer, '')) LIKE {p}",
            f"LOWER(COALESCE(t.conductor, '')) LIKE {p}",
            f"LOWER(COALESCE(t.ensemble, '')) LIKE {p}",
        ])
    return " OR ".join(conditions), params


def _like_prefilter(variants: list[str], params: dict) -> str:
    """instr() test that every LIKE match passes (see library_search_index)."""
    alternatives = []
    for i, variant in enumerate(variants):
        pieces = [search_fold(piece) for piece in re.split("[%_]", variant.lower()) if piece]
        if not pieces:
            return "1"  # the pattern is only wildcards
        for j, piece in enumerate(pieces):
            params[f"piece{i}_{j}"] = piece
        alternatives.append(" AND ".join(
            f"instr(s.search_text, :piece{i}_{j}) > 0" for j in range(len(pieces))
        ))
    return " OR ".join(f"({alternative})" for alternative in alternatives)


def _like_search(db: sqlite3.Connection, q: str, variants: list[str], limit: int,
                 index_ready: bool) -> list:
    where_clause, params = _like_conditions(variants)
    params.update(q=q.lower(), limit=limit)
    if not index_ready:
        return db.execute(
            f"""
            SELECT {_TRACK_COLUMNS} {_TRACK_JOINS}
            WHERE {where_clause}
            ORDER BY (LOWER(t.title) = :q) DESC, LOWER(t.title)
            LIMIT :limit
            """,
            params,
        ).fetchall()

    prefilter = _like_prefilter(variants, params)
    # CROSS JOIN keeps the narrow index as the scanned table, so the LIKE
    # conditions run only for its candidates; queued tracks are checked from
    # the base tables. Rows no longer arrive in ID order, so the ID that
    # broke title ties in the scan is now an explicit sort key.
    return db.execute(
        f"""
        SELECT * FROM (
            SELECT {_TRACK_COLUMNS}
            FROM track_search_index s
            CROSS JOIN tracks t
            JOIN artists ar_primary ON t.artist_id = ar_primary.id
            JOIN albums al ON t.album_id = al.id
            WHERE t.id = s.track_id
              AND ({prefilter})
              AND s.track_id NOT IN (SELECT track_id FROM track_search_dirty)
              AND ({where_clause})
            UNION ALL
            SELECT {_TRACK_COLUMNS} {_TRACK_JOINS}
            WHERE t.id IN (SELECT track_id FROM track_search_dirty)
              AND ({where_clause})
        )
        ORDER BY (LOWER(title) = :q) DESC, LOWER(title), id
        LIMIT :limit
        """,
        params,
    ).fetchall()


def _scan_all_tracks(db: sqlite3.Connection):
    last_id = 0
    while True:
        batch = db.execute(
            f"SELECT {_TRACK_COLUMNS} {_TRACK_JOINS} WHERE t.id > ? ORDER BY t.id LIMIT ?",
            (last_id, _FALLBACK_BATCH),
        ).fetchall()
        if not batch:
            return
        last_id = batch[-1]["id"]
        yield from batch


def _query_terms(key: str, max_term_length: int) -> Optional[set[str]]:
    """Distinct substrings of key that could equal a stored term, or None
    when there are more than _MAX_TERM_LOOKUPS of them."""
    substrings = set()
    for i in range(len(key)):
        for j in range(i + 1, min(len(key), i + max_term_length) + 1):
            substrings.add(key[i:j])
            if len(substrings) > _MAX_TERM_LOOKUPS:
                return None
    return substrings


def _indexed_fallback_candidates(db: sqlite3.Connection, variants: list[str], max_term_length: int):
    """Every track that could pass the variant predicate, in ID order."""
    keys = list(dict.fromkeys(search_fold(variant) for variant in variants))
    candidate_ids = {row[0] for row in db.execute(
        "SELECT track_id FROM track_search_index WHERE "
        + " OR ".join("instr(search_text, ?) > 0" for _ in keys),
        keys,
    )}
    for key in keys:
        # A reverse match is a whole folded field inside the folded query.
        substrings = _query_terms(key, max_term_length)
        if substrings is not None:
            rows = db.execute(
                "SELECT track_id FROM track_search_terms "
                "WHERE term IN (SELECT value FROM json_each(?))",
                (json.dumps(list(substrings)),),
            )
        else:
            rows = db.execute(
                "SELECT track_id FROM track_search_terms WHERE instr(?, term) > 0", (key,)
            )
        candidate_ids.update(row[0] for row in rows)
    candidate_ids.update(row[0] for row in db.execute("SELECT track_id FROM track_search_dirty"))
    return db.execute(
        f"""
        SELECT {_TRACK_COLUMNS} {_TRACK_JOINS}
        WHERE t.id IN (SELECT value FROM json_each(?))
        ORDER BY t.id
        """,
        (json.dumps(sorted(candidate_ids)),),
    )


def _scan_batch_end_id(db: sqlite3.Connection, track_id: int) -> Optional[int]:
    """Last ID of the _FALLBACK_BATCH-row scan batch that contains track_id.

    The fallback finishes the batch in which it reaches the limit; the
    indexed path skips non-candidates, so it locates that boundary here.
    None means the batch runs to the end of the table.
    """
    position = db.execute(
        f"SELECT COUNT(*) {_TRACK_JOINS} WHERE t.id <= ?", (track_id,)
    ).fetchone()[0]
    batch_end = -(-position // _FALLBACK_BATCH) * _FALLBACK_BATCH
    row = db.execute(
        f"SELECT t.id {_TRACK_JOINS} ORDER BY t.id LIMIT 1 OFFSET ?", (batch_end - 1,)
    ).fetchone()
    return row[0] if row else None


def search_tracks(
    db: sqlite3.Connection,
    q: str,
    limit: int,
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> dict:
    q = (q or "").strip()
    variants = get_all_variants(q) if q else []
    if not variants:
        return {"tracks": []}

    index_meta = read_index_meta(db) or {}
    index_ready = index_meta.get("signature") == INDEX_SIGNATURE
    tracks = _like_search(db, q, variants, limit, index_ready)

    matched_track_ids = {row["id"] for row in tracks}
    use_variant_fallback = _needs_variant_fallback(q, variants, to_simplified, to_traditional)

    if len(tracks) < limit and use_variant_fallback:
        # Do not sample only the first remaining*3 rows: a match near the end
        # of a 66k-track library would never be returned. The index keys are
        # folded with chinese_variants, so other converters scan every track.
        if (index_ready and to_simplified is chinese_variants.to_simplified
                and to_traditional is chinese_variants.to_traditional):
            candidates = _indexed_fallback_candidates(
                db, variants, int(index_meta.get("max_term_length") or 0),
            )
        else:
            candidates = _scan_all_tracks(db)
        limit_reached = False
        batch_end_id = None
        for track in candidates:
            if batch_end_id is not None and track["id"] > batch_end_id:
                break
            if track["id"] in matched_track_ids:
                continue
            if _matches_variants(
                [track["title"], track["artist_name"], track["album_title"],
                 track["composer"], track["work_title"], track["performer"],
                 track["conductor"], track["ensemble"]],
                variants, to_simplified, to_traditional,
            ):
                matched_track_ids.add(track["id"])
                if len(matched_track_ids) >= limit and not limit_reached:
                    limit_reached = True
                    batch_end_id = _scan_batch_end_id(db, track["id"])

        if len(matched_track_ids) > len(tracks):
            id_placeholders = ",".join("?" * len(matched_track_ids))
            final_rows = db.execute(
                f"""
                SELECT {_TRACK_COLUMNS} {_TRACK_JOINS}
                WHERE t.id IN ({id_placeholders})
                ORDER BY (LOWER(t.title) = ?) DESC, LOWER(t.title)
                LIMIT ?
                """,
                (*matched_track_ids, q.lower(), limit),
            ).fetchall()
            return {"tracks": [dict(row) for row in final_rows]}

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
