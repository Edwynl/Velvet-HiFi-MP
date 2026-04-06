from __future__ import annotations

import sqlite3
import re
from typing import Optional

from fastapi import HTTPException


def _clean_credit_value(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip(" ;|/\n\t")


def _split_credit_values(value) -> list[str]:
    if not value:
        return []

    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    items: list[str] = []
    seen: set[str] = set()

    for raw in raw_values:
        text = _clean_credit_value(raw)
        if not text:
            continue

        parts = re.split(r"\s*(?:;|\||\n+)\s*", text)
        if len(parts) == 1 and " / " in text:
            parts = [part.strip() for part in text.split(" / ")]

        for part in parts:
            cleaned = _clean_credit_value(part)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                items.append(cleaned)

    return items


def _aggregate_credit_rows(rows, field: str) -> list[dict]:
    counts: dict[str, int] = {}
    for row in rows:
        for name in _split_credit_values(row[field]):
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": name, "track_count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]


VALID_CREDIT_FIELDS = {
    "performer": "Performer",
    "conductor": "Conductor",
    "ensemble": "Ensemble",
}


def _resolve_credit_field(role: str) -> str:
    field = (role or "").strip().lower()
    if field not in VALID_CREDIT_FIELDS:
        raise HTTPException(400, f"Unsupported credit role: {role}")
    return field


def _credit_field_matches(value, target_name: str) -> bool:
    target = _clean_credit_value(target_name).lower()
    if not target:
        return False
    return any(item.lower() == target for item in _split_credit_values(value))


def _first_credit_name(value, fallback: str = "") -> str:
    names = _split_credit_values(value)
    if names:
        return names[0]
    return _clean_credit_value(fallback)


def _work_version_key(track: dict) -> str:
    conductor = _first_credit_name(track.get("conductor"))
    ensemble = _first_credit_name(track.get("ensemble"))
    performer = _first_credit_name(track.get("performer"), track.get("artist_name") or track.get("artist"))
    year = str(track.get("year") or "")
    album_id = str(track.get("album_id") or "")

    if not any([conductor, ensemble, performer]):
        return f"album::{album_id}"
    return "||".join([conductor, ensemble, performer, year])


def _build_work_versions(tracks: list[dict]) -> list[dict]:
    versions: dict[str, dict] = {}

    for track in tracks:
        key = _work_version_key(track)
        conductor = _first_credit_name(track.get("conductor"))
        ensemble = _first_credit_name(track.get("ensemble"))
        performer = _first_credit_name(track.get("performer"), track.get("artist_name") or track.get("artist"))
        year = track.get("year")
        album_id = track.get("album_id")
        album_title = track.get("album_title") or "Unknown Album"

        if key not in versions:
            versions[key] = {
                "key": key,
                "headline": conductor or ensemble or performer or album_title,
                "conductor": conductor or None,
                "ensemble": ensemble or None,
                "performer": performer or None,
                "year": year,
                "track_count": 0,
                "album_ids": set(),
                "album_titles": set(),
            }

        versions[key]["track_count"] += 1
        if album_id:
            versions[key]["album_ids"].add(album_id)
        if album_title:
            versions[key]["album_titles"].add(album_title)

    items = []
    for version in versions.values():
        items.append({
            "key": version["key"],
            "headline": version["headline"],
            "conductor": version["conductor"],
            "ensemble": version["ensemble"],
            "performer": version["performer"],
            "year": version["year"],
            "track_count": version["track_count"],
            "album_count": len(version["album_ids"]),
            "album_titles": sorted(version["album_titles"]),
        })

    items.sort(key=lambda item: (-(item["year"] or 0), item["headline"].lower()))
    return items


def fetch_album_detail(db: sqlite3.Connection, album_id: int) -> dict:
    album = db.execute(
        """
        SELECT al.*, al.artist_id, ar.name as artist_name,
               (SELECT MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END)
                FROM tracks t WHERE t.album_id = al.id) as is_hires
        FROM albums al JOIN artists ar ON al.artist_id=ar.id
        WHERE al.id=?
        """,
        (album_id,),
    ).fetchone()
    if not album:
        raise HTTPException(404, "Album not found")

    tracks = db.execute(
        """
        SELECT * FROM tracks WHERE album_id=?
        ORDER BY disc_number, track_number, title
        """,
        (album_id,),
    ).fetchall()
    return {**dict(album), "tracks": [dict(track) for track in tracks]}


