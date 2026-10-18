"""One matching contract for Velvet's track, artist, and album search.

A field is indexed in BOTH forms:
- the literal Unicode/case folded form (never loses a direct substring);
- the OpenCC T2S folded form (allows Simplified and Traditional queries).

The two forms are delimited, so a query cannot match across field boundaries.
Catalog and track dirty rows are folded on demand to preserve correctness
between a metadata update and the next batch index refresh. The music files
and stored display values are never rewritten.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from chinese_variants import literal_fold, search_fold, to_traditional

SEP = "\x1f"
_BATCH = 400
_MAX_SUBSTRINGS = 10000
# Beyond this a query cannot usefully narrow anything, and the reverse direction
# falls back to a full instr() scan per term batch. Truncation is safe because
# the index only matches substrings of the query.
_MAX_QUERY_LENGTH = 128


def indexed_text(values) -> str:
    keys: dict[str, None] = {}
    for value in values:
        if value:
            for key in (literal_fold(str(value)), search_fold(str(value))):
                if key:
                    keys[key] = None
    return SEP.join(keys)


def matches_fields(query: str, values, *, reverse: bool = False) -> bool:
    literal, canonical = literal_fold(query), search_fold(query)
    if not literal and not canonical:
        return False
    for value in values:
        if not value:
            continue
        raw, folded = literal_fold(str(value)), search_fold(str(value))
        if (literal and literal in raw) or (canonical and canonical in folded):
            return True
        if reverse and ((raw and raw in literal) or (folded and folded in canonical)):
            return True
    return False


def _chunks(values, n=_BATCH):
    for start in range(0, len(values), n):
        yield values[start:start + n]


def _existing(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ready(db: sqlite3.Connection) -> bool:
    from library_search_index import INDEX_SIGNATURE, read_index_meta
    meta = read_index_meta(db) or {}
    return meta.get("signature") == INDEX_SIGNATURE


def _track_fields(db: sqlite3.Connection, ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for part in _chunks(ids):
        marks = ",".join("?" for _ in part)
        for row in db.execute(
            f"""SELECT t.id, t.title, ar.name, al.title, t.composer, t.work_title,
                       t.performer, t.conductor, t.ensemble
                FROM tracks t
                JOIN artists ar ON ar.id=t.artist_id
                JOIN albums al ON al.id=t.album_id
                WHERE t.id IN ({marks})""", part
        ):
            found[row[0]] = [v for v in row[1:] if v]
        for track_id, name in db.execute(
            f"""SELECT ta.track_id, a.name
                FROM track_artists ta JOIN artists a ON a.id=ta.artist_id
                WHERE ta.track_id IN ({marks})""", part
        ):
            found.setdefault(track_id, []).append(name)
        if _existing(db, "artist_aliases"):
            for track_id, name in db.execute(
                f"""SELECT ta.track_id, aa.name FROM track_artists ta
                    JOIN artist_aliases aa ON aa.artist_id=ta.artist_id
                    WHERE ta.track_id IN ({marks})""", part
            ):
                found.setdefault(track_id, []).append(name)
            for track_id, name in db.execute(
                f"""SELECT t.id, aa.name FROM tracks t
                    JOIN artist_aliases aa ON aa.artist_id=t.artist_id
                    WHERE t.id IN ({marks})""", part
            ):
                found.setdefault(track_id, []).append(name)
    return found


def _substrings(key: str, max_length: int) -> set[str] | None:
    if not key or not max_length:
        return set()
    out: set[str] = set()
    for i in range(len(key)):
        for end in range(i + 1, min(len(key), i + max_length) + 1):
            out.add(key[i:end])
            if len(out) > _MAX_SUBSTRINGS:
                return None
    return out


_catalog_columns_cache: set[str] | None = None


def _catalog_has_genre_key_index(db: sqlite3.Connection) -> bool:
    """genre_key arrived in migration 13; a pre-migration library lacks it.

    Cached per process: the schema only changes when migrations run, which is
    before any search.
    """
    global _catalog_columns_cache
    if _catalog_columns_cache is None:
        _catalog_columns_cache = {
            row[1] for row in db.execute("PRAGMA table_info(catalog_search_index)")
        }
    return "genre_key" in _catalog_columns_cache


def normalize_query(query: str) -> tuple[str, str]:
    """Fold a query once, enforcing the bounds the HTTP layer relies on.

    A very long query makes the reverse direction fall back to
    `instr(?, term)` over every term in the index, which is a full scan per
    batch. Truncating is safe because the index only ever matches substrings of
    the query: a term that the truncated prefix cannot contain was never going
    to match.
    """
    query = query or ""
    if len(query) > _MAX_QUERY_LENGTH:
        query = query[:_MAX_QUERY_LENGTH]
    return literal_fold(query), search_fold(query)


def rank_order_by(alias: str = "ti") -> str:
    """ORDER BY fragment ranking tracks by how well they answer the query.

    Consumes exactly three bound parameters, all the folded query key: an exact
    canonical title, then a literal prefix, then any literal substring.

    The prefix test is `instr(...) = 1`, never `LIKE ? || '%'`: without an ESCAPE
    clause LIKE treats the *query's* own `%` and `_` as wildcards, so the query
    `a_b` would promote the unrelated title `aXb` above the literal match `za_b`.

    Ranking uses the stored `title_key` because a raw `LOWER(title)` cannot tell
    that a Simplified query equals a Traditional title exactly -- which is what
    let 愛 outrank 愛你 for the query 爱你.
    """
    return (
        "CASE"
        f" WHEN {alias}.title_key = ? THEN 0"
        f" WHEN instr(COALESCE({alias}.title_key,''), ?) = 1 THEN 1"
        f" WHEN instr(COALESCE({alias}.title_key,''), ?) > 0 THEN 2"
        " ELSE 3 END,"
        " LOWER(t.title), t.id"
    )


def rank_params(folded: str) -> tuple[str, str, str]:
    """The three parameters consumed by `rank_order_by`."""
    return folded, folded, folded


def dirty_album_ids_matching(db: sqlite3.Connection, folded: str) -> set[int]:
    """Albums whose genre changed since the catalog index was last refreshed.

    The stored `catalog_search_index.genre_key` still holds the previous genre
    for these albums, so it would answer the query with a stale label: after
    搖滾 -> 金屬, the query 摇滚 would still match and 金属 would not, until the
    next refresh. Newly inserted albums have no index row at all. Fold the
    authoritative `albums.genre` for exactly those pending rows so search stays
    correct between refreshes, which is the same contract the dirty queue gives
    the title and credit fields.
    """
    if not folded or not _existing(db, "catalog_search_dirty"):
        return set()
    rows = db.execute(
        "SELECT a.id, a.genre FROM albums a "
        "JOIN catalog_search_dirty d ON d.kind='album' AND d.entity_id=a.id "
        "WHERE a.genre IS NOT NULL AND a.genre != ''"
    ).fetchall()
    return {int(row[0]) for row in rows if folded in search_fold(str(row[1]))}


def _has_script_variants(raw: str, folded: str) -> bool:
    """True when the query reads differently in Simplified vs Traditional.

    Reverse matching (a stored field contained in the query) exists for Chinese
    script variants, as the pre-OpenCC search did. Applied to Latin text it
    lets an artist "Low" match the query "Yellow" or a one-letter title match
    nearly every query, so plain queries only match forward.
    """
    return folded != raw or literal_fold(to_traditional(raw)) != raw


def match_track_ids(db: sqlite3.Connection, query: str, *, reverse: bool = True) -> set[int]:
    """Complete candidate set; no page-limit shortcut may hide Chinese matches."""
    raw, folded = normalize_query(query)
    if not raw and not folded:
        return set()
    reverse = reverse and _has_script_variants(raw, folded)
    if not _ready(db) or not _existing(db, "track_search_index"):
        all_ids = [int(row[0]) for row in db.execute("SELECT id FROM tracks ORDER BY id")]
        fields = _track_fields(db, all_ids)
        return {track_id for track_id, values in fields.items()
                if matches_fields(query, values, reverse=reverse)}

    found = {row[0] for row in db.execute(
        "SELECT track_id FROM track_search_index "
        "WHERE instr(search_text, ?) > 0 OR instr(search_text, ?) > 0",
        (raw, folded),
    )}
    if reverse and _existing(db, "track_search_terms"):
        from library_search_index import read_index_meta
        max_length = int((read_index_meta(db) or {}).get("max_term_length") or 0)
        terms = set()
        for key in {raw, folded}:
            part = _substrings(key, max_length)
            if part is None:
                # Extremely long queries: no quadratic expansion.
                found.update(row[0] for row in db.execute(
                    "SELECT track_id FROM track_search_terms WHERE instr(?, term)>0",
                    (key,),
                ))
            else:
                terms.update(part)
        if terms:
            # Batches keep SQLite's JSON parameter and temporary sets bounded.
            vals = sorted(terms)
            for part in _chunks(vals, 2000):
                found.update(row[0] for row in db.execute(
                    "SELECT track_id FROM track_search_terms "
                    "WHERE term IN (SELECT value FROM json_each(?))",
                    (json.dumps(part, ensure_ascii=False),),
                ))

    dirty_ids = [int(row[0]) for row in db.execute(
        "SELECT track_id FROM track_search_dirty ORDER BY track_id"
    )]
    if dirty_ids:
        found.difference_update(dirty_ids)
        for track_id, values in _track_fields(db, dirty_ids).items():
            if matches_fields(query, values, reverse=reverse):
                found.add(track_id)
    return found


def _catalog_fields(db: sqlite3.Connection, kind: str, ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    owners: dict[int, int] = {}
    for part in _chunks(ids):
        marks = ",".join("?" for _ in part)
        if kind == "artist":
            for artist_id, name in db.execute(
                f"SELECT id, name FROM artists WHERE id IN ({marks})", part
            ):
                found[artist_id] = [name]
                owners[artist_id] = artist_id
        else:
            for album_id, title, artist_id, name in db.execute(
                f"""SELECT al.id, al.title, ar.id, ar.name FROM albums al
                    JOIN artists ar ON ar.id=al.artist_id
                    WHERE al.id IN ({marks})""", part
            ):
                found[album_id] = [title, name]
                owners[album_id] = artist_id

    if not found or not _existing(db, "artist_aliases"):
        return found
    grouped: dict[int, list[str]] = defaultdict(list)
    owner_ids = sorted(set(owners.values()))
    for part in _chunks(owner_ids):
        marks = ",".join("?" for _ in part)
        for artist_id, alias in db.execute(
            f"SELECT artist_id, name FROM artist_aliases WHERE artist_id IN ({marks})",
            part,
        ):
            grouped[artist_id].append(alias)
    for entity_id, artist_id in owners.items():
        found[entity_id].extend(grouped.get(artist_id, []))
    return found


def match_catalog_ids(db: sqlite3.Connection, kind: str, query: str) -> set[int]:
    """Artist/album matches including aliases; correct before index refresh."""
    if kind not in ("artist", "album"):
        raise ValueError("Invalid catalog kind")
    raw, folded = normalize_query(query)
    if not raw and not folded:
        return set()
    ready = (_ready(db) and _existing(db, "catalog_search_index")
             and _existing(db, "catalog_search_dirty"))
    if ready:
        ids = {row[0] for row in db.execute(
            "SELECT entity_id FROM catalog_search_index "
            "WHERE kind=? AND (instr(search_text, ?) > 0 OR instr(search_text, ?) > 0)",
            (kind, raw, folded),
        )}
        dirty = [int(row[0]) for row in db.execute(
            "SELECT entity_id FROM catalog_search_dirty WHERE kind=?", (kind,)
        )]
        ids.difference_update(dirty)
        dirty_fields = _catalog_fields(db, kind, dirty)
        ids.update(entity_id for entity_id, vals in dirty_fields.items()
                   if matches_fields(query, vals))
        return ids
    table = "artists" if kind == "artist" else "albums"
    ids = [int(row[0]) for row in db.execute(f"SELECT id FROM {table}")]
    return {entity_id for entity_id, vals in _catalog_fields(db, kind, ids).items()
            if matches_fields(query, vals)}


def ids_json(ids: set[int]) -> str:
    """Bound SQLite parameter count even for broad one-character searches."""
    return json.dumps(sorted(ids), separators=(",", ":"))
