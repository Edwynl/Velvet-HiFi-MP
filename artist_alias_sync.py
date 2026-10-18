"""Opt-in MusicBrainz artist alias import for Velvet's offline search index.

An artist's displayed local name is never changed. Remote metadata is fetched
only when the user explicitly selects "Sync aliases" for a specific artist.
Search itself never makes network requests. The importer first establishes a
verified MusicBrainz identity, then persists alternate spellings to the
existing artist_aliases table, whose triggers update track/catalog dirty queues.
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


def _romanized_sort_name(name: str) -> str:
    """Convert official sort-name 'Hsu, LaLa' into searchable 'LaLa Hsu'."""
    if not name or name.count(",") != 1:
        return ""
    family, given = (part.strip() for part in name.split(",", 1))
    return f"{given} {family}" if family and given else ""


def _identity_names(data: dict) -> list[str]:
    names = [data.get("name"), data.get("sort-name"),
             _romanized_sort_name(data.get("sort-name") or "")]
    names.extend(a.get("name") for a in data.get("aliases", [])
                 if isinstance(a, dict))
    return [str(n).strip() for n in names if isinstance(n, str) and n.strip()]


def _is_verified(data: dict, local_name: str) -> bool:
    """Never associate an uncertain MusicBrainz search hit with a local artist."""
    target = search_fold(local_name)
    return bool(target and any(search_fold(n) == target
                               for n in _identity_names(data)))


class AliasSyncUnavailable(Exception):
    """Remote lookup unavailable, without changing local artist records."""


async def _json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    try:
        resp = await client.get(url, params=params, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise AliasSyncUnavailable("Invalid MusicBrainz response")
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise AliasSyncUnavailable("MusicBrainz is unavailable; try again later") from exc


async def sync_artist_aliases(
    db: sqlite3.Connection,
    artist_id: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Import aliases only from a verified MusicBrainz artist.

    This function intentionally does not commit. The API caller commits when
    everything succeeds; tests can provide a fake HTTP client without network.
    MusicBrainz recommends no more than one request per second; two-request
    searches insert a delay. If the artist already has an MBID, only one lookup
    is made. Existing local aliases are never deleted.
    """
    row = db.execute("SELECT id, name, mbid FROM artists WHERE id=?", (artist_id,)).fetchone()
    if row is None:
        raise ValueError("Artist not found")
    artist_name, known_mbid = str(row[1]), row[2]
    if known_mbid:
        try:
            mbid = str(uuid.UUID(str(known_mbid)))
        except ValueError:
            return {"status": "invalid_mbid", "added": [], "aliases": fetch_aliases(db, artist_id)}
    else:
        mbid = None

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15)
    try:
        if not mbid:
            response = await _json(
                client, _MUSICBRAINZ + "/",
                {"query": artist_name, "fmt": "json", "limit": 10},
            )
            candidates = [
                candidate for candidate in response.get("artists", [])
                if isinstance(candidate, dict) and candidate.get("id")
                and _is_verified(candidate, artist_name)
            ]
            ids = {candidate["id"] for candidate in candidates}
            if len(ids) != 1:
                return {
                    "status": "ambiguous" if ids else "not_found",
                    "added": [], "aliases": fetch_aliases(db, artist_id),
                }
            try:
                mbid = str(uuid.UUID(str(next(iter(ids)))))
            except ValueError:
                return {"status": "invalid_mbid", "added": [],
                        "aliases": fetch_aliases(db, artist_id)}
            await asyncio.sleep(1.1)

        record = await _json(
            client, f"{_MUSICBRAINZ}/{mbid}",
            {"inc": "aliases", "fmt": "json"},
        )
        if str(record.get("id") or "").lower() != mbid.lower():
            return {"status": "identity_mismatch", "added": [],
                    "aliases": fetch_aliases(db, artist_id)}
        if not known_mbid and not _is_verified(record, artist_name):
            return {"status": "identity_mismatch", "added": [],
                    "aliases": fetch_aliases(db, artist_id)}

        return _store_verified_aliases(db, artist_id, artist_name, mbid, record,
                                       known_mbid=bool(known_mbid))
    finally:
        if own_client:
            await client.aclose()


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
    """Persist only verified names, respecting collisions with other artists."""
    current = {a.casefold() for a in fetch_aliases(db, artist_id)}
    added = []
    conflicts = []
    choices = [*_identity_names(record)]
    if query_alias and _is_verified(record, query_alias):
        choices.append(query_alias.strip())
    for name in dict.fromkeys(choices):
        if not 1 <= len(name) <= 128 or name.casefold() == artist_name.casefold():
            continue
        if name.casefold() in current:
            continue
        other = db.execute(
            "SELECT id FROM artists WHERE id != ? AND name = ? COLLATE NOCASE",
            (artist_id, name),
        ).fetchone()
        if other:
            conflicts.append(name)
            continue
        if add_alias(db, artist_id, name):
            current.add(name.casefold())
            added.append(name)
        else:
            conflicts.append(name)
    if not known_mbid:
        db.execute("UPDATE artists SET mbid=? WHERE id=?", (mbid, artist_id))
    return {
        "status": "ok", "artist_id": artist_id, "mbid": mbid,
        "added": added, "conflicts": conflicts,
        "aliases": fetch_aliases(db, artist_id),
    }