def fetch_album_credits(db: sqlite3.Connection, album_id: int) -> dict:
    album = db.execute(
        """
        SELECT al.*, ar.name as artist_name,
               (SELECT MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END)
                FROM tracks t WHERE t.album_id = al.id) as is_hires
        FROM albums al
        JOIN artists ar ON al.artist_id = ar.id
        WHERE al.id = ?
        """,
        (album_id,),
    ).fetchone()
    if not album:
        raise HTTPException(404, "Album not found")

    composers = db.execute(
        """
        SELECT t.composer, COUNT(*) as track_count
        FROM tracks t
        WHERE t.album_id = ? AND t.composer IS NOT NULL AND t.composer != ''
        GROUP BY t.composer
        ORDER BY track_count DESC, t.composer COLLATE NOCASE ASC
        """,
        (album_id,),
    ).fetchall()

    works = db.execute(
        """
        SELECT t.work_title, COUNT(*) as track_count
        FROM tracks t
        WHERE t.album_id = ? AND t.work_title IS NOT NULL AND t.work_title != ''
        GROUP BY t.work_title
        ORDER BY t.work_title COLLATE NOCASE ASC
        """,
        (album_id,),
    ).fetchall()

    artists = db.execute(
        """
        SELECT DISTINCT ar.id, ar.name
        FROM tracks t
        JOIN track_artists ta ON ta.track_id = t.id
        JOIN artists ar ON ar.id = ta.artist_id
        WHERE t.album_id = ?
        ORDER BY ar.name COLLATE NOCASE ASC
        """,
        (album_id,),
    ).fetchall()

    credit_rows = db.execute(
        """
        SELECT t.performer, t.conductor, t.ensemble
        FROM tracks t
        WHERE t.album_id = ?
        """,
        (album_id,),
    ).fetchall()

    return {
        "album": dict(album),
        "primary_artist": {"id": album["artist_id"], "name": album["artist_name"]},
        "artists": [dict(row) for row in artists],
        "composers": [dict(row) for row in composers],
        "works": [dict(row) for row in works],
        "performers": _aggregate_credit_rows(credit_rows, "performer"),
        "conductors": _aggregate_credit_rows(credit_rows, "conductor"),
        "ensembles": _aggregate_credit_rows(credit_rows, "ensemble"),
        "label": album["label"],
        "genre": album["genre"],
        "year": album["year"],
    }


