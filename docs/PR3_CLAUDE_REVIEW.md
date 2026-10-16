# PR #3 — P2 architecture and library performance: Claude handoff

**Repository:** Edwynl/Velvet-HiFi-MP
**Branch:** `perf/frontend-pwa-library-pr3`
**Base:** `fix/auth-boundaries-and-soxr-resampling` (PR #1)
**PR #2:** separate and intentionally untouched
**Status:** Draft / never merge automatically

## Delivered (this PR only)

### 1. Browser architecture: first controlled extraction

- Extracted roughly 4,000 lines from the old ~13,100-line `static/index.html`, into six classic-script files:
  - `static/js/library-views.js`: artist, album, genre, playlist, home/discover/search views
  - `static/js/library-cards.js`: card/track-row factories
  - `static/js/player-ui.js`: player visual controls and popovers (not the transport engine)
  - `static/js/dsp-controls.js`: graphic EQ and preset controls
  - `static/js/settings-view.js`: **complete** Settings view, including nested DSP preset callbacks
  - `static/js/remote-settings.js`: Remote/Tailscale settings and credential/DLNA controls
- The original HTML still owns application state/bootstrap, transport engine, a significant share of the UI, and inline CSS. This is a *first stage*, not a complete migration to ESM/React.
- The server serves **only** an allowlist of those six JS assets via `/static/js/{module_name}`. They load before login (the application shell must be accessible before authentication), without exposing arbitrary server files.
- Existing JSDOM tests inject each classic-script file using `tests/frontend/inline-modules.mjs`. Browser code remains identical where feasible.

### 2. PWA storage budget

- Service worker caches six new frontend scripts for offline shell.
- Thumbnail cache: **400 entries AND 24 MiB** maximum.
- Full cover cache: **80 entries AND 40 MiB** maximum.
- Eviction checks bytes as well as count; response sizes are memoized to avoid O(n²) body re-reading.
- Responses with `Cache-Control: no-store`, byte ranges, or `ticket`/`api_key`/`password` query parameters are not cached.
- Regression tests: `tests/frontend/pwa-auth.test.mjs` and `tests/frontend/pwa-cache-budget.test.mjs`.
- This is the *cover-art cache* limit, not a guarantee about browser total storage usage.

### 3. SQLite and music scanning

- Migration **9** adds `track_file_state` (file size, mtime, optional content hash) plus targeted indexes:
  - `idx_tracks_duplicate_size` for candidate duplicate grouping
  - `idx_history_recent_track` for recent playback joins
  - `idx_tracks_album_quality` for album-quality metadata access
- Normal music scanning stores a baseline file-stat snapshot. Subsequent scans only extract metadata for new/changed size-or-mtime files.
- Changed files update their existing `tracks.id`, preserving linked playlists and `play_history`.
- Manual folder refresh is now a **forced, non-destructive** re-read: never delete/re-insert old tracks or silently prune files from temporarily offline drives.
- Each file's scan changes are wrapped in a SQLite SAVEPOINT to avoid partial metadata / artist relationships on parse errors.
- Existing pre-migration track records receive a conservative baseline on their first ordinary scan, then modifications are tracked on following scans. A manual folder refresh can force immediate metadata reload.
- A background exact-duplicate scanner uses size grouping followed by full SHA-256; never auto-deletes tracks.
  - `POST /api/library/duplicates/scan` → 202, starts job (409 if already scanning)
  - `GET /api/library/duplicates` → progress, result groups, known limitations
  - Max 5,000 candidate files and 100 result groups per scan by default. Results explicitly report truncation.
  - Only byte-identical files are duplicates. Different FLAC/MP3 encodings or different tags are NOT considered duplicates even if they contain the same music.

## Verification

Run:
```bash
python -m pytest tests -q
npm ci && npm run test:frontend
```
Independent GitHub workflow: `.github/workflows/pr3-ci.yml`, Linux and Windows, Python and frontend matrix.

Check `tests/test_library_incremental.py`, `tests/test_scanner_service.py` (non-destructive refresh), `tests/test_server_api.py` (static module exposure and scan API), and `tests/frontend/pwa-cache-budget.test.mjs`.

**Do not treat green CI as proof of Android/iOS audio, actual DAC output, library-wide performance on terabyte-scale drives, or real user authentication across proxy configurations.** CI uses controlled SQLite fixtures and JSDOM.

## Claude integration-review checklist

1. Run entire PR #3 diff against *PR #1's latest base*, not `main`. PR #1 may have advanced since this branch was created. Check the merge base and rebase/update PR #3 **only after** PR #1 is finalized, with conflict resolution.
2. Verify all 6 scripts are served with correct JS MIME and accessible during first-time login, while arbitrary server files remain inaccessible. Verify no duplicated or broken global lexical state/event handlers.
3. Test first-load UI across Home, albums, artists, search, Settings, DSP, Remote, playlist and player UI, including old cached Service Workers and a fresh PWA install.
4. Verify no duplicate scan overwrites a more recent metadata snapshot and no hash is returned for a changed file. Confirm no files can be hashed outside configured library roots and no deletion occurs.
5. Verify normal scanning preserves tracks/playlists/play history when embedded tags or filesystem timestamps change; force refresh preserves identity and does not remove temporarily absent files.
6. Validate schema migration 8→9 on a COPY of a real library DB, with a backup first. Measure scan and index performance on 10k+ track libraries. The incremental cache is per tracked path and does not automatically reconcile moved files.
7. Verify 24 MiB / 40 MiB caps after many simultaneous image requests on Chrome/Android; note that per-entry Cache API operations are not fully serialized.
8. Check that duplicate job is properly protected by auth middleware, does not block playback under heavy CPU/disk hashing, and presents actionable output.
9. Identify P0/P1 regressions, add focused tests and fix only those. Keep P2 follow-up ideas in recommendations, not spontaneous refactoring.

## Deliberate follow-ups, not silently claimed complete

- Full frontend ESM or React/Vite migration and extraction of inline CSS/core playback state
- Perceptual/audio-fingerprint duplicate grouping across different encodings (not byte-identical)
- Automatic moved-file reconciliation and safe user-confirmed deduplication UI
- Cross-account cover-cache clearing and finer worker storage concurrency control
- Physical DAC/iOS/Android/UPnP validation

**Review result expected:** changed files, exact test results on latest commit, remaining defects P0/P1/P2, migration/data-integrity risks, GO/NO-GO recommendation. Do not merge PR #1, PR #2, or PR #3 without owner approval.