async def resolve_search_alias(
    db: sqlite3.Connection,
    query: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Explicit one-click fallback when an English stage name is not indexed.

    Search MusicBrainz for the typed alias; link it to an existing local artist
    only if both the remote alias and the remote/local artist identity verify.
    Never create/merge artists, and never query remotely while typing.
    """
    query = (query or "").strip()
    if not 1 <= len(query) <= 128:
        return {"status": "invalid_query"}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15)
    try:
        response = await _json(
            client, _MUSICBRAINZ + "/",
            {"query": query, "fmt": "json", "limit": 10},
        )
        candidates = [
            candidate for candidate in response.get("artists", [])
            if isinstance(candidate, dict) and candidate.get("id")
            and _is_verified(candidate, query)
        ]
        if not candidates:
            return {"status": "not_found"}
        local_artists = db.execute("SELECT id,name,mbid FROM artists").fetchall()
        pairs = {}
        for candidate in candidates:
            try:
                mbid = str(uuid.UUID(str(candidate["id"])))
            except ValueError:
                continue
            candidate_name = candidate.get("name") or ""
            for row in local_artists:
                # An existing MBID is the strongest identity link. Otherwise
                # require the same canonical primary artist name, not a guess.
                if (row[2] and str(row[2]).lower() == mbid.lower()) or (
                    candidate_name and search_fold(candidate_name) == search_fold(str(row[1]))
                ):
                    pairs[(int(row[0]), mbid)] = str(row[1])
        if len(pairs) != 1:
            return {"status": "ambiguous" if pairs else "not_in_library"}
        (artist_id, mbid), artist_name = next(iter(pairs.items()))
        await asyncio.sleep(1.1)
        record = await _json(client, f"{_MUSICBRAINZ}/{mbid}",
                             {"inc": "aliases", "fmt": "json"})
        if str(record.get("id") or "").lower() != mbid.lower():
            return {"status": "identity_mismatch"}
        if not _is_verified(record, query) or (
            not _is_verified(record, artist_name)
            and not db.execute("SELECT 1 FROM artists WHERE id=? AND mbid=?",
                               (artist_id, mbid)).fetchone()
        ):
            return {"status": "identity_mismatch"}
        known_mbid = db.execute(
            "SELECT mbid FROM artists WHERE id=?", (artist_id,)
        ).fetchone()[0]
        result = _store_verified_aliases(
            db, artist_id, artist_name, mbid, record,
            known_mbid=bool(known_mbid), query_alias=query,
        )
        return result
    finally:
        if own_client:
            await client.aclose()
