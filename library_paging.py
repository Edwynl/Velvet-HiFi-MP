"""Bounded album pages for high-latency browser clients.

This endpoint complements the existing /api/albums list contract, rather than
silently changing clients that expect an array. Only the requested page is
materialized; album statistics are evaluated for the selected records.
"""
from __future__ import annotations

import sqlite3
from typing import Callable


def album_catalog_filters(db: sqlite3.Connection) -> dict:
    genres = [
        row[0] for row in db.execute(
            "SELECT DISTINCT trim(genre) FROM albums WHERE trim(coalesce(genre,'')) != '' "
            "ORDER BY trim(genre) COLLATE NOCASE"
        )
    ]
    decades = [
        f"{int(row[0])}s" for row in db.execute(
            "SELECT DISTINCT CAST(year / 10 AS INTEGER) * 10 "
            "FROM albums WHERE year BETWEEN 1800 AND 2200 ORDER BY 1 DESC"
        )
    ]
    return {"genres": genres, "decades": decades}


def fetch_album_page(
    db: sqlite3.Connection,
    *,
    search: str = "",
    limit: int = 60,
    offset: int = 0,
    sort: str = "title",
    genre: str = "",
    decade: str = "",
    favorites_only: bool = False,
    get_all_variants: Callable[[str], list[str]] | None = None,
) -> dict:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Page size must be 1..200 and offset nonnegative")
    orders = {
        "title": "al.title COLLATE NOCASE ASC, al.id ASC",
        "year": "al.year DESC, al.title COLLATE NOCASE ASC, al.id ASC",
        "artist": "ar.sort_name COLLATE NOCASE ASC, al.year DESC, al.id ASC",
        "added": "al.created_at DESC, al.id DESC",
    }
    parts: list[str] = []
    args: list = []
    q = search.strip()
    if q:
        variants = list(dict.fromkeys(
            [q, *(get_all_variants(q) if get_all_variants else [])]
        ))[:12]
        escape = chr(92)
        like = ["%" + v.replace(escape, escape + escape).replace("%", escape + "%").replace("_", escape + "_") + "%" for v in variants]
        terms = " OR ".join("(al.title LIKE ? ESCAPE '\\' OR ar.name LIKE ? ESCAPE '\\')" for _ in like)
        parts.append(f"({terms})")
        for item in like:
            args.extend((item, item))
    if genre:
        parts.append("trim(al.genre) = ?")  # same normalization as the offered labels
        args.append(genre)
    if decade:
        if not decade.endswith("s") or not decade[:-1].isdigit():
            raise ValueError("Invalid decade")
        decade_year = int(decade[:-1])
        if not 1800 <= decade_year <= 2200 or decade_year % 10:
            raise ValueError("Invalid decade")
        parts.append("al.year >= ? AND al.year < ?")
        args.extend((decade_year, decade_year + 10))
    if favorites_only:
        parts.append("al.is_favorite = 1")
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    from_sql = "FROM albums al JOIN artists ar ON ar.id = al.artist_id"
    total = db.execute(f"SELECT COUNT(*) {from_sql}{where}", args).fetchone()[0]
    page_rows = db.execute(
        f"""
        SELECT al.*, ar.name AS artist_name,
               (SELECT COUNT(*) FROM tracks t WHERE t.album_id = al.id) AS track_count,
               (SELECT MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16
                                THEN 1 ELSE 0 END)
                FROM tracks t WHERE t.album_id = al.id) AS is_hires
        {from_sql}{where}
        ORDER BY {orders.get(sort, orders["title"])}
        LIMIT ? OFFSET ?
        """,
        (*args, limit, offset),
    ).fetchall()
    return {
        "albums": [dict(r) for r in page_rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def fetch_artist_page(
    db: sqlite3.Connection,
    *,
    search: str = "",
    limit: int = 60,
    offset: int = 0,
    get_all_variants: Callable[[str], list[str]] | None = None,
) -> dict:
    """Count before slicing; only aggregate credits for the requested artists."""
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Page size must be 1..200 and offset nonnegative")
    q = search.strip()
    variants = list(dict.fromkeys(
        [q, *(get_all_variants(q) if get_all_variants and q else [])]
    ))[:12] if q else []
    params: list[str] = []
    where = ""
    if variants:
        backslash = chr(92)
        params = [
            "%" + v.replace(backslash, backslash + backslash)
                   .replace("%", backslash + "%").replace("_", backslash + "_") + "%"
            for v in variants
        ]
        name_match = " OR ".join("ar.name LIKE ? ESCAPE '\\'" for _ in params)
        alias_match = " OR ".join("aa.name LIKE ? ESCAPE '\\'" for _ in params)
        where = (
            f"WHERE {name_match} OR EXISTS (SELECT 1 FROM artist_aliases aa "
            f"WHERE aa.artist_id = ar.id AND ({alias_match}))"
        )
        params = params * 2
    total = db.execute(f"SELECT COUNT(*) FROM artists ar {where}", params).fetchone()[0]
    rows = db.execute(
        f"""
        SELECT ar.*,
               (SELECT COUNT(DISTINCT t.album_id)
                FROM track_artists ta JOIN tracks t ON ta.track_id=t.id
                WHERE ta.artist_id=ar.id) AS album_count,
               (SELECT COUNT(*) FROM track_artists ta WHERE ta.artist_id=ar.id) AS track_count
        FROM artists ar {where}
        ORDER BY ar.sort_name COLLATE NOCASE ASC, ar.id ASC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    return {
        "artists": [dict(row) for row in rows],
        "total": total, "limit": limit, "offset": offset,
    }
