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

### Deferred: automatic MusicBrainz aliases

Automatic alias import from MusicBrainz (Sync Names, bulk enrichment, release
corroboration, identity review, alias provenance audit) was removed from this
PR to ship search first. The full implementation, including schema
migrations 14–16, is preserved on branch `feat/musicbrainz-aliases`.
When it returns it needs new migration numbers above the ones merged by then.

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
