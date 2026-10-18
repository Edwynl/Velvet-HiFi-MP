"""Verified, opt-in MusicBrainz aliases for the offline Velvet music search.

Strong identity evidence is an existing local MusicBrainz artist ID matching
a detailed MusicBrainz record *and* its locally stored artist name. A plain
name match in MusicBrainz search results is NOT an identity match: two
different musicians may share a stage name, and the search result is capped.
Such candidates always require an explicit human confirmation of their MBID.
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid

import httpx

from artist_aliases import add_alias, fetch_aliases
from chinese_variants import search_fold

_MUSICBRAINZ = "https://musicbrainz.org/ws/2/artist"
_HEADERS = {"User-Agent": "VelvetHiFi/3.0 (https://github.com/Edwynl/Velvet-HiFi-MP)"}
_MAX_CANDIDATES = 10


def _romanized_sort_name(name: str) -> str:
    """Turn MusicBrainz sort name 'Hsu, LaLa' into 'LaLa Hsu'."""
    if not name or name.count(",") != 1:
        return ""
    family, given = (part.strip() for part in name.split(",", 1))
    return f"{given} {family}" if family and given else ""


def _identity_names(data: dict) -> list[str]:
    names = [
        data.get("name"),
        data.get("sort-name"),
        _romanized_sort_name(data.get("sort-name") or ""),
    ]
    names.extend(
        entry.get("name") for entry in (data.get("aliases") or [])
        if isinstance(entry, dict)
    )
    return [name.strip() for name in names if isinstance(name, str) and name.strip()]


def _is_verified(data: dict, candidate_name: str) -> bool:
    value = search_fold(candidate_name)
    return bool(value and any(search_fold(name) == value for name in _identity_names(data)))


def _mbid(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _public_candidate(candidate: dict) -> dict:
    """Information for a user to explicitly verify; never blindly adopt it."""
    return {
        "mbid": _mbid(candidate.get("id")) or "",
        "name": str(candidate.get("name") or "")[:128],
        "disambiguation": str(candidate.get("disambiguation") or "")[:200],
        "country": str(candidate.get("country") or "")[:60],
        "type": str(candidate.get("type") or "")[:60],
    }


class AliasSyncUnavailable(Exception):
    """MusicBrainz request unavailable; caller may retry without modifying DB."""


async def _json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    try:
        response = await client.get(url, params=params, headers=_HEADERS)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise AliasSyncUnavailable("Invalid MusicBrainz response")
        return value
    except (httpx.HTTPError, ValueError) as exc:
        raise AliasSyncUnavailable("MusicBrainz is unavailable; try again later") from exc


async def _detailed(client, mbid: str) -> dict:
    return await _json(
        client, f"{_MUSICBRAINZ}/{mbid}", {"inc": "aliases", "fmt": "json"},
    )


def _store_verified_aliases(
    db: sqlite3.Connection,
    artist_id: int,
    artist_name: str,
    mbid: str,
    record: dict,
    *,
    known_mbid: bool,
    query_alias: str = "",
) -> dict:
    """Save only names from the verified record; never change display names."""
    current = {value.casefold() for value in fetch_aliases(db, artist_id)}
    added = []
    conflicts = []
    choices = _identity_names(record)
    if query_alias and _is_verified(record, query_alias):
        choices.append(query_alias.strip())
    for name in dict.fromkeys(choices):
        if not 1 <= len(name) <= 128 or name.casefold() == artist_name.casefold():
            continue
        if name.casefold() in current:
            continue
        collision = db.execute(
            "SELECT id FROM artists WHERE id != ? AND name = ? COLLATE NOCASE",
            (artist_id, name),
        ).fetchone()
        if collision:
            conflicts.append(name)
            continue
        if add_alias(db, artist_id, name):
            current.add(name.casefold())
            added.append(name)
        else:
            conflicts.append(name)
    if not known_mbid:
        db.execute("UPDATE artists SET mbid=? WHERE id=?", (mbid, artist_id))
    # After the MBID write: its trigger clears the flag whenever the ID changes.
    db.execute("UPDATE artists SET mbid_verified=1 WHERE id=?", (artist_id,))
    return {
        "status": "ok", "artist_id": artist_id, "mbid": mbid,
        "aliases": fetch_aliases(db, artist_id),
        "added": added, "conflicts": conflicts,
    }


async def sync_artist_aliases(
    db: sqlite3.Connection,
    artist_id: int,
    *,
    client: httpx.AsyncClient | None = None,
    confirmed_mbid: str | None = None,
    query_alias: str = "",
) -> dict:
    """Import aliases if identity is trusted or explicitly confirmed.

    Without a user-confirmed local MBID, the *first* call returns a
    human-review candidate shortlist; it never persists a search-result-only
    identity. An unconfirmed stored MBID (bio enrichment saves the first
    name-search hit) is not evidence either: a namesake's record matches the
    local name too. A subsequent explicit-confirmation call with an MBID
    fetches the detailed record again and validates its name. Callers own the
    transaction.
    """
    row = db.execute(
        "SELECT id, name, mbid, mbid_verified FROM artists WHERE id=?", (artist_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Artist not found")
    artist_name, local_raw = str(row[1]), row[2]
    local_mbid = _mbid(local_raw) if local_raw else None
    if local_raw and not local_mbid:
        return {"status": "invalid_mbid", "added": [], "aliases": fetch_aliases(db, artist_id)}
    trusted_mbid = local_mbid if row[3] else None
    confirmed = _mbid(confirmed_mbid) if confirmed_mbid else None
    if confirmed_mbid and not confirmed:
        return {"status": "invalid_mbid", "added": [], "aliases": fetch_aliases(db, artist_id)}
    if confirmed and trusted_mbid and confirmed != trusted_mbid:
        return {"status": "identity_mismatch", "added": [],
                "aliases": fetch_aliases(db, artist_id)}
    mbid = trusted_mbid or confirmed

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15)
    try:
        if not mbid:
            result = await _json(
                client, _MUSICBRAINZ + "/",
                {"query": artist_name, "fmt": "json", "limit": _MAX_CANDIDATES},
            )
            candidates = []
            seen = set()
            for candidate in result.get("artists") or []:
                if not isinstance(candidate, dict):
                    continue
                proposed = _public_candidate(candidate)
                if not proposed["mbid"] or proposed["mbid"] in seen:
                    continue
                # A short search response is not a complete identity record.
                # Even one exact-name candidate requires human confirmation.
                seen.add(proposed["mbid"])
                candidates.append(proposed)
            return {
                "status": "needs_review" if candidates else "not_found",
                "artist_id": artist_id,
                "candidates": candidates,
                "added": [], "aliases": fetch_aliases(db, artist_id),
            }

        record = await _detailed(client, mbid)
        if _mbid(record.get("id")) != mbid:
            return {"status": "identity_mismatch", "added": [],
                    "aliases": fetch_aliases(db, artist_id)}
        if not _is_verified(record, artist_name):
            return {"status": "identity_mismatch", "added": [],
                    "aliases": fetch_aliases(db, artist_id)}
        if query_alias and not _is_verified(record, query_alias):
            return {"status": "identity_mismatch", "added": [],
                    "aliases": fetch_aliases(db, artist_id)}
        # Reject another local artist already carrying this MusicBrainz ID.
        duplicate = db.execute(
            "SELECT id FROM artists WHERE id!=? AND mbid=?",
            (artist_id, mbid),
        ).fetchone()
        if duplicate:
            return {"status": "identity_mismatch", "added": [],
                    "aliases": fetch_aliases(db, artist_id)}
        return _store_verified_aliases(
            db, artist_id, artist_name, mbid, record,
            known_mbid=(mbid == local_mbid), query_alias=query_alias,
        )
    finally:
        if own_client:
            await client.aclose()


async def _pace(client, *, first: bool) -> None:
    # The batch client owns its own >=1.25s pacing; single lookup has two or
    # more requests and must not issue them faster than MusicBrainz allows.
    if not first and not getattr(client, "rate_limited", False):
        await asyncio.sleep(1.1)


async def resolve_search_alias(
    db: sqlite3.Connection,
    query: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Explicit opt-in lookup for a Latin name absent from local search.

    Search candidate snippets may omit aliases. Fetch each detailed candidate
    first, then verify both the typed name and local artist. Never auto-bind
    a candidate based on a shared primary artist name alone.
    """
    query = (query or "").strip()
    if not 1 <= len(query) <= 128:
        return {"status": "invalid_query"}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15)
    try:
        hits = await _json(
            client, _MUSICBRAINZ + "/",
            {"query": query, "fmt": "json", "limit": _MAX_CANDIDATES},
        )
        candidates = []
        seen = set()
        for candidate in hits.get("artists") or []:
            if not isinstance(candidate, dict):
                continue
            mbid = _mbid(candidate.get("id"))
            if mbid and mbid not in seen:
                seen.add(mbid)
                candidates.append((mbid, candidate))
        if not candidates:
            return {"status": "not_found"}

        # Only a user-confirmed MBID can rule a local artist in or out; an
        # unconfirmed one may be a namesake's ID from name-only enrichment.
        locals_ = db.execute(
            "SELECT id,name,CASE WHEN mbid_verified THEN mbid END FROM artists"
        ).fetchall()
        pairs: dict[tuple[int, str], tuple[str, dict]] = {}
        for position, (mbid, hit) in enumerate(candidates):
            await _pace(client, first=False)
            record = await _detailed(client, mbid)
            if _mbid(record.get("id")) != mbid or not _is_verified(record, query):
                continue
            for row in locals_:
                artist_id, local_name, local_raw = int(row[0]), str(row[1]), row[2]
                local_mbid = _mbid(local_raw) if local_raw else None
                if local_mbid and local_mbid != mbid:
                    continue
                if not _is_verified(record, local_name):
                    continue
                pairs[(artist_id, mbid)] = (local_name, record)

        if not pairs:
            return {"status": "not_in_library"}
        if len(pairs) != 1:
            return {"status": "ambiguous"}
        (artist_id, mbid), (local_name, record) = next(iter(pairs.items()))
        local = db.execute(
            "SELECT mbid FROM artists WHERE id=? AND mbid_verified", (artist_id,),
        ).fetchone()
        if local is not None and _mbid(local[0]) == mbid:
            return _store_verified_aliases(
                db, artist_id, local_name, mbid, record,
                known_mbid=True, query_alias=query,
            )
        # No trustworthy MBID: return proposal, let the UI explicitly confirm.
        return {
            "status": "needs_review", "artist_id": artist_id,
            "query_alias": query,
            "candidate": _public_candidate(record),
        }
    finally:
        if own_client:
            await client.aclose()
