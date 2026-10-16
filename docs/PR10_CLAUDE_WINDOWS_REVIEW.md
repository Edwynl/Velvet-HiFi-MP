# VELVET PR #10 — One-Shot Claude Review & Windows Local Validation

**Branch:** `feat/library-mobile-windows-hardening` (based on `main` at `8274dbf`)
**Pull request:** https://github.com/Edwynl/Velvet-HiFi-MP/pull/10
**Status:** Draft. Do not merge until owner explicitly approves.
**CI policy:** GitHub Actions minutes exhausted. ALL final development commits have `[skip ci]`. DO NOT launch/rerun GitHub Actions, modify GitHub CI into a permanent disabled state, or describe CI as green. Owner will run Windows tests locally.
**Out of scope:** PR #2 Android Signal Path, PR #6/7 security branches, EXN100/UPnP cover image resolution and encoding, original FLAC precision and streaming DSP.

## Implemented changes

### A. Safe library operations / data integrity

- `library_safety.py`: album deletion with `delete_files=true` moves audio into hidden files on their original volumes via `os.replace`, accompanied by a durable per-operation manifest in `velvet_data/album-trash`. It does **not** permanently unlink FLAC.
- Aborts before changing the DB if any source file is missing or outside a configured library root; attempts to restore staged files after file or DB failures.
- `GET /api/library/trash` and `POST /api/library/trash/{token}/restore` list and restore archives. After restoring, a normal library scan rebuilds deleted track rows; the old track ID is **not** automatically recreated.
- Deletion and restore now reserve the shared library-maintenance flag. Normal scan/refresh and SHA-256 duplicate jobs are refused while an album is being moved, and deletion is refused during their execution.
- `db_pool.py`: on API `.close()`, roll back any uncommitted transaction. Recycling marks owner-thread connections stale instead of attempting illegal cross-thread `sqlite3.Connection.close()`. Owner thread replaces stale connection upon next `get_db()`.
- Tests: `tests/test_library_safety.py`, `tests/test_db_pool_lifecycle.py`, plus added API tests in `tests/test_server_api.py`.
- **Critical design acceptance:** database transactions and Windows filesystem renames cannot be one true atomic transaction. A sudden power loss can leave a `pending` archive manifest; provide manual recovery and backup before accepting this feature. Do not claim full crash atomicity. Never purge archives automatically.

### B. Library search and bounded pagination

- `library_paging.py` and `server.py`: `GET /api/albums/page`, `GET /api/artists/page`; SQL `LIMIT 60 OFFSET` with total count, deterministic sort, filters and server-side search. Existing `/api/albums` and `/api/artists` array endpoints remain unchanged for compatibility.
- `static/js/library-views.js`: albums and artists request 60 records per page rather than 15,000 albums or 5,000 artists on first load. Search sends the actual query string; it no longer calls `/api/search?q=` with an empty query. Stale network responses are discarded and failures retain the previous page.
- `library_discovery.py`: Chinese simplified/traditional fallback scans track candidates in bounded 512-row SQL batches until the requested results are found or the track table ends. This fixes the former three-pages-only blind spot; the fallback is still O(N) at worst and warrants real-library profiling.
- `static/js/library-cards.js`: lazy DOM attachment in 24-card batches via `IntersectionObserver`; max 60 server-paged albums/artists. This is **progressive rendering**, not a full recycling virtual scroller.
- Tests: `tests/test_library_paging.py`, `tests/test_library_discovery.py`, `tests/frontend/mobile-catalog-paging.test.mjs` and existing navigation-back tests.
- The API response includes only page metadata and titles; album covers continue to use the existing URLs and original full-size path.

### C. Mobile / Tailscale / PWA

- `static/index.html`: the Settings and Remote scripts are downloaded only on visiting those views. The shared app state, player logic, DSP engine and auth bootstrap stay eager.
- `server.py`: compresses **only** public HTML and allowlisted JS modules with Gzip, caches compressed blobs per revision, and responds with `Vary: Accept-Encoding`. **Never** touches FLAC/WAV/DSD or cover bytes.
- `sw.js`: serializes cache.put + byte-budget cleanup per artwork cache; thumbnail responses no longer wait for eviction before reaching the UI; 24 MiB thumbnail / 40 MiB full-cover budgets remain unchanged. Existing image quality/resolution is untouched.
- `remote_access.py` + `static/js/remote-settings.js`: show an HTTPS link only after detecting a **running Tailscale Serve** proxy to port 8765. Otherwise guide the user to opt in via `tailscale serve --bg 8765`. NEVER enable public Funnel or alter VPN configuration automatically.
- Important: `http://100.x.x.x:8765` is not a browser secure context despite VPN transport encryption. Android/iOS Service Workers typically require HTTPS for remote IP origins. Real PWA verification requires the user's Tailnet domain and devices.
- `tests/frontend/inline-modules.mjs` intentionally inlines production-lazy modules in JSDOM to retain existing tests; browser network lazy behavior needs separate manual confirmation.
- Tests: existing `tests/frontend/pwa-auth.test.mjs`, `pwa-cache-budget.test.mjs` and `tests/test_remote_access_https.py`, plus Gzip API tests.

