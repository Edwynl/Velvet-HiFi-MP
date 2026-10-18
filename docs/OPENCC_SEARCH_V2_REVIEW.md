# OpenCC Search V2 — implementation and Claude review

Branch: `feat/opencc-search-v2` (based on `main`).
No GitHub Actions or CI workflows should be started; run verification locally.

## Scope

- Use the pinned OpenCC 1.4.2 wheel and its `t2s.json` phrase dictionary.
- Keep original artist/album/track display text and FLAC metadata unchanged.
- Canonical OpenCC T2S **and** literal NFKC/casefold keys protect both
  Chinese variants and direct substring matches affected by phrase context.
- Index search paths: global tracks, catalog tracks, artists, albums, aliases,
  composers, conductors, credited artists; include album genre on browse.
- SQLite migration 12 adds dirty-queued artist/album search indexes. No
  repeated transfer of a full catalog to clients; pagination stays in SQL.
- Existing track dirty queue preserves updates before index refresh.
- Index signature contains installed OpenCC package version, and its change
  triggers a full track/catalog reindex on the next refresh.
- Search endpoints retain old method signatures and existing API response
  envelopes for frontend compatibility.

## Local Windows test checklist

```powershell
python -m pip install -r requirements.txt
python -m pytest tests/test_library_discovery.py tests/test_library_paging.py tests/test_search_opencc_v2.py -q
python -m pytest tests -q
npm run test:frontend
python scripts/benchmark_search.py --source "velvet_data/library.db" --copy "$env:TEMP/velvet-search-benchmark.db" --verify
```

Run the benchmark on a backed-up scratch copy; never rebuild a production DB
without making a copy first. Benchmark script compares indexed and full-scan
results and reports search latency. This branch is **not** verified against a
real Windows database or on-device music library yet.

## Claude code review priorities

1. Verify `library_search_match.match_track_ids` never loses a direct
   substring match when OpenCC changes full-phrase segmentation.
2. Verify updated track/artist/album/alias dirty triggers and deletion cases.
3. Compare indexed vs unindexed results, including partial titles, mixed
   Chinese/English, literal % and _, and names with multiple credited artists.
4. Verify migrations on an existing schema-11 DB, reindex performance and
   fresh installs. Check low-power Windows PC memory/CPU.
5. Confirm Artist/Album `total` and pagination boundaries after normalization.
6. Check package wheel availability for deployed Python and Windows CPU.
7. Confirm no changes to stream/DSP, artwork resolution, file tags, or ID3 data.

Do not merge until local tests and review are complete.
