"""Verified, opt-in MusicBrainz aliases for the offline Velvet music search.

Strong identity evidence is an existing local MusicBrainz artist ID matching
a detailed MusicBrainz record *and* its locally stored artist name. A plain
name match in MusicBrainz search results is NOT an identity match: two
different musicians may share a stage name, and the search result is capped.
Such candidates always require an explicit human confirmation of their MBID.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import uuid

import httpx

from artist_aliases import add_alias, fetch_aliases
from artist_alias_provenance import mark_alias_source
from chinese_variants import get_all_variants, search_fold

_MUSICBRAINZ = "https://musicbrainz.org/ws/2/artist"
_HEADERS = {"User-Agent": "VelvetHiFi/3.0 (https://github.com/Edwynl/Velvet-HiFi-MP)"}
_MAX_CANDIDATES = 10
# Each detailed record is a separate, paced (>=1.1s) MusicBrainz request made
# while the user waits. Search results arrive best-scored first, so a few
# lookups keep the click well inside the client's timeout.
_MAX_RESOLVE_LOOKUPS = 5
_RELEASE_GROUPS = "https://musicbrainz.org/ws/2/release-group"
_MAX_AUTO_CANDIDATES = 4
# Search has a hard service maximum of 100 per page. We deliberately check
# *all* results for the constrained name query, not only its relevance-sorted
# first page. A name returning more records than our safe cap needs review.
_EXACT_ARTIST_PAGE_SIZE = 25
_MAX_EXACT_ARTIST_RESULTS = 100
_RELEASE_GROUP_PAGE_SIZE = 100
_MAX_RELEASE_GROUP_RESULTS = 200
_GENERIC_ALBUMS = frozenset({
    "greatesthits", "bestof", "thebestof", "essential", "live",
    "compilation", "singles", "single", "various", "collection",
    "精选", "精選", "精选集", "专辑", "專輯", "演唱会", "演唱會",
    "thecollection", "anthology", "vol1", "volume1",
})


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


def _identity_keys(data: dict) -> set[str]:
    """Folded identity names, computed once per record."""
    return {key for key in map(search_fold, _identity_names(data)) if key}


def _is_verified(data: dict, candidate_name: str) -> bool:
    return search_fold(candidate_name) in _identity_keys(data)


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



# Edition notes ("(Deluxe Edition)", "[Remastered]", "（豪華版）") differ between
# a file tag and the MusicBrainz release-group title of the same album.
_EDITION_NOTE = re.compile(r"[(\[（【][^)\]）】]*[)\]）】]")


def _album_key(title: str) -> str:
    """Conservative exact-title comparison, tolerant of script/punctuation."""
    title = _EDITION_NOTE.sub(" ", title)
    key = "".join(c for c in search_fold(title).casefold() if c.isalnum())
    if len(key) < 3 or key in _GENERIC_ALBUMS:
        return ""
    if key.startswith(("greatesthits", "bestof", "thebestof")):
        return ""
    return key


def _distinctive_local_albums(db: sqlite3.Connection, artist_id: int) -> dict[str, str]:
    """At most 200 local original album titles: never use track-name guesses."""
    found = {}
    for (title,) in db.execute(
        "SELECT title FROM albums WHERE artist_id=? ORDER BY id LIMIT 200",
        (artist_id,),
    ):
        key = _album_key(str(title or ""))
        if key:
            found.setdefault(key, str(title))
    return found


def _release_matches(data: dict, mbid: str, local: dict[str, str]) -> list[str]:
    """Count only full-length/EP release groups credited to this exact artist."""
    matched = set()
    for item in data.get("release-groups") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("primary-type") or "").lower() not in ("album", "ep"):
            continue
        # Request inc=artist-credits. Shared/compilation release groups are not
        # independent identity evidence for a primary local singer.
        credit = [
            _mbid(part.get("artist", {}).get("id"))
            for part in (item.get("artist-credit") or [])
            if isinstance(part, dict) and isinstance(part.get("artist"), dict)
        ]
        if credit != [mbid]:
            continue
        key = _album_key(str(item.get("title") or ""))
        if key in local:
            matched.add(key)
    return sorted(matched)


# Lucene phrase-query metacharacters. URL encoding alone does not make a
# literal artist-field query safe: punctuation must be escaped for Lucene too.
_LUCENE_SPECIAL = frozenset(r'+-!(){}[]^"~*?:\/|&')


def _artist_field_query(name: str) -> str:
    escaped = "".join("\\" + char if char in _LUCENE_SPECIAL else char
                      for char in name)
    return f'artist:"{escaped}"'


async def _complete_exact_name_candidates(
    artist_name: str, client,
) -> list[str] | None:
    """Exhaustively page bounded, field-scoped MusicBrainz artist searches.

    A generic MusicBrainz query has millions of fuzzy matches; its relevance
    rank and fuzzy page tail CANNOT prove the absence of a namesake. Query the
    documented artist: field with an escaped phrase (plus OpenCC name
    variants), consume *all* matching pages, then compare authoritative
    primary names locally. If any query is too broad, malformed or incomplete
    the identity is unsafe to auto-confirm and must be reviewed by a person.
    """
    target = search_fold(artist_name)
    seen: dict[str, dict] = {}
    # Fixed-point OpenCC folding is not always a single conversion step
    # (陳昇 -> 陈昇 -> 陈升). Enumerate both original and folded variants so
    # an equivalent-script namesake cannot escape the exhaustive search.
    variants = list(dict.fromkeys(
        variant.strip()
        for spelling in (artist_name, search_fold(artist_name))
        for variant in get_all_variants(spelling)
        if variant and variant.strip()
    ))
    for variant in variants:
        query = _artist_field_query(variant)
        offset = 0
        total = None
        query_seen: set[str] = set()
        while total is None or offset < total:
            await _pace(client, first=False)
            page = await _json(
                client, _MUSICBRAINZ + "/",
                {"query": query, "fmt": "json",
                 "limit": _EXACT_ARTIST_PAGE_SIZE, "offset": offset},
            )
            count = page.get("count")
            received_offset = page.get("offset")
            artists = page.get("artists")
            if (type(count) is not int or count < 0
                    or count > _MAX_EXACT_ARTIST_RESULTS
                    or type(received_offset) is not int
                    or received_offset != offset
                    or not isinstance(artists, list)
                    or len(artists) > _EXACT_ARTIST_PAGE_SIZE):
                return None
            if total is None:
                total = count
            elif total != count:
                return None
            if (offset + len(artists) > total
                    or (offset < total and not artists)):
                return None
            for item in artists:
                if not isinstance(item, dict):
                    return None
                mbid = _mbid(item.get("id"))
                if not mbid or mbid in query_seen:
                    # Duplicate IDs across pages may mean the index changed
                    # during pagination. Completeness is not established.
                    return None
                query_seen.add(mbid)
                if search_fold(str(item.get("name") or "")) == target:
                    seen[mbid] = item
                    if len(seen) > _MAX_AUTO_CANDIDATES:
                        return None
            offset += len(artists)
    return list(seen)


async def _complete_release_groups(
    mbid: str, client,
) -> dict | None:
    """Read all release groups within a bounded window before comparison.

    A second namesake whose matching releases fall after the first 100 must
    not be silently ignored. Large/incomplete discographies require review.
    """
    groups: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        await _pace(client, first=False)
        page = await _json(
            client, _RELEASE_GROUPS + "/",
            {"artist": mbid, "type": "album|ep",
             "limit": _RELEASE_GROUP_PAGE_SIZE, "offset": offset,
             "inc": "artist-credits", "fmt": "json"},
        )
        count = page.get("release-group-count")
        got_offset = page.get("release-group-offset")
        batch = page.get("release-groups")
        if (type(count) is not int or count < 0
                or count > _MAX_RELEASE_GROUP_RESULTS
                or type(got_offset) is not int or got_offset != offset
                or not isinstance(batch, list)
                or len(batch) > _RELEASE_GROUP_PAGE_SIZE):
            return None
        if total is None:
            total = count
        elif count != total:
            return None
        if offset + len(batch) > total or (offset < total and not batch):
            return None
        groups.extend(batch)
        offset += len(batch)
    return {"release-groups": groups}


async def _corroborate_release_groups(
    db: sqlite3.Connection, artist_id: int, artist_name: str,
    response: dict, client,
) -> tuple[str, list[str]] | None:
    """Use album evidence only after every same-name artist is enumerated.

    The broad first-page response is solely for manual-review suggestions;
    auto-verification must use exhaustive, constrained artist-field search.
    """
    local = _distinctive_local_albums(db, artist_id)
    if len(local) < 2:
        return None
    eligible = await _complete_exact_name_candidates(artist_name, client)
    if not eligible:
        return None

    qualified: list[tuple[str, list[str]]] = []
    for mbid in eligible:
        data = await _complete_release_groups(mbid, client)
        if data is None:
            return None  # A namesake might match outside a truncated browse.
        matched = _release_matches(data, mbid, local)
        if len(matched) >= 2:
            qualified.append((mbid, matched))
    return qualified[0] if len(qualified) == 1 else None


def _store_verified_aliases(
    db: sqlite3.Connection,
    artist_id: int,
    artist_name: str,
    mbid: str,
    record: dict,
    *,
    known_mbid: bool,
    query_alias: str = "",
    evidence_method: str = "trusted_mbid",
    evidence_albums: tuple[str, ...] = (),
) -> dict:
    """Save only names from a verified identity; never change display names."""
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
            mark_alias_source(
                db, artist_id, name, evidence_method, mbid=mbid,
                evidence="; ".join(evidence_albums),
            )
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
        "verification": evidence_method,
        "evidence_albums": list(evidence_albums),
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
    evidence_albums: tuple[str, ...] = ()
    evidence_method = "user_confirmed" if confirmed else "trusted_mbid"

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
            # Independent album evidence can confirm an artist without
            # asking the user to approve thousands of name-only suggestions.
            # No aliases/MBID are stored until a unique candidate is proven
            # by two distinct, non-generic local release-group titles.
            confirmed_release = await _corroborate_release_groups(
                db, artist_id, artist_name, result, client,
            )
            if not confirmed_release:
                return {
                    "status": "needs_review" if candidates else "not_found",
                    "artist_id": artist_id,
                    "candidates": candidates,
                    "added": [], "aliases": fetch_aliases(db, artist_id),
                }
            mbid, matched_keys = confirmed_release
            local_titles = _distinctive_local_albums(db, artist_id)
            evidence_albums = tuple(local_titles[k] for k in matched_keys)
            evidence_method = "release_corroborated"

        # Deliberately pace lookup after search/browse, unless the caller
        # already uses the batch-wide rate limiter.
        if evidence_albums:
            await _pace(client, first=False)
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
            evidence_method=evidence_method, evidence_albums=evidence_albums,
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
    detail_offset: int = 0,
) -> dict:
    """Explicit opt-in lookup for a Latin name absent from local search.

    Search candidate snippets may omit aliases. Fetch each detailed candidate
    first, then verify both the typed name and local artist. Never auto-bind
    a candidate based on a shared primary artist name alone.
    """
    query = (query or "").strip()
    if not 1 <= len(query) <= 128 or not 0 <= detail_offset < _MAX_CANDIDATES:
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
        # Fold every local name once, not once per candidate record.
        locals_ = []
        for artist_id, local_name, local_raw in db.execute(
            "SELECT id,name,CASE WHEN mbid_verified THEN mbid END FROM artists"
        ):
            local_key = search_fold(str(local_name))
            if local_key:
                local_mbid = _mbid(local_raw) if local_raw else None
                locals_.append((int(artist_id), str(local_name), local_mbid, local_key))
        query_key = search_fold(query)
        pairs: dict[tuple[int, str], tuple[str, dict]] = {}
        # A phone should wait for no more than five detailed lookups per
        # click. A user can explicitly request the remaining candidates.
        page_end = detail_offset + _MAX_RESOLVE_LOOKUPS
        for mbid, hit in candidates[detail_offset:page_end]:
            await _pace(client, first=False)
            record = await _detailed(client, mbid)
            identity = _identity_keys(record)
            if _mbid(record.get("id")) != mbid or query_key not in identity:
                continue
            for artist_id, local_name, local_mbid, local_key in locals_:
                if local_mbid and local_mbid != mbid:
                    continue
                if local_key in identity:
                    pairs[(artist_id, mbid)] = (local_name, record)

        if not pairs:
            if page_end < len(candidates):
                return {
                    "status": "more_candidates", "next_offset": page_end,
                    "remaining": len(candidates) - page_end,
                }
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
                evidence_method="trusted_mbid",
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
