# Velvet unified search + artist aliases (PR #17)

## User-visible behavior

The same topbar input now searches the **entire library** from Home,
Discover, Albums, Artists, Tracks, Composers, Works, Credits, or Genres.

Global search results are grouped and displayed in this exact order:

1. **Artists** — tap an artist card to open the artist page.
2. **Albums** — tap an album cover to open the album.
3. **Tracks** — tap a row to start playback with the existing queue behavior.

The search API returns a backward-compatible `tracks` list and additional
`artists`, `albums`, `total_artists`, and `total_albums` keys. All
three collections are searched by the server, paged/capped, and returned
without downloading an entire catalog to phones/tablets. Input is capped at
128 characters. Empty search does not query the library.

Specialized classical-library screens retain a **separate local Filter** field
for Composers, Works, Credits, and Genres. It is intentionally a literal
filter of displayed cards; full OpenCC conversion is done by the global search.

## Alias identity (why LaLa Hsu differs from 徐佳瑩)

- OpenCC normalizes `徐佳瑩` and `徐佳莹` but cannot infer stage names.
- Aliases are persisted in existing `artist_aliases` linked to the artist ID.
  Its triggers dirty track, artist and album index rows immediately.
- Artist detail now offers **Sync Names** (opt-in MusicBrainz) and **+ Alias**.
- The global search empty state also offers **Find artist by another name**.
  It searches MusicBrainz once **only after a click**, checks that the remote
  name/alias maps unambiguously to an artist already in the local library,
  imports the verified alias, and repeats the offline search. A new alias
  that cannot be verified never merges/creates artist records.
- Sync resolves a stable MusicBrainz ID by stored MBID or **verified exact
  canonical artist name**, then looks up `inc=aliases`. It also imports a
  verified MusicBrainz sort-name and its given/family reordered form, e.g.
  official `Hsu, LaLa` yields `LaLa Hsu` for the local record 徐佳瑩.
- If MusicBrainz returns ambiguous/unverifiable identities, it writes nothing.
  If an alias is already another local artist's *primary* name, it skips that
  alias rather than silently merging two distinct records.
- Internet is accessed **only when Sync Names is clicked**. Every subsequent
  search is fully local/offline; a missing album cover or artist image does not
  affect matching.
- Explicit + Alias is available for stage names absent from MusicBrainz. It is
  not a hardcoded Chinese conversion dictionary.

## Review / local Windows checks

Run locally; do **not** trigger GitHub Actions. Keep PR #17 Draft until Claude
completes review.

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_unified_search.py tests/test_search_opencc_v2.py tests/test_server_api.py -q
.\venv\Scripts\python.exe -m pytest tests -q
npm run test:frontend
```

Acceptance cases:

- `徐佳莹` finds stored `徐佳瑩` in Artists, associated Albums, and Tracks.
- After **Sync Names** on verified artist 徐佳瑩, `LaLa Hsu` and `lala hsu`
  produce the same three categories. Verify on a copy of the real library.
- When `Lala Hsu` has no initial local result, the optional one-click
  MusicBrainz resolution links it to existing 徐佳瑩 and reruns local search.
- After explicit **+ Alias**, results are immediately correct before reindex.
- `爱你` ranks `愛你` ahead of `愛`; literal `a_b` and `100%` remain
  literal in prefix ranking.
- Dirty album genres, aliases, and metadata edits are correct before rebuild.
- Typing in **Albums** or **Artists** topbar takes the user to the same
  global result sections, not an album-only/artist-only filter.
- Artist and album cards retain navigation; track rows retain direct playback.
- iOS/Safari PWA typing (including Chinese IME) does not show stale results.
- Pagination and cover-art quality remain unchanged.

Benchmark: the new three-category search adds two catalog queries to the
old track-only route. Re-measure **p50/p95/p99** on a cloned 66k+ track DB,
especially single-character / 1-2 Chinese character queries and searches
matching thousands of artists/albums. Do not claim perf until independently
measured.

All changes to this feature are confined to search/catalog/alias/UI code.
Do not modify DSP, VU meters, DLNA, or FLAC/ID3 metadata.
