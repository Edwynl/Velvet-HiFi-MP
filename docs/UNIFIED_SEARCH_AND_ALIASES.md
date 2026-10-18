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

Specialized classical-library screens retain a **separate local Filter** field
for Composers, Works, Credits, and Genres. It is intentionally a literal
filter of displayed cards; full OpenCC conversion is done by the global search.

## Alias identity (why LaLa Hsu differs from 徐佳瑩)

- OpenCC normalizes `徐佳瑩` and `徐佳莹` but cannot infer stage names.
- Aliases are persisted in existing `artist_aliases` linked to the artist ID.
  Its triggers dirty track, artist and album index rows immediately.
- Artist detail now offers **Sync Names** (opt-in MusicBrainz) and **+ Alias**.
- The global search empty state offers **Find artist by another name**.
  This checks up to ten candidate MusicBrainz details to avoid missing an
  English name whose search summary omits aliases. A candidate without a
  *previously stored trusted MBID* requires **explicit user confirmation**
  before it is linked; no silent name-based artist matching.
- If an artist has a matching trusted MBID, the detailed record is verified
  against the local artist's original name and imported into offline search.
  If the local artist has **no MBID**, `Sync Names` shows candidate name,
  disambiguation, country, type and MBID and waits for the user to select the
  correct artist. Two different singers can share a name.
- **Review identities** on the Artists page lists unconfirmed artists from
  the batch, opens their artist page, and guides the user to the same manual
  confirmation flow. A `needs_review` checkpoint is not automatically retried
  or treated as successfully matched.
- Confirming a candidate re-fetches the authoritative MusicBrainz record and
  verifies the local artist name and any submitted query alias. It then
  persists its other spellings, including a sort name like `Hsu, LaLa` and
  `LaLa Hsu`. No FLAC/ID3 tags or artist display names are changed.
- Existing primary artist names and aliases cannot silently be hijacked:
  conflicting aliases are skipped, and wrong MBIDs are rejected.
- Internet is accessed **only when Sync Names is clicked**. Every subsequent
  search is fully local/offline; a missing album cover or artist image does not
  affect matching.
- Explicit + Alias is available for stage names absent from MusicBrainz. It is
  not a hardcoded Chinese conversion dictionary.

## Batch alias enrichment (Artists page)

The Artists page now has a user-controlled MusicBrainz enrichment panel:

- **Sync next 25** — fetch a small batch of unprocessed local artists.
- **Sync all remaining** — explicitly confirm a potentially hours-long task,
  paced at least 1.25 seconds between MusicBrainz requests; the worker caps a
  single selection at 20,000 artist records.
- **Retry unresolved** — recheck network failures and genuinely unresolved
  non-review outcomes. Artists marked `needs_review` are not blindly retried.
- **Review identities** — browse name-only candidates and explicitly verify
  their MusicBrainz identity on the artist detail page.
- **Stop** — cooperative, after the currently active remote artist lookup.
  No audio file is changed.

Schema migration 14 records per-artist checkpoints with status, timestamp,
last error and imported aliases count. Every artist is committed separately;
the next batch resumes the unprocessed set even after server restart. Job
progress survives *navigation*; a process restart resets in-memory current-job
progress but retains durable per-artist coverage.

No MusicBrainz calls happen on application startup or ordinary searches.
Results may be incomplete if the local artist has no verifiable MusicBrainz
identity, including artists with common names. Never guess or merge artists.
An outage pauses the batch to avoid hammering MusicBrainz. Existing alias
records and FLAC tags remain untouched.

API:
- `GET /api/library/artist-aliases/enrichment` — progress/coverage.
- `POST /api/library/artist-aliases/enrichment/start` with JSON
  `{"mode":"pending","limit":25,"all_remaining":false}` — start.
- `GET /api/library/artist-aliases/enrichment/review` — awaiting user check.
- `POST /api/artists/{artist_id}/aliases/confirm` with JSON
  `{"mbid":"uuid","query_alias":"optional name"}` — explicit confirmation,
  followed by a fresh MusicBrainz detail verification.
- `GET /api/search/category?kind=albums&q=name&limit=40&offset=24` —
  continue one search group without loading all categories.
- `POST /api/library/artist-aliases/enrichment/stop` — stop.
- Retry with `{"mode":"retry","limit":25}`.

## Review / local Windows checks

Run locally; do **not** trigger GitHub Actions. Keep PR #17 Draft until
Codex reviews the new commits and local verification is complete.

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_artist_alias_batch.py tests/test_unified_search.py tests/test_search_opencc_v2.py tests/test_server_api.py -q
.\venv\Scripts\python.exe -m pytest tests -q
npm run test:frontend
```

Acceptance cases:

- `徐佳莹` finds stored `徐佳瑩` in Artists, associated Albums, and Tracks.
- After **Sync Names** on verified artist 徐佳瑩, `LaLa Hsu` and `lala hsu`
  produce the same three categories. Verify on a copy of the real library.
- When `Lala Hsu` has no initial local result, the optional one-click
  MusicBrainz lookup returns a *review candidate*, requests explicit approval,
  and only then links it to the existing local artist.
- After explicit **+ Alias**, results are immediately correct before reindex.
- `爱你` ranks `愛你` ahead of `愛`; literal `a_b` and `100%` remain
  literal in prefix ranking.
- Dirty album genres, aliases, and metadata edits are correct before rebuild.
- Typing in **Albums** or **Artists** topbar takes the user to the same
  global result sections, not an album-only/artist-only filter.
- Artist and album cards retain navigation; track rows retain direct playback.
- iOS/Safari PWA typing (including Chinese IME) does not show stale results.
- **Load more** independently continues >12 Artists, >24 Albums and >80
  Tracks; clicking an appended track preserves correct queue index.
- Transient 503/Tailscale failures recover Artists-page progress polling.
- Rate pacing is tested at 0.0, 1.25 and 2.50 seconds with a virtual clock,
  plus real-shaped HTTP 429 pause and resumable checkpoints.
- Batch import performs small committed index refreshes **between artists**,
  drains remaining dirty rows without a long SQLite writer transaction, and
  does not modify cover-art quality.

Benchmark: the new three-category search adds two catalog queries to the
old track-only route. Re-measure **p50/p95/p99** on a cloned 66k+ track DB,
especially single-character / 1-2 Chinese character queries and searches
matching thousands of artists/albums. Do not claim perf until independently
measured.

All changes to this feature are confined to search/catalog/alias/UI code.
Do not modify DSP, VU meters, DLNA, or FLAC/ID3 metadata.
