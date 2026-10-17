"""Precomputed search keys that narrow /api/search to a few candidate tracks.

Every key is the *search fold* of a field: ``to_simplified`` and then
SQLite-style ASCII lowering. ``to_simplified`` is a per-character map whose
values are never keys, so for the predicates search uses:

* ``LOWER(field) LIKE '%p%'`` implies every ``%``/``_``-free piece of ``p``,
  folded, is inside ``fold(field)``;
* the variant fallback (``v``/``S(v)``/``T(v)`` inside the field, or the
  field inside them) implies ``fold(v) in fold(field)`` or
  ``fold(field) in fold(v)``.

``track_search_index.search_text`` joins the folds of every field either
predicate reads, so ``instr`` on it finds the forward candidates;
``track_search_terms`` holds each fallback field's fold as a whole term, so
the reverse candidates are the terms that are substrings of the query. The
index only narrows: the original predicates still decide every result.

Triggers (schema 10) queue changed tracks in ``track_search_dirty``; search
checks those with the exact path, so a writer that never calls
``refresh_track_search_index`` only costs speed.
"""
from __future__ import annotations

import hashlib
import sqlite3
import string

from chinese_variants import _T2S_MAP, to_simplified

SEP = "\x1f"
_INDEX_FORMAT = 1
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _signature() -> str:
    digest = hashlib.sha1(repr(sorted(_T2S_MAP.items())).encode("utf-8")).hexdigest()
    return f"{_INDEX_FORMAT}:{digest[:16]}"


INDEX_SIGNATURE = _signature()


def search_fold(text: str) -> str:
    """Simplified Chinese, then only A-Z lowered (as SQLite's LOWER())."""
    return to_simplified(text).translate(_ASCII_LOWER)


def read_index_meta(db: sqlite3.Connection) -> dict[str, str] | None:
    """Index metadata, or None when the index tables do not exist yet.

    ``signature`` equals INDEX_SIGNATURE once the index is built;
    ``max_term_length`` bounds every stored term's length.
    """
    try:
        return dict(db.execute("SELECT key, value FROM search_index_meta").fetchall())
    except sqlite3.OperationalError:
        return None


def _build_rows(db: sqlite3.Connection, track_ids: list[int]):
    placeholders = ",".join("?" * len(track_ids))
    credited: dict[int, list[str]] = {}
    for track_id, name in db.execute(
        f"""
        SELECT ta.track_id, ar.name
        FROM track_artists ta JOIN artists ar ON ar.id = ta.artist_id
        WHERE ta.track_id IN ({placeholders})
        """,
        track_ids,
    ):
        credited.setdefault(track_id, []).append(name)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'artist_aliases'").fetchone():
        # Search also matches credited artists by their other names.
        for track_id, name in db.execute(
            f"""
            SELECT ta.track_id, aa.name
            FROM track_artists ta JOIN artist_aliases aa ON aa.artist_id = ta.artist_id
            WHERE ta.track_id IN ({placeholders})
            """,
            track_ids,
        ):
            credited.setdefault(track_id, []).append(name)

    index_rows = []
    term_rows = []
    for track_id, *fallback_fields in db.execute(
        f"""
        SELECT t.id, t.title, ar.name, al.title, t.composer, t.work_title,
               t.performer, t.conductor, t.ensemble
        FROM tracks t
        LEFT JOIN artists ar ON ar.id = t.artist_id
        LEFT JOIN albums al ON al.id = t.album_id
        WHERE t.id IN ({placeholders})
        """,
        track_ids,
    ):
        terms = list(dict.fromkeys(
            search_fold(str(value)) for value in fallback_fields if value
        ))
        credit_terms = [search_fold(str(name)) for name in credited.get(track_id, []) if name]
        index_rows.append((track_id, SEP.join(dict.fromkeys(terms + credit_terms))))
        term_rows.extend((term, track_id) for term in terms)
    return index_rows, term_rows


def refresh_track_search_index(db: sqlite3.Connection, batch_size: int = 2000) -> int:
    """Rebuild index rows for dirty tracks. The caller commits.

    Returns the number of tracks processed; 0 on a pre-10 schema.
    """
    meta = read_index_meta(db)
    if meta is None:
        return 0
    max_term_length = int(meta.get("max_term_length") or 0)
    if meta.get("signature") != INDEX_SIGNATURE:
        # New index, or the conversion map changed: every key is suspect.
        db.execute("INSERT OR IGNORE INTO track_search_dirty(track_id) SELECT id FROM tracks")
        db.execute(
            "INSERT OR REPLACE INTO search_index_meta(key, value) VALUES ('signature', ?)",
            (INDEX_SIGNATURE,),
        )
        max_term_length = 0

    processed = 0
    while True:
        track_ids = [row[0] for row in db.execute(
            "SELECT track_id FROM track_search_dirty ORDER BY track_id LIMIT ?",
            (batch_size,),
        )]
        if not track_ids:
            return processed
        placeholders = ",".join("?" * len(track_ids))
        # Removed tracks keep no rows; present ones are rewritten below.
        db.execute(f"DELETE FROM track_search_index WHERE track_id IN ({placeholders})", track_ids)
        db.execute(f"DELETE FROM track_search_terms WHERE track_id IN ({placeholders})", track_ids)
        index_rows, term_rows = _build_rows(db, track_ids)
        db.executemany(
            "INSERT INTO track_search_index(track_id, search_text) VALUES (?, ?)", index_rows
        )
        db.executemany(
            "INSERT INTO track_search_terms(term, track_id) VALUES (?, ?)", term_rows
        )
        db.execute(f"DELETE FROM track_search_dirty WHERE track_id IN ({placeholders})", track_ids)
        # Only ever grows, so it stays an upper bound when terms are removed.
        max_term_length = max([max_term_length, *(len(term) for term, _ in term_rows)])
        db.execute(
            "INSERT OR REPLACE INTO search_index_meta(key, value) VALUES ('max_term_length', ?)",
            (str(max_term_length),),
        )
        processed += len(track_ids)
