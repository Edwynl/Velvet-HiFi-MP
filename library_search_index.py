"""Precomputed dual-key track and catalog search indexes.

OpenCC T2S may rewrite *phrases*. It is NOT a homomorphism over individual
characters or substrings, so we must never use the old per-character LIKE
prefilter theorem. Store complete raw and converted field keys separately;
search checks both. Dirty rows are matched from authoritative metadata.
"""
from __future__ import annotations

import sqlite3
from importlib.metadata import version as package_version

from chinese_variants import literal_fold, search_fold
from library_search_match import has_column, indexed_text, _catalog_fields

# A changed dictionary, fold algorithm, or on-disk format MUST invalidate keys.
# 3: terms also carry credited artist and alias names (reverse-match parity).
# 4: search_text rows also carry title_key, so search can rank in SQL.
_INDEX_FORMAT = 4
INDEX_SIGNATURE = f"{_INDEX_FORMAT}:opencc-{package_version('OpenCC')}:t2s-nfkc-casefold:dual-keys"


def read_index_meta(db: sqlite3.Connection) -> dict[str, str] | None:
    try:
        return dict(db.execute("SELECT key, value FROM search_index_meta").fetchall())
    except sqlite3.OperationalError:
        return None


def _build_rows(db: sqlite3.Connection, track_ids: list[int]):
    marks = ",".join("?" for _ in track_ids)
    credited: dict[int, list[str]] = {}
    for track_id, name in db.execute(
        f"""SELECT ta.track_id, ar.name FROM track_artists ta
            JOIN artists ar ON ar.id=ta.artist_id
            WHERE ta.track_id IN ({marks})""", track_ids
    ):
        credited.setdefault(track_id, []).append(name)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='artist_aliases'").fetchone():
        for track_id, name in db.execute(
            f"""SELECT ta.track_id, aa.name FROM track_artists ta
                JOIN artist_aliases aa ON aa.artist_id=ta.artist_id
                WHERE ta.track_id IN ({marks})""", track_ids
        ):
            credited.setdefault(track_id, []).append(name)
        # Primary artist aliases must also match tracks with no track_artists row.
        for track_id, name in db.execute(
            f"""SELECT t.id, aa.name FROM tracks t
                JOIN artist_aliases aa ON aa.artist_id=t.artist_id
                WHERE t.id IN ({marks})""", track_ids
        ):
            credited.setdefault(track_id, []).append(name)

    index_rows, term_rows = [], []
    for track_id, title, *rest in db.execute(
        f"""SELECT t.id, t.title, ar.name, al.title, t.composer, t.work_title,
                   t.performer, t.conductor, t.ensemble
            FROM tracks t
            LEFT JOIN artists ar ON ar.id=t.artist_id
            LEFT JOIN albums al ON al.id=t.album_id
            WHERE t.id IN ({marks})""", track_ids
    ):
        fields = [title, *rest]
        values = [*fields, *credited.get(track_id, [])]
        # title_key lets search rank an exact canonical match above a track that
        # only matched because its title is a substring of the query.
        index_rows.append((
            track_id,
            indexed_text(values),
            search_fold(str(title)) if title else "",
        ))
        # Reverse-substring compatibility with the original search. The index
        # stores complete field keys; never allow a substring across fields.
        # Credited artists and aliases must be listed here too: search_text is
        # built from the same values, so omitting them made the reverse
        # direction (stored name contained in the query) lose every credited
        # artist and alias while the exact-scan path still matched them.
        terms = set()
        for value in values:
            if value:
                terms.update((literal_fold(str(value)), search_fold(str(value))))
        term_rows.extend((term, track_id) for term in terms if term)
    return index_rows, term_rows


