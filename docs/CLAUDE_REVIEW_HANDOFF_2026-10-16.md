# VELVET Consolidated Claude Review — 2026-10-16

Repository: `Edwynl/Velvet-HiFi-MP`
Review target: **Draft PR #1**, branch `fix/auth-boundaries-and-soxr-resampling`
Base: `main`. **No change has been merged.**

## Review once, after this development batch

Review the ENTIRE PR diff rather than individual commits. Look for feature integration conflicts, playback regressions, authentication flaws and OS-specific incompatibilities. Where necessary, make focused fixes on the same branch, extend tests, and report results. Do **not** merge without the owner's approval.

### Completed code areas

| Area | Implemented |
|---|---|
| Network bootstrap | Remote clients cannot access unconfigured server APIs; first password setup requires loopback |
| Authentication | Credential mutation endpoints always require authentication; don't trust raw X-Forwarded-For as client identity |
| Key lifecycle | Frontend login obtains a key via authenticated request; API key rotations update local client state |
| Password hashing | PBKDF2 with per-password salt when bcrypt is absent; legacy unsalted SHA-256 verification retained only for existing hashes |
| HTTP direct audio | Single byte ranges including suffix, 206, invalid/out-of-bounds 416, proper Content-Range |
| FFmpeg streams | Reuse async decoder streaming cleanup; canceled requests and timeouts terminate and reap child processes |
| Resampling | Correct libsoxr parameters; WAV response matches output bytes; preserve 44.1k vs 48k source families and reject extreme target rates |
| DSP-only | FLAC response with cleanup |
| DoP honesty | Requests for unimplemented DoP return 501 rather than ordinary PCM mislabeled as DSD |
| Native audio | Place `-af` after FFmpeg input; use float32 PCM to avoid 16-bit decoder truncation; regression mock test |
| Configuration | Honor `VELVET_CORS_ORIGINS` through settings and `VELVET_HOST` in Uvicorn |
| PWA | Bound thumbnail/cover caches, avoid caching `no-store` responses, and skip all non-cover API/audio |
| Testing and CI | Python + frontend GitHub Actions workflow, regression tests, corrected stale DSP deletion test, removed invalid `patch.py` |

### Required validation

1. Run `python -m pytest tests -q` and `npm run test:frontend`. Confirm CI for the **latest** head SHA passes, rather than relying on older green checks.
2. On Windows test: source FLAC/MP3 16/24 bit, 44.1k/48k, WASAPI shared and exclusive-capable DAC where available, DSP preamp/EQ, stop/pause/resume, repeated rapid switching, hardware disconnect/reconnect, playback end and seek.
3. On actual iOS/Android browser or PWA: login/renew API key, play/pause, audio in background and lock screen, same-origin cover caching, low connectivity, long playlists.
4. Check that first-time setup works locally on `http://localhost:8765`, that unauthenticated LAN access is denied, and that configured remote clients can play normally. Verify Tailscale/reverse-proxy client IP handling.
5. Confirm FFmpeg output headers correspond to the actual WAV/FLAC bytes and that compressed/direct original streams preserve Range requests.
6. Test an audio client disconnecting while FFmpeg has a long-lived process: there must be no leaked subprocess.
7. Verify cover cache does not grow without bound after scrolling a large library.
8. Check dependency audits: an earlier CI npm install warned of one high-severity vulnerability; identify the dependency and fix only with appropriate lockfile tests.

### Remaining limits / honest caveats

- True DoP is **not implemented** and needs suitable hardware, marker packaging and DAC acceptance tests. Do not claim DSD output works.
- Browser playback is not automatically bit-perfect; device, DSP, volume and resampling behavior matter. Native float32 audio is likewise not guaranteed bit-perfect.
- ~~`LocalPlayer` reads FFmpeg stdout inside the PortAudio callback~~ — fixed in the second review round: a decoder thread fills a bounded PCM ring buffer and the callback only copies memory. Still needs Windows hardware validation (see release-readiness report).
- ~~URL query-string API key for HTMLMediaElement streaming~~ — replaced in the second review round by HMAC tickets scoped to one track/cover; query-string credentials are no longer accepted.
- Full frontend migration away from a ~450KB single HTML file, synchronized multi-room playback, device-capability negotiation and real ASIO exclusive output are **not included** in this patch. These are separate architecture/hardware projects, not fixes that can safely be claimed complete in a general regression PR.
- GitHub CI is not equivalent to validating Windows audio devices, Tailscale, iOS or Hi-Fi DACs.

### Claude output requested

Provide one final review report with: `P0/P1/P2` findings, direct code fixes, tests run and pass/fail counts, performance and compatibility risks, any migration notes, exact remaining items, and a clear **merge / do-not-merge** recommendation. Keep PR as draft until approved.