def fetch_composers(db: sqlite3.Connection, limit: int, offset: int, search: str = "") -> list[dict]:
    where_clause = "WHERE t.composer IS NOT NULL AND t.composer != ''"
    params: list = []

    if search:
        where_clause += " AND LOWER(t.composer) LIKE ?"
        params.append(f"%{search.lower()}%")

    rows = db.execute(
        f"""
        SELECT t.composer,
               COUNT(*) as track_count,
               COUNT(DISTINCT t.album_id) as album_count,
               COUNT(DISTINCT CASE
                 WHEN t.work_title IS NOT NULL AND t.work_title != '' THEN t.work_title
               END) as work_count
        FROM tracks t
        {where_clause}
        GROUP BY t.composer
        ORDER BY track_count DESC, t.composer COLLATE NOCASE ASC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_composer_detail(db: sqlite3.Connection, composer: str, limit: int) -> dict:
    stats = db.execute(
        """
        SELECT COUNT(*) as track_count,
               COUNT(DISTINCT t.album_id) as album_count,
               COUNT(DISTINCT CASE
                 WHEN t.work_title IS NOT NULL AND t.work_title != '' THEN t.work_title
               END) as work_count,
               SUM(t.duration) as total_duration
        FROM tracks t
        WHERE t.composer = ?
        """,
        (composer,),
    ).fetchone()

    if not stats or not stats["track_count"]:
        raise HTTPException(404, "Composer not found")

    works = db.execute(
        """
        SELECT COALESCE(NULLIF(t.work_title, ''), t.title) as work_title,
               COUNT(*) as track_count,
               COUNT(DISTINCT t.album_id) as album_count,
               MIN(al.year) as first_year,
               MAX(al.year) as latest_year
        FROM tracks t
        JOIN albums al ON t.album_id = al.id
        WHERE t.composer = ?
        GROUP BY COALESCE(NULLIF(t.work_title, ''), t.title)
        ORDER BY work_title COLLATE NOCASE ASC
        """,
        (composer,),
    ).fetchall()

    albums = db.execute(
        """
        SELECT DISTINCT al.*, ar.name as artist_name
        FROM tracks t
        JOIN albums al ON t.album_id = al.id
        JOIN artists ar ON al.artist_id = ar.id
        WHERE t.composer = ?
        ORDER BY al.year DESC, al.title COLLATE NOCASE ASC
        LIMIT 24
        """,
        (composer,),
    ).fetchall()

    tracks = db.execute(
        """
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id, al.year
        FROM tracks t
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        WHERE t.composer = ?
        ORDER BY COALESCE(NULLIF(t.work_title, ''), t.title) COLLATE NOCASE ASC,
                 al.year DESC, t.disc_number ASC, t.track_number ASC
        LIMIT ?
        """,
        (composer, limit),
    ).fetchall()

    return {
        "name": composer,
        "track_count": stats["track_count"] or 0,
        "album_count": stats["album_count"] or 0,
        "work_count": stats["work_count"] or 0,
        "total_duration_hours": round((stats["total_duration"] or 0) / 3600, 1),
        "works": [dict(row) for row in works],
        "albums": [dict(row) for row in albums],
        "tracks": [dict(row) for row in tracks],
    }


def fetch_works(
    db: sqlite3.Connection,
    limit: int,
    offset: int,
    search: str = "",
    composer: str = "",
) -> list[dict]:
    where_parts = ["t.composer IS NOT NULL", "t.composer != ''"]
    params: list = []

    if search:
        like = f"%{search.lower()}%"
        where_parts.append(
            """(
                LOWER(COALESCE(t.work_title, t.title, '')) LIKE ?
                OR LOWER(COALESCE(t.composer, '')) LIKE ?
                OR LOWER(COALESCE(t.performer, '')) LIKE ?
                OR LOWER(COALESCE(t.conductor, '')) LIKE ?
                OR LOWER(COALESCE(t.ensemble, '')) LIKE ?
            )"""
        )
        params.extend([like, like, like, like, like])

    if composer:
        where_parts.append("t.composer = ?")
        params.append(composer)

    where_clause = "WHERE " + " AND ".join(where_parts)

    rows = db.execute(
        f"""
        SELECT t.composer,
               COALESCE(NULLIF(t.work_title, ''), t.title) as work_title,
               COUNT(*) as track_count,
               COUNT(DISTINCT t.album_id) as album_count,
               MIN(al.year) as first_year,
               MAX(al.year) as latest_year
        FROM tracks t
        JOIN albums al ON t.album_id = al.id
        {where_clause}
        GROUP BY t.composer, COALESCE(NULLIF(t.work_title, ''), t.title)
        ORDER BY t.composer COLLATE NOCASE ASC, work_title COLLATE NOCASE ASC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_work_detail(
    db: sqlite3.Connection,
    composer: str,
    title: str,
    limit: int,
    role: str = "",
    name: str = "",
    version: str = "",
) -> dict:
    work_expr = "COALESCE(NULLIF(t.work_title, ''), t.title)"

    role_field = _resolve_credit_field(role) if role else ""
    candidate_params: list = [composer, title]
    role_where = ""
    if role_field and name:
        role_where = f" AND LOWER(COALESCE(t.{role_field}, '')) LIKE ?"
        candidate_params.append(f"%{name.lower()}%")

    track_rows = db.execute(
        f"""
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id, al.year,
               al.artist_id as album_artist_id
        FROM tracks t
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        WHERE t.composer = ? AND {work_expr} = ?{role_where}
        ORDER BY al.year DESC, t.disc_number ASC, t.track_number ASC
        """,
        candidate_params,
    ).fetchall()

    filtered_tracks = []
    if role_field and name:
        for row in track_rows:
            track = dict(row)
            if _credit_field_matches(track.get(role_field), name):
                filtered_tracks.append(track)
    else:
        filtered_tracks = [dict(row) for row in track_rows]

    if not filtered_tracks:
        raise HTTPException(404, "Work not found")

    all_versions = _build_work_versions(filtered_tracks)

    if version:
        filtered_tracks = [track for track in filtered_tracks if _work_version_key(track) == version]
        if not filtered_tracks:
            raise HTTPException(404, "Work version not found")

    total_duration = sum(track.get("duration") or 0 for track in filtered_tracks)
    years = [track.get("year") for track in filtered_tracks if track.get("year")]
    album_map: dict[int, dict] = {}
    credit_rows = []

    for track in filtered_tracks:
        album_id = track["album_id"]
        if album_id not in album_map:
            album_map[album_id] = {
                "id": album_id,
                "title": track["album_title"],
                "artist_id": track["album_artist_id"],
                "artist_name": track["artist_name"],
                "year": track["year"],
            }
        credit_rows.append({
            "performer": track.get("performer"),
            "conductor": track.get("conductor"),
            "ensemble": track.get("ensemble"),
        })

    return {
        "composer": composer,
        "work_title": title,
        "track_count": len(filtered_tracks),
        "album_count": len(album_map),
        "total_duration_hours": round(total_duration / 3600, 1),
        "first_year": min(years) if years else None,
        "latest_year": max(years) if years else None,
        "selected_filter": {"role": role_field, "label": VALID_CREDIT_FIELDS.get(role_field), "name": name} if role_field and name else None,
        "selected_version": next((item for item in all_versions if item["key"] == version), None) if version else None,
        "performers": _aggregate_credit_rows(credit_rows, "performer"),
        "conductors": _aggregate_credit_rows(credit_rows, "conductor"),
        "ensembles": _aggregate_credit_rows(credit_rows, "ensemble"),
        "versions": all_versions,
        "albums": sorted(album_map.values(), key=lambda item: (-(item["year"] or 0), item["title"].lower()))[:36],
        "tracks": filtered_tracks[:limit],
    }


def fetch_credit_entities(
    db: sqlite3.Connection,
    role: str,
    limit: int,
    offset: int,
    search: str = "",
) -> dict:
    field = _resolve_credit_field(role)
    rows = db.execute(
        f"""
        SELECT t.{field} as credit_value
        FROM tracks t
        WHERE t.{field} IS NOT NULL AND t.{field} != ''
        """
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        for name in _split_credit_values(row["credit_value"]):
            if search and search.lower() not in name.lower():
                continue
            counts[name] = counts.get(name, 0) + 1

    items = [
        {"name": name, "track_count": count, "role": field}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]
    return {
        "role": field,
        "label": VALID_CREDIT_FIELDS[field],
        "total": len(items),
        "items": items[offset:offset + limit],
    }


def fetch_credit_entity_detail(db: sqlite3.Connection, role: str, name: str, limit: int) -> dict:
    field = _resolve_credit_field(role)
    candidate_rows = db.execute(
        f"""
        SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id, al.year, al.artist_id as album_artist_id
        FROM tracks t
        JOIN artists ar ON t.artist_id = ar.id
        JOIN albums al ON t.album_id = al.id
        WHERE LOWER(COALESCE(t.{field}, '')) LIKE ?
        ORDER BY al.year DESC, al.title COLLATE NOCASE ASC, t.disc_number ASC, t.track_number ASC
        """,
        (f"%{name.lower()}%",),
    ).fetchall()

    matched_tracks = []
    target = name.lower()
    for row in candidate_rows:
        track = dict(row)
        names = _split_credit_values(track.get(field))
        if any(item.lower() == target for item in names):
            matched_tracks.append(track)

    if not matched_tracks:
        raise HTTPException(404, f"{VALID_CREDIT_FIELDS[field]} not found")

    album_map: dict[int, dict] = {}
    composer_counts: dict[str, int] = {}
    work_counts: dict[tuple[str, str], dict] = {}
    total_duration = 0.0

    for track in matched_tracks:
        total_duration += track.get("duration") or 0
        album_id = track["album_id"]
        if album_id not in album_map:
            album_map[album_id] = {
                "id": album_id,
                "title": track["album_title"],
                "artist_id": track["album_artist_id"],
                "artist_name": track["artist_name"],
                "year": track["year"],
            }

        composer = _clean_credit_value(track.get("composer")) or "Unknown Composer"
        composer_counts[composer] = composer_counts.get(composer, 0) + 1

        work_title = _clean_credit_value(track.get("work_title")) or track["title"]
        work_key = (composer, work_title)
        if work_key not in work_counts:
            work_counts[work_key] = {
                "composer": composer,
                "work_title": work_title,
                "track_count": 0,
                "album_ids": set(),
            }
        work_counts[work_key]["track_count"] += 1
        work_counts[work_key]["album_ids"].add(album_id)

    works = []
    for item in work_counts.values():
        works.append({
            "composer": item["composer"],
            "work_title": item["work_title"],
            "track_count": item["track_count"],
            "album_count": len(item["album_ids"]),
        })
    works.sort(key=lambda item: (-item["track_count"], item["composer"].lower(), item["work_title"].lower()))

    composers = [
        {"name": composer, "track_count": count}
        for composer, count in sorted(composer_counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]

    albums = sorted(album_map.values(), key=lambda item: (-(item["year"] or 0), item["title"].lower()))

    return {
        "role": field,
        "label": VALID_CREDIT_FIELDS[field],
        "name": name,
        "track_count": len(matched_tracks),
        "album_count": len(album_map),
        "work_count": len(work_counts),
        "total_duration_hours": round(total_duration / 3600, 1),
        "composers": composers,
        "works": works[:60],
        "albums": albums[:36],
        "tracks": matched_tracks[:limit],
    }
