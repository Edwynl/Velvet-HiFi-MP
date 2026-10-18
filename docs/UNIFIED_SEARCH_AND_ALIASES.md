# Velvet unified search + artist aliases (PR #17)

## User-visible behavior

The same topbar input now searches the **entire library** from Home,
Discover, Albums, Artists, Tracks, Composers, Works, Credits, or Genres.

Global search results are grouped and displayed in this exact order:

1. **Artists** — tap an artist card to open the artist page.
2. **Albums** — tap an album cover to open the album.
3. **Tracks** — tap a row to start playback with the existing queue behavior.

The search API returns a backward-compatible `tracks` list and additional
`artists`, `albums`, `total_artists`, `total_albums`, and `total_tracks`.
Each section has **Load more** when the first page is truncated; the button
requests only that category from `/api/search/category` with `kind`,
`q`, `limit`, and `offset`. This preserves the global Artists → Albums →
Tracks grouping without downloading the entire catalog to phones/tablets. Input is capped at
128 characters. Empty search does not query the library.

Artists and albums rank an exact name, title or alias first, then names that
start with the query, then other matches; tracks rank an exact canonical
title first. The chosen sort orders results of equal rank.

Specialized classical-library screens retain a **separate local Filter** field
for Composers, Works, Credits, and Genres. It is intentionally a literal
filter of displayed cards; full OpenCC conversion is done by the global search.

## Artist aliases (why LaLa Hsu differs from 徐佳瑩)

- OpenCC normalizes `徐佳瑩` and `徐佳莹` but cannot infer stage names.
- Artist detail offers **+ Alias**: the user adds a stage, translated or
  romanized name, e.g. `LaLa Hsu` for `徐佳瑩`. It is stored in the existing
  `artist_aliases` table linked to the artist ID; FLAC/ID3 tags and display
  names are never changed.
- Alias triggers queue the artist's tracks, artist and album index rows.
  Queued rows are matched from live data, so a new alias is searchable in all
  three categories immediately. The queue is then indexed in the background
  in short committed passes (≤800 rows each), so a popular artist never holds
  SQLite's writer in one long transaction. A running scan, duplicate check or
  album change refreshes on its own instead.
- A name that already belongs to another artist is refused (HTTP 409).
- When a search finds nothing, the empty state points to **+ Alias**. Search
  never contacts any remote service.

## MusicBrainz alias identity (verified, opt-in)

This is the MusicBrainz / MBID half split out of the original #17; it builds on
the search system merged in #25.

Artist detail offers **Sync Names** (opt-in MusicBrainz) next to **+ Alias**. The
global search empty state offers **Find artist by another name**. Each click
examines at most five detailed MusicBrainz candidates; if the correct alias sits
in candidates 6–10, **Check more MusicBrainz candidates** offers an explicit
second lookup. **No network request occurs while typing.**

- If an artist has an independently verified MBID, its detailed record is checked
  against the local name and imported into offline search. **Without one**, Velvet
  attempts release corroboration: at least **two distinct, non-generic local album
  titles** must match release groups credited solely to **one unique**
  name-matched MusicBrainz artist.
- For identity safety the server issues **field-scoped Lucene phrase searches**
  (`artist:"..."`) for the original, simplified, traditional and fixed-point
  OpenCC name variants, escapes Lucene punctuation, and **paginates all matching
  records**. The broad search's first-page ordering is NEVER used as proof that
  no other same-named artist exists. A result exceeding 100 name-scoped records, a
  missing count/offset, an inconsistent page, or more than four exactly named
  artists requires **human review**. Each candidate's album/EP release groups are
  fully paginated too (up to 200 records) so a namesake with matching albums
  cannot be overlooked. If completeness cannot be established, aliases and MBID
  are left unchanged. Everyone else enters **Review identities**.
- Confirming a candidate re-fetches the authoritative MusicBrainz record and
  verifies the local artist name and any submitted query alias, then persists its
  other spellings including a sort name like `Hsu, LaLa`. **No FLAC/ID3 tags or
  artist display names are changed.**
- Existing primary artist names and aliases cannot silently be hijacked:
  conflicting aliases are skipped and wrong MBIDs are rejected.
