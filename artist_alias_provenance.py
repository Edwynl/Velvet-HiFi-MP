"""Provenance and safe audit of Velvet artist aliases.

Before schema 16 aliases could be either user-entered or imported by an
earlier name-only MusicBrainz matcher. Their origin cannot be inferred after
the fact. They remain usable, but are explicitly marked as UNKNOWN by absence
of a provenance row and can be reviewed without destroying manual aliases.
"""
from __future__ import annotations

import sqlite3

KNOWN_SOURCES = frozenset({
    "manual", "user_confirmed", "trusted_mbid",
    "release_corroborated", "reviewed_legacy",
})


def mark_alias_source(
    db: sqlite3.Connection, artist_id: int, name: str, source: str,
    *, mbid: str = "", evidence: str = "",
) -> bool:
    """Record origin without overwriting an earlier user's manual source."""
    if source not in KNOWN_SOURCES:
        raise ValueError("Unknown alias provenance")
    row = db.execute(
        "SELECT id FROM artist_aliases WHERE artist_id=? AND name=? COLLATE NOCASE",
        (artist_id, name),
    ).fetchone()
    if row is None:
        return False
    db.execute(
        """INSERT OR IGNORE INTO artist_alias_provenance
           (alias_id,source,mbid,evidence) VALUES (?,?,?,?)""",
        (int(row[0]), source, str(mbid or "")[:40], str(evidence or "")[:500]),
    )
    return True


def audit_legacy_aliases(
    db: sqlite3.Connection, *, limit: int = 25, offset: int = 0,
) -> dict:
    """Page aliases of *unknown* origin, never silently quarantine/delete."""
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Invalid alias audit pagination")
    where = "FROM artist_aliases aa JOIN artists ar ON ar.id=aa.artist_id " \
            "LEFT JOIN artist_alias_provenance p ON p.alias_id=aa.id " \
            "WHERE p.alias_id IS NULL"
    total = db.execute(f"SELECT COUNT(*) {where}").fetchone()[0]
    rows = db.execute(
        f"""SELECT aa.id AS alias_id, aa.artist_id, ar.name AS artist_name,
                   aa.name AS alias, aa.created_at
            {where} ORDER BY ar.name COLLATE NOCASE, aa.name COLLATE NOCASE, aa.id
            LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return {
        "aliases": [dict(row) for row in rows],
        "total": int(total), "limit": limit, "offset": offset,
    }


def resolve_legacy_alias(
    db: sqlite3.Connection, artist_id: int, name: str, *, action: str,
) -> dict:
    """Explicit action to KEEP or REMOVE exactly one unproven alias."""
    if action not in ("keep", "remove"):
        raise ValueError("Invalid alias audit action")
    row = db.execute(
        """SELECT aa.id FROM artist_aliases aa
           LEFT JOIN artist_alias_provenance p ON p.alias_id=aa.id
           WHERE aa.artist_id=? AND aa.name=? COLLATE NOCASE
             AND p.alias_id IS NULL""",
        (artist_id, name),
    ).fetchone()
    if row is None:
        raise LookupError("Alias is not pending audit")
    if action == "keep":
        mark_alias_source(db, artist_id, name, "reviewed_legacy")
    else:
        # Existing alias-delete triggers update both track and catalog dirty
        # indexes. No FLAC metadata or artist names are modified.
        db.execute("DELETE FROM artist_aliases WHERE id=?", (int(row[0]),))
    return {"status": "ok", "action": action, "artist_id": artist_id, "alias": name}
