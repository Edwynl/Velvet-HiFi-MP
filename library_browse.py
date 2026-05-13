from __future__ import annotations

import sqlite3
from typing import Callable, Optional


def _row_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _matches_variants(
    values: list[str],
    variants: list[str],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> bool:
    for raw_value in values:
        if not raw_value:
            continue

        field = str(raw_value)
        field_simp = to_simplified(field)
        field_trad = to_traditional(field)

        for variant in variants:
            v_simp = to_simplified(variant)
            v_trad = to_traditional(variant)
            if (
                variant in field
                or v_simp in field
                or v_trad in field
                or field in variant
                or field_simp in variant
                or field_trad in variant
            ):
                return True

    return False


def fetch_artists(
    db: sqlite3.Connection,
    search: str,
    limit: int,
    offset: int,
    get_all_variants: Callable[[str], list[str]],
    to_simplified: Callable[[str], str],
    to_traditional: Callable[[str], str],
) -> list[dict]:
    search = (search or "").strip()
    variants = get_all_variants(search) if search else []

    if not search:
        rows = db.execute(
            """
            SELECT ar.*,
                   COUNT(DISTINCT t.album_id) as album_count,
                   COUNT(DISTINCT ta.track_id) as track_count
            FROM artists ar
            LEFT JOIN track_artists ta ON ar.id = ta.artist_id
            LEFT JOIN tracks t ON ta.track_id = t.id
            GROUP BY ar.id
            ORDER BY ar.sort_name
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return _row_dicts(rows)

    like_patterns = [f"%{variant}%" for variant in variants if variant]
    direct_rows = []
    if like_patterns:
        conditions = " OR ".join(["ar.name LIKE ?"] * len(like_patterns))
        direct_rows = db.execute(
            f"""
            SELECT ar.*,
                   COUNT(DISTINCT t.album_id) as album_count,
                   COUNT(DISTINCT ta.track_id) as track_count
            FROM artists ar
            LEFT JOIN track_artists ta ON ar.id = ta.artist_id
            LEFT JOIN tracks t ON ta.track_id = t.id
            WHERE {conditions}
            GROUP BY ar.id
            """,
            like_patterns,
        ).fetchall()

    all_rows = db.execute(
        """
        SELECT ar.*,
               COUNT(DISTINCT t.album_id) as album_count,
               COUNT(DISTINCT ta.track_id) as track_count
        FROM artists ar
        LEFT JOIN track_artists ta ON ar.id = ta.artist_id
        LEFT JOIN tracks t ON ta.track_id = t.id
        GROUP BY ar.id
        """
    ).fetchall()

    matched_ids = {row["id"] for row in direct_rows}
    for artist in all_rows:
        if artist["id"] in matched_ids:
            continue
        if _matches_variants(
            [artist["name"]],
            variants,
            to_simplified,
            to_traditional,
        ):
            matched_ids.add(artist["id"])

    matched_artists = [dict(row) for row in all_rows if row["id"] in matched_ids]
    matched_artists.sort(key=lambda item: item.get("sort_name", item.get("name", "")))
    return matched_artists[offset:offset + limit]


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
    sort_map = {
        "title": "al.title",
        "year": "al.year DESC, al.title",
        "artist": "ar.sort_name, al.year DESC",
        "added": "al.created_at DESC",
    }
    order = sort_map.get(sort, "al.title")

    search = (search or "").strip()
    variants = get_all_variants(search) if search else []
    like_patterns = [f"%{variant}%" for variant in variants if variant]

    params: list = []
    where_parts: list[str] = []

    if like_patterns:
        title_conditions = " OR ".join(["al.title LIKE ?"] * len(like_patterns))
        artist_conditions = " OR ".join(["ar.name LIKE ?"] * len(like_patterns))
        where_parts.append(f"({title_conditions} OR {artist_conditions})")
        params.extend([*like_patterns, *like_patterns])

    if year is not None:
        where_parts.append("al.year = ?")
        params.append(year)

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    direct_rows = db.execute(
        f"""
        SELECT al.*, ar.name as artist_name, COUNT(t.id) as track_count,
               MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        LEFT JOIN tracks t ON t.album_id = al.id
        {where_clause}
        GROUP BY al.id
        ORDER BY {order}
        """,
        params,
    ).fetchall()

    if not search or len(direct_rows) >= limit:
        return [dict(row) for row in direct_rows[offset:offset + limit]]

    all_rows = db.execute(
        """
        SELECT al.*, ar.name as artist_name, COUNT(t.id) as track_count,
               MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        LEFT JOIN tracks t ON t.album_id = al.id
        GROUP BY al.id
        """
    ).fetchall()

    matched_ids = {row["id"] for row in direct_rows}
    for album in all_rows:
        if album["id"] in matched_ids:
            continue
        if year and album["year"] != year:
            continue
        if _matches_variants(
            [album["title"], album["artist_name"]],
            variants,
            to_simplified,
            to_traditional,
        ):
            matched_ids.add(album["id"])

    matched_rows = [dict(row) for row in all_rows if row["id"] in matched_ids]
    if sort == "year":
        matched_rows.sort(key=lambda item: (-(item.get("year") or 0), item.get("title", "")))
    elif sort == "artist":
        matched_rows.sort(key=lambda item: (item.get("artist_name", ""), -(item.get("year") or 0)))
    elif sort == "added":
        matched_rows.sort(key=lambda item: -(item.get("created_at") or 0))
    else:
        matched_rows.sort(key=lambda item: item.get("title", ""))
    return matched_rows[offset:offset + limit]


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
        like = f"%{search.lower()}%"
        where_parts.append(
            """(
                LOWER(t.title) LIKE ?
                OR LOWER(ar.name) LIKE ?
                OR LOWER(al.title) LIKE ?
                OR LOWER(COALESCE(al.genre, '')) LIKE ?
                OR LOWER(COALESCE(t.composer, '')) LIKE ?
                OR LOWER(COALESCE(t.work_title, '')) LIKE ?
                OR LOWER(COALESCE(t.performer, '')) LIKE ?
                OR LOWER(COALESCE(t.conductor, '')) LIKE ?
                OR LOWER(COALESCE(t.ensemble, '')) LIKE ?
            )"""
        )
        params.extend([like, like, like, like, like, like, like, like, like])
        count_params.extend([like, like, like, like, like, like, like, like, like])

    if favorites_only:
        where_parts.append("al.is_favorite = 1")

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

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
