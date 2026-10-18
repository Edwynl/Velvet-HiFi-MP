"""Bounded album pages for high-latency browser clients.

This endpoint complements the existing /api/albums list contract, rather than
silently changing clients that expect an array. Only the requested page is
materialized; album statistics are evaluated for the selected records.
"""
from __future__ import annotations

import sqlite3
import re
import unicodedata
from typing import Callable
from artist_ranking import artist_familiarity
from artist_regions import REGION_LABELS, artist_region_map

ALBUM_GENRE_GROUPS = (
    'Asian Pop', 'Pop & Rock', 'Jazz', 'Classical', 'Hip-Hop & R&B',
    'Electronic', 'Soundtracks', 'Folk & World', 'Other',
)


def album_genre_group(value: str | None) -> str:
    """Group catalog labels for browsing without rewriting imported tags."""
    name = ''.join(c for c in unicodedata.normalize('NFKD', value or '')
                   if not unicodedata.combining(c)).casefold()
    rules = (
        ('Soundtracks', r'soundtrack|film|anime|jeux video|video game|原声|原聲|サウンドトラック'),
        ('Jazz', r'jazz|爵士'),
        ('Classical', r'classic|classique|古典|クラシック'),
        ('Hip-Hop & R&B', r'hip.?hop|\brap\b|r&b|rhythm|soul|funk|说唱|說唱'),
        ('Electronic', r'electro|dance|house|techno|trance|drum|电子|電子'),
        ('Asian Pop', r'asie|asia|far east|[cjk][ -]?pop|mandopop|cantopop|华语|華語|国语|國語|粤语|粵語|港台|ポップ|韓国|韩国|日本'),
        ('Pop & Rock', r'pop|rock|metal|punk|alternati|indie|inde\b|流行'),
        ('Folk & World', r'folk|country|blues|monde|world|reggae|afro|latin|民族|民谣|民謠|世界'),
    )
    return next((group for group, pattern in rules if re.search(pattern, name)), 'Other')


def _search_rank(db: sqlite3.Connection, kind: str, id_sql: str,
                 q: str) -> tuple[str, str, tuple]:
    """JOIN, ORDER BY prefix and parameters that put the best matches first.

    Like track search, a search answers the query before the chosen sort; the
    sort still orders results of equal rank.
    """
    from library_search_match import catalog_rank, has_column

    if not q or not has_column(db, "catalog_search_index", "search_text"):
        return "", "", ()
    order, args = catalog_rank(q)
    join = (f" LEFT JOIN catalog_search_index cr"
            f" ON cr.kind='{kind}' AND cr.entity_id={id_sql}")
    return join, order + ", ", args