- Internet is accessed **only when Sync Names is clicked**. Every subsequent
  search is fully local/offline.
- Explicit **+ Alias** remains available for stage names absent from MusicBrainz.

## Batch alias enrichment (Artists page)

The Artists page has a user-controlled MusicBrainz enrichment panel:

- **Sync next 25** — fetch a small batch of unprocessed local artists.
- **Sync all remaining** — explicitly confirm a potentially hours-long task, paced
  at least 1.25 s between MusicBrainz requests, capped at 20,000 artist records.
- **Retry unresolved** — recheck network failures and genuinely unresolved
  non-review outcomes. Artists marked `needs_review` are not blindly retried.
- **Review identities** — browse name-only candidates and explicitly verify their
  MusicBrainz identity on the artist detail page.
- **Audit old aliases** — inspect unknown-origin aliases from previous versions
  and explicitly KEEP or REMOVE one entry at a time.
- **Stop** — cooperative, after the currently active remote lookup. No audio file
  is changed.

Migration 14 records per-artist checkpoints; migration 15 marks existing MBIDs
unverified; migration **16** adds alias provenance (`manual`, `user_confirmed`,
`trusted_mbid`, `release_corroborated`, `reviewed_legacy`). Historical aliases
without provenance are retained and explicitly listed for audit — never silently
declared correct or deleted. Every artist is committed separately, so the next
batch resumes the unprocessed set even after a server restart.

No MusicBrainz calls happen on application startup or ordinary searches. Results
may be incomplete for artists with no verifiable MusicBrainz identity, including
artists with common names. Never guess or merge artists. An outage pauses the
batch to avoid hammering MusicBrainz.

## API

- `GET /api/search?q=...&limit=80` — Artists, Albums and Tracks.
- `GET /api/search/category?kind=albums&q=name&limit=40&offset=24` —
  continue one search group without loading all categories.
- `POST /api/artists/{artist_id}/aliases` with JSON `{"name":"LaLa Hsu"}` —
  add one alias.

## Schema

- Migration **12**: `catalog_search_index` / `catalog_search_dirty` with
  artist/album keys and alias triggers.
- Migration **13**: `track_search_index.title_key` for ranking and
  `catalog_search_index.genre_key` for script-aware genre matching.
- Migration **14**: `artist_alias_enrichment_state` per-artist batch checkpoints.
- Migration **15**: `artists.mbid_verified` + a trigger clearing it on MBID change.
- Migration **16**: `artist_alias_provenance` (source, mbid, evidence) with the
  legacy-unknown audit path. `LATEST_SCHEMA_VERSION` is 16.

## Review / local Windows checks

Run locally; do **not** trigger GitHub Actions.

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
npm run test:frontend
```

Acceptance cases:

- `徐佳莹` finds stored `徐佳瑩` in Artists, associated Albums, and Tracks.
- After **+ Alias** `LaLa Hsu` on 徐佳瑩, `LaLa Hsu` and `lala hsu` produce
  the same three categories immediately, before reindex.
- `爱你` ranks `愛你` ahead of `愛`; literal `a_b` and `100%` remain
  literal in prefix ranking.
- A Latin query does not match in reverse: artist `Low` is not a result for
  `Yellow`.
- Dirty album genres, aliases, and metadata edits are correct before rebuild.
- Typing in **Albums** or **Artists** topbar takes the user to the same
  global result sections, not an album-only/artist-only filter.
- Artist and album cards retain navigation; track rows retain direct playback.
- iOS/Safari PWA typing (including Chinese IME) does not show stale results.
- **Load more** independently continues >12 Artists, >24 Albums and >80
  Tracks; clicking an appended track preserves correct queue index.

Benchmark: the three-category search adds two catalog queries to the old
track-only route. Measure **p50/p95/p99** on a cloned 66k+ track DB,
especially single-character / 1-2 Chinese character queries and searches
matching thousands of artists/albums.

All changes to this feature are confined to search/catalog/alias/UI code.
Do not modify DSP, VU meters, DLNA, or FLAC/ID3 metadata.
