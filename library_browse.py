from __future__ import annotations

import sqlite3
from typing import Callable, Optional


def _row_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def fetch_artists(
    db: sqlite3.Connection,
    search: str,
    limit: int,
    offset: int,
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> list[dict]:
    """Artists: OpenCC keys, aliases, and pagination after filtering."""
    from library_search_match import ids_json, match_catalog_ids

    params: list = []
    where = ""
    if search.strip():
        where = "WHERE ar.id IN (SELECT value FROM json_each(?))"
        params.append(ids_json(match_catalog_ids(db, "artist", search.strip())))
    rows = db.execute(
        f"""SELECT ar.*,
                   COUNT(DISTINCT t.album_id) as album_count,
                   COUNT(DISTINCT ta.track_id) as track_count
            FROM artists ar
            LEFT JOIN track_artists ta ON ar.id=ta.artist_id
            LEFT JOIN tracks t ON t.id=ta.track_id
            {where}
            GROUP BY ar.id
            ORDER BY ar.sort_name COLLATE NOCASE, ar.id
            LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()
    return _row_dicts(rows)


def fetch_albums(
    db: sqlite3.Connection,
    search: str,
    limit: int,
    offset: int,
    sort: str,
    year: Optional[int],
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> list[dict]:
    """Albums: complete Chinese results before offset/limit and year filters."""
    from library_search_match import ids_json, match_catalog_ids

    sort_map = {
        "title": "al.title",
        "year": "al.year DESC, al.title",
        "artist": "ar.sort_name, al.year DESC",
        "added": "al.created_at DESC",
    }
    params: list = []
    clauses = []
    if search.strip():
        clauses.append("al.id IN (SELECT value FROM json_each(?))")
        params.append(ids_json(match_catalog_ids(db, "album", search.strip())))
    if year is not None:
        clauses.append("al.year = ?")
        params.append(year)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.execute(
        f"""SELECT al.*, ar.name AS artist_name,
                   COUNT(t.id) AS track_count,
                   MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16
                            THEN 1 ELSE 0 END) AS is_hires
            FROM albums al
            JOIN artists ar ON al.artist_id=ar.id
            LEFT JOIN tracks t ON t.album_id=al.id
            {where}
            GROUP BY al.id
            ORDER BY {sort_map.get(sort, sort_map["title"])}, al.id
            LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()
    return _row_dicts(rows)


def fetch_tracks(
    db: sqlite3.Connection,
    limit: int,
    offset: int,
    search: str,
    sort: str,
    favorites_only: bool,
) -> dict:
    sort_map = {
        "title": "t.title COLLATE NOCASE ASC",
        "artist": "ar.name COLLATE NOCASE ASC, al.title COLLATE NOCASE ASC, t.track_number ASC",
        "album": "al.title COLLATE NOCASE ASC, t.disc_number ASC, t.track_number ASC",
        "composer": "COALESCE(t.composer, '') COLLATE NOCASE ASC, t.title COLLATE NOCASE ASC",
        "recent": "t.date_added DESC",
        "quality": "t.sample_rate DESC, t.bit_depth DESC, t.title COLLATE NOCASE ASC",
    }
    order_by = sort_map.get(sort, sort_map["title"])

    where_parts = []
    params: list = []
    count_params: list = []

    if search:
        from library_search_match import ids_json, match_track_ids
        # Preserve the old genre lookup in addition to Chinese title/credits.
        search_ids = ids_json(match_track_ids(db, search, reverse=True))
        # Genre remains a literal substring (not a LIKE wildcard pattern).
        genre_term = search.casefold()
        where_parts.append(
            "(t.id IN (SELECT value FROM json_each(?)) "
            "OR instr(LOWER(COALESCE(al.genre,'')), ?) > 0)"
        )
        params.extend((search_ids, genre_term))
        count_params.extend((search_ids, genre_term))

    if favorites_only:
        where_parts.append("al.is_favorite = 1")

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    if not search and not favorites_only:
        total = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    elif not search and favorites_only:
        total = db.execute(
            """
            SELECT COUNT(*)
            FROM tracks t
            JOIN albums al ON t.album_id = al.id
            WHERE al.is_favorite = 1
            """
        ).fetchone()[0]
    else:
        total = db.execute(
            f"""
            SELECT COUNT(*)
            FROM tracks t
            JOIN artists ar ON t.artist_id = ar.id
            JOIN albums al ON t.album_id = al.id
            {where_clause}
            """,
            count_params,
        ).fetchone()[0]

    rows = db.execute(
        f"""
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id,
               al.genre, al.year, al.is_favorite
        FROM tracks t
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        {where_clause}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    return {"tracks": _row_dicts(rows), "total": total}