def album_catalog_filters(db: sqlite3.Connection) -> dict:
    present = {album_genre_group(row[0]) for row in db.execute('SELECT DISTINCT genre FROM albums')}
    genres = [group for group in ALBUM_GENRE_GROUPS if group in present]
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
        from library_search_match import ids_json, match_catalog_ids
        matches = match_catalog_ids(db, "album", q)
        parts.append("al.id IN (SELECT value FROM json_each(?))")
        args.append(ids_json(matches))
    if genre:
        if genre in ALBUM_GENRE_GROUPS:
            labels = [row[0] for row in db.execute("SELECT DISTINCT trim(coalesce(genre,'')) FROM albums")
                      if album_genre_group(row[0]) == genre]
            parts.append("trim(coalesce(al.genre,'')) IN (" + ','.join('?' for _ in labels) + ")" if labels else '0')
            args.extend(labels)
        else:
            # Preserve exact filtering for existing callers using raw tags.
            parts.append("trim(al.genre) = ?")
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
    rank_join, rank_order, rank_args = _search_rank(db, "album", "al.id", q)
    page_rows = db.execute(
        f"""
        SELECT al.*, ar.name AS artist_name,
               (SELECT COUNT(*) FROM tracks t WHERE t.album_id = al.id) AS track_count,
               (SELECT MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16
                                THEN 1 ELSE 0 END)
                FROM tracks t WHERE t.album_id = al.id) AS is_hires
        {from_sql}{rank_join}{where}
        ORDER BY {rank_order}{orders.get(sort, orders["title"])}
        LIMIT ? OFFSET ?
        """,
        (*args, *rank_args, limit, offset),
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
    sort: str = 'name',
    region: str = '',
    get_all_variants: Callable[[str], list[str]] | None = None,
    facets: bool = True,
) -> dict:
    """Count before slicing; only aggregate credits for the requested artists.

    `facets=False` is for global search: it skips classifying every artist in
    the library into a region (which the Artists page needs for its filter
    chips) and returns rows without a region.
    """
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Page size must be 1..200 and offset nonnegative")
    if region and region not in REGION_LABELS:
        raise ValueError('Invalid artist region')
    q = search.strip()
    params: list[str] = []
    where = ""
    if q:
        from library_search_match import ids_json, match_catalog_ids
        params = [ids_json(match_catalog_ids(db, "artist", q))]
        where = "WHERE ar.id IN (SELECT value FROM json_each(?))"
    if facets or region:
        regions = artist_region_map(db)
        db.create_function('velvet_artist_region', 1, lambda id: regions.get(id, 'unknown'), deterministic=True)
        counts = dict(db.execute(
            f'SELECT velvet_artist_region(ar.id), COUNT(*) FROM artists ar {where} GROUP BY 1', params
        ).fetchall())
        region_facets = [{'region': key, 'label': label, 'count': counts.get(key, 0)}
                         for key, label in REGION_LABELS.items()]
        all_total = sum(counts.values())
        total = counts.get(region, 0) if region else all_total
        region_sql = 'velvet_artist_region(ar.id)'
    else:
        all_total = total = db.execute(
            f'SELECT COUNT(*) FROM artists ar {where}', params
        ).fetchone()[0]
        region_facets = []
        region_sql = 'NULL'
    if region:
        where = f'WHERE ({where[6:]}) AND ' if where else 'WHERE '
        where += 'velvet_artist_region(ar.id) = ?'
        params.append(region)
    rank_join, rank_order, rank_args = _search_rank(db, "artist", "ar.id", q)
    db.create_function('velvet_artist_familiarity', 1, artist_familiarity, deterministic=True)
    order = ('velvet_artist_familiarity(ar.name) DESC, play_count DESC, favorite_count DESC, library_album_count DESC, '
             'ar.sort_name COLLATE NOCASE ASC, ar.id ASC' if sort == 'popular' else
             'ar.sort_name COLLATE NOCASE ASC, ar.id ASC')
    rows = db.execute(
        f"""
        SELECT ar.*, {region_sql} AS region,
               (SELECT COUNT(*) FROM play_history ph JOIN tracks pt ON pt.id=ph.track_id
                WHERE pt.artist_id=ar.id) AS play_count,
               (SELECT COUNT(*) FROM albums fa WHERE fa.artist_id=ar.id AND fa.is_favorite=1) AS favorite_count,
               (SELECT COUNT(*) FROM albums la WHERE la.artist_id=ar.id) AS library_album_count,
               (SELECT COUNT(DISTINCT t.album_id)
                FROM track_artists ta JOIN tracks t ON ta.track_id=t.id
                WHERE ta.artist_id=ar.id) AS album_count,
               (SELECT COUNT(*) FROM track_artists ta WHERE ta.artist_id=ar.id) AS track_count
        FROM artists ar{rank_join} {where}
        ORDER BY {rank_order}{order}
        LIMIT ? OFFSET ?
        """,
        (*params, *rank_args, limit, offset),
    ).fetchall()
    return {
        "artists": [dict(row) for row in rows],
        "total": total, "limit": limit, "offset": offset,
        "regions": region_facets, "all_total": all_total,
    }