def _refresh_catalog_indexes(db: sqlite3.Connection, batch_size: int,
                            max_rows: int | None = None) -> None:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_search_dirty'"
    ).fetchone():
        return
    # genre_key arrived in migration 13; older libraries still lack it.
    with_genre_key = has_column(db, "catalog_search_index", "genre_key")
    done = 0
    while True:
        if max_rows is not None and done >= max_rows:
            return
        dirty = db.execute(
            "SELECT kind, entity_id FROM catalog_search_dirty "
            "ORDER BY kind, entity_id LIMIT ?", (batch_size,),
        ).fetchall()
        if not dirty:
            return
        done += len(dirty)
        for kind in ("artist", "album"):
            ids = [row[1] for row in dirty if row[0] == kind]
            if not ids:
                continue
            marks = ",".join("?" for _ in ids)
            db.execute(
                f"DELETE FROM catalog_search_index WHERE kind=? AND entity_id IN ({marks})",
                (kind, *ids),
            )
            fields = _catalog_fields(db, kind, ids)
            rows = [(kind, entity_id, indexed_text(values))
                    for entity_id, values in fields.items()]
            if with_genre_key and kind == "album":
                genres = dict(db.execute(
                    f"SELECT id, genre FROM albums WHERE id IN ({marks})", ids
                ))
                db.executemany(
                    "INSERT INTO catalog_search_index"
                    "(kind,entity_id,search_text,genre_key) VALUES (?,?,?,?)",
                    [(k, e, s, search_fold(str(g or "")))
                     for k, e, s in rows for g in (genres.get(e),)],
                )
            else:
                db.executemany(
                    "INSERT INTO catalog_search_index(kind,entity_id,search_text) "
                    "VALUES (?,?,?)",
                    rows,
                )
            db.execute(
                f"DELETE FROM catalog_search_dirty WHERE kind=? AND entity_id IN ({marks})",
                (kind, *ids),
            )


def refresh_track_search_index(db: sqlite3.Connection, batch_size: int = 2000,
                               *, max_rows: int | None = None) -> int:
    """Refresh index rows, including catalog keys. Caller owns the transaction.

    Return value remains the number of track rows (backward compatibility).

    `max_rows` bounds how much one call indexes. It is a batch-granularity
    budget: the check runs before starting the next batch, because the SQL
    rebuilds a whole batch at a time, so a call indexes at most
    `max_rows + batch_size - 1` rows. Both loops drain their queue completely
    when it is omitted, which is what a full rebuild wants. A caller that must
    not hold one long write transaction -- the bulk alias importer -- passes a
    budget instead: whatever stays dirty remains correct, because dirty rows
    are still matched through the live data path, and the server picks the rest
    up on its next refresh.
    A missing OpenCC installation fails at import rather than silently
    degrading Chinese search to a 115-character map.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be positive when given")
    meta = read_index_meta(db)
    if meta is None:
        return 0
    max_term_length = int(meta.get("max_term_length") or 0)
    if meta.get("signature") != INDEX_SIGNATURE:
        db.execute("INSERT OR IGNORE INTO track_search_dirty(track_id) SELECT id FROM tracks")
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_search_dirty'"
        ).fetchone():
            db.execute(
                "INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id) "
                "SELECT 'artist',id FROM artists"
            )
            db.execute(
                "INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id) "
                "SELECT 'album',id FROM albums"
            )
        db.execute(
            "INSERT OR REPLACE INTO search_index_meta(key,value) VALUES('signature',?)",
            (INDEX_SIGNATURE,),
        )
        max_term_length = 0

    count = 0
    while True:
        if max_rows is not None and count >= max_rows:
            break
        track_ids = [row[0] for row in db.execute(
            "SELECT track_id FROM track_search_dirty ORDER BY track_id LIMIT ?",
            (batch_size,),
        )]
        if not track_ids:
            break
        marks = ",".join("?" for _ in track_ids)
        db.execute(f"DELETE FROM track_search_index WHERE track_id IN ({marks})", track_ids)
        db.execute(f"DELETE FROM track_search_terms WHERE track_id IN ({marks})", track_ids)
        keys, terms = _build_rows(db, track_ids)
        db.executemany(
            "INSERT INTO track_search_index(track_id,search_text,title_key) VALUES(?,?,?)",
            keys,
        )
        db.executemany(
            "INSERT INTO track_search_terms(term,track_id) VALUES(?,?)", terms
        )
        db.execute(f"DELETE FROM track_search_dirty WHERE track_id IN ({marks})", track_ids)
        max_term_length = max([max_term_length, *(len(term) for term, _ in terms)])
        db.execute(
            "INSERT OR REPLACE INTO search_index_meta(key,value) "
            "VALUES('max_term_length',?)", (str(max_term_length),),
        )
        count += len(track_ids)
    _refresh_catalog_indexes(db, batch_size, max_rows)
    return count
