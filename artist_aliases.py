"""One artist row per person; their other names live in artist_aliases.

Merging repoints every reference (tracks, credits, albums) to the kept row,
merges albums that end up with the same artist and title (the scanner keys
albums by that pair, so a rescan would merge them anyway), and keeps the
merged rows' names as aliases. Callers commit.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional


def _has_alias_table(db: sqlite3.Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artist_aliases'"
    ).fetchone() is not None


def resolve_alias(db: sqlite3.Connection, name: str) -> Optional[int]:
    """Artist ID that has name as an alias (case-insensitive), else None."""
    if not name or not _has_alias_table(db):
        return None
    row = db.execute("SELECT artist_id FROM artist_aliases WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def fetch_aliases(db: sqlite3.Connection, artist_id: int) -> list[str]:
    if not _has_alias_table(db):
        return []
    return [row[0] for row in db.execute(
        "SELECT name FROM artist_aliases WHERE artist_id = ? ORDER BY name COLLATE NOCASE",
        (artist_id,),
    )]


def add_alias(db: sqlite3.Connection, artist_id: int, name: str) -> bool:
    """Record name as another name of artist_id. Returns False when name is
    blank, is the artist's own name, or already belongs to another artist."""
    name = (name or "").strip()
    if not name:
        return False
    own = db.execute("SELECT name FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if own is None:
        raise ValueError(f"artist {artist_id} does not exist")
    if own[0].casefold() == name.casefold():
        return False
    owner = resolve_alias(db, name)
    if owner is not None:
        return owner == artist_id
    db.execute("INSERT INTO artist_aliases (artist_id, name) VALUES (?, ?)", (artist_id, name))
    return True


def _has_provenance_table(db: sqlite3.Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artist_alias_provenance'"
    ).fetchone() is not None


def _provenance_of(db: sqlite3.Connection, artist_id: int) -> dict[str, tuple]:
    """Recorded origins of an artist's aliases, keyed by casefolded name."""
    if not _has_provenance_table(db):
        return {}
    return {
        name.casefold(): (source, mbid, evidence)
        for name, source, mbid, evidence in db.execute(
            """SELECT aa.name, p.source, p.mbid, p.evidence
               FROM artist_aliases aa JOIN artist_alias_provenance p ON p.alias_id = aa.id
               WHERE aa.artist_id = ?""",
            (artist_id,),
        )
    }


def _record_provenance(db: sqlite3.Connection, artist_id: int, name: str,
                       source: str, mbid: str = "", evidence: str = "") -> None:
    if _has_provenance_table(db):
        from artist_alias_provenance import mark_alias_source
        mark_alias_source(db, artist_id, name, source, mbid=mbid, evidence=evidence)


def _merge_duplicate_albums(db: sqlite3.Connection, artist_id: int) -> int:
    """Fold albums of artist_id that share a title into the oldest one."""
    merged = 0
    groups = db.execute(
        """
        SELECT title, GROUP_CONCAT(id) FROM (
            SELECT id, title FROM albums WHERE artist_id = ? ORDER BY id
        ) GROUP BY title HAVING COUNT(*) > 1
        """,
        (artist_id,),
    ).fetchall()
    for _title, ids in groups:
        keep, *drop = [int(value) for value in ids.split(",")]
        for album_id in drop:
            db.execute(
                """
                UPDATE albums SET
                    year = COALESCE(year, (SELECT year FROM albums WHERE id = :d)),
                    genre = COALESCE(genre, (SELECT genre FROM albums WHERE id = :d)),
                    label = COALESCE(label, (SELECT label FROM albums WHERE id = :d)),
                    mbid = COALESCE(mbid, (SELECT mbid FROM albums WHERE id = :d)),
                    cover_path = COALESCE(cover_path, (SELECT cover_path FROM albums WHERE id = :d)),
                    is_favorite = MAX(COALESCE(is_favorite, 0),
                                      (SELECT COALESCE(is_favorite, 0) FROM albums WHERE id = :d))
                WHERE id = :k
                """,
                {"k": keep, "d": album_id},
            )
            db.execute("UPDATE tracks SET album_id = ? WHERE album_id = ?", (keep, album_id))
            db.execute("DELETE FROM albums WHERE id = ?", (album_id,))
            merged += 1
        db.execute(
            "UPDATE albums SET total_tracks = (SELECT COUNT(*) FROM tracks WHERE album_id = ?) WHERE id = ?",
            (keep, keep),
        )
    return merged


def merge_artists(db: sqlite3.Connection, target_id: int, source_ids: Iterable[int]) -> dict:
    """Move everything of source_ids onto target_id and delete the sources.

    The sources' names (and aliases) become aliases of the target.
    """
    sources = [sid for sid in dict.fromkeys(source_ids) if sid != target_id]
    if db.execute("SELECT 1 FROM artists WHERE id = ?", (target_id,)).fetchone() is None:
        raise ValueError(f"artist {target_id} does not exist")
    names = []
    # Moved aliases keep the origin recorded for them; one with no record
    # stays unknown (listed by the legacy audit). A merged artist's own name
    # becomes an alias by this deliberate merge.
    provenance: dict[str, tuple] = {}
    merged_names: list[str] = []
    for source_id in sources:
        row = db.execute(
            "SELECT name, sort_name, mbid, bio, image_path FROM artists WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"artist {source_id} does not exist")
        names.append(row[0])
        merged_names.append(row[0])
        db.execute(
            """
            UPDATE artists SET
                mbid = COALESCE(mbid, ?), bio = COALESCE(bio, ?), image_path = COALESCE(image_path, ?)
            WHERE id = ?
            """,
            (row[2], row[3], row[4], target_id),
        )
        db.execute("UPDATE tracks SET artist_id = ? WHERE artist_id = ?", (target_id, source_id))
        db.execute(
            "INSERT OR IGNORE INTO track_artists (track_id, artist_id) "
            "SELECT track_id, ? FROM track_artists WHERE artist_id = ?",
            (target_id, source_id),
        )
        db.execute("DELETE FROM track_artists WHERE artist_id = ?", (source_id,))
        db.execute("UPDATE albums SET artist_id = ? WHERE artist_id = ?", (target_id, source_id))
        if _has_alias_table(db):
            provenance.update(_provenance_of(db, source_id))
            names.extend(row[0] for row in db.execute(
                "SELECT name FROM artist_aliases WHERE artist_id = ?", (source_id,)
            ))
            db.execute("DELETE FROM artist_aliases WHERE artist_id = ?", (source_id,))
        db.execute("DELETE FROM artists WHERE id = ?", (source_id,))
    for name in names:
        if add_alias(db, target_id, name):
            recorded = provenance.get(name.casefold())
            if recorded:
                _record_provenance(db, target_id, name, *recorded)
            elif name in merged_names:
                _record_provenance(db, target_id, name, "manual")
    albums_merged = _merge_duplicate_albums(db, target_id)
    return {"artist_id": target_id, "merged": sources, "aliases": names, "albums_merged": albums_merged}


def rename_artist(db: sqlite3.Connection, artist_id: int, new_name: str, *, keep_old_name: bool = True) -> dict:
    """Set the display name; the old name becomes an alias.

    When another artist already has new_name, artist_id is merged into it.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("new artist name is blank")
    row = db.execute("SELECT name FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if row is None:
        raise ValueError(f"artist {artist_id} does not exist")
    old_name = row[0]
    if old_name == new_name:
        return {"artist_id": artist_id, "aliases": []}
    other = db.execute(
        "SELECT id FROM artists WHERE name = ? AND id != ?", (new_name, artist_id)
    ).fetchone()
    if other is not None:
        return merge_artists(db, other[0], [artist_id])
    if _has_alias_table(db):
        # The new display name must not stay listed as its own alias.
        db.execute("DELETE FROM artist_aliases WHERE artist_id = ? AND name = ?", (artist_id, new_name))
    from scanner_service import sort_name

    db.execute(
        "UPDATE artists SET name = ?, sort_name = ? WHERE id = ?",
        (new_name, sort_name(new_name), artist_id),
    )
    aliases = [old_name] if keep_old_name and add_alias(db, artist_id, old_name) else []
    for alias in aliases:
        # A deliberate rename keeps the old display name as a known alias.
        _record_provenance(db, artist_id, alias, "manual")
    return {"artist_id": artist_id, "aliases": aliases}