### D. Windows audio and long paths

- `scanner_service.py`: use Windows extended-length filesystem paths for recursive traversal, but preserve original plain track paths in SQLite so the music library does not appear re-imported.
- `library_duplicates.py`: read long file paths using the filesystem adapter while reporting plain file names.
- `local_playback.py`: actual PCM-frame position reporting, plus optional digital gain (0–1). **Unity gain 1.0** does not attenuate PCM samples; any other value applies DSP-style digital attenuation and must NOT be marketed as bit-perfect or physical DAC amplifier volume.
- `POST /api/playback/volume` is an optional native digital gain control.
- Nothing in this PR verifies WASAPI exclusive, ASIO output, a real DAC or EXN100.
- Tests: `tests/test_native_volume_position.py`, existing `tests/test_native_audio.py`.

### E. CI and local test runner

- `.github/workflows/ci.yml`: Windows-first workflow (Linux no longer a required matrix).
- Removed redundant `.github/workflows/pr3-ci.yml`.
- **Do not run GitHub Actions now.** All commits after the user's request contain `[skip ci]`; the GitHub workflow is not permanently disabled.
- `scripts/windows_pr10_local_test.ps1`: full or `-Quick` smoke suite, PowerShell, local Python 3.12 and Node 22.
- `scripts/benchmark_mobile_library.py`: read-only SQL pagination benchmark against a **copy** of a large music database; reports medians, p95 and raw JSON payload sizes (not network latency).

## Local Windows acceptance steps

1. Stop VELVET before backing up SQLite and its WAL/SHM state. Save a verified backup of `velvet_data/library.db` **and the actual FLAC folders**. Run `PRAGMA integrity_check` on the backup. Prefer testing destructive operations only on a **disposable copy** of FLAC and the database.
2. Check out this branch on a disposable local worktree. Do not overwrite uncommitted work in any other branch.
3. In PowerShell:

   ```powershell
   .\scripts\windows_pr10_local_test.ps1
   # Or a faster initial check:
   .\scripts\windows_pr10_local_test.ps1 -Quick
   ```

   Requires Python 3.12, Node.js/npm, FFmpeg on PATH. It runs Python tests with temp SQLite fixtures and JSDOM tests; no GitHub Actions.
4. Benchmark a **copy** of the actual large DB:

   ```powershell
   .\.venv\Scripts\python.exe scripts\benchmark_mobile_library.py --db "C:\temp\velvet-library-copy.db"
   ```

5. Chrome/Android/iOS over Tailnet HTTPS: check first-load transferred KB, number of HTTP requests, page load time, Albums/Artists page 2, fast search changes, back-button/navigation, focus filters, Favorites, PWA refresh and offline cached covers. Check first-load script requests: Settings/Remote should not be requested until opened.
6. Windows 260+ path import and duplicate hash; files should stay under the original DB path, no re-import.
7. Archive / restore one disposable FLAC album. Induce a file lock and DB exception; originals must remain recoverable. Confirm that archives are hidden from the scanner and recovery instructions are clear.
8. Native volume and frame progress: test with Windows playback where practical. Digital gain !=1 is NOT bit-perfect. Real WASAPI shared/exclusive/USB-hotplug acceptance remains deferred until real DAC available.

## Review requirements for Claude

- Review the WHOLE PR diff against *current* `main`, not PR #2/#6/#7. Check whether `main` advanced and warn about conflicts; do not overwrite newer security/audio fixes.
- Prioritize P0/P1 findings, security leaks, loss of FLAC, rollback consistency, password/auth, mobile Safari runtime, JavaScript lazy module globals, SQL paging correctness, same-size/time content hashing, and long paths.
- Verify schema stays at 9 and no new unintended migrations or music-file overwrites occur.
- Run regression tests only in a local Windows shell. Since owner explicitly disabled GitHub Actions for quota reasons, **never queue, rerun or enable GitHub Actions** without a new owner request.
- Give a single comprehensive audit with test evidence and a clear GO/NO-GO. Keep PR Draft and never merge automatically.

## Known limitations (do not conceal)

- Local tests and real-device tests have NOT been run by the author in this session because the owner will run them on Windows. GitHub Actions are deliberately skipped.
- A crash can leave a pending archive; manifest-assisted recovery is required; DB records are not automatically reconstructed on restore.
- Simplified/traditional fallback may scan the full library at worst.
- Album/artist pagination is offset-based rather than seek/keyset; page membership may change if library is edited during navigation.
- The 60-item cap and progressive-card DOM attachment reduce client pressure but do not guarantee specific FPS, battery use, or Tailscale network throughput.
- HTTPS detection is read-only/opt-in, and requires real Tailscale Serve configuration. Android/iOS service-worker and lock-screen behavior remain unverified.
- Bit-perfect and physical DAC audio paths are intentionally unmodified; digital volume != unity is not bit-perfect.

**Delivery goal:** Working Windows server + lighter phone/tablet browsing and reliable library safety, with final local verification by owner, then one Claude audit.
