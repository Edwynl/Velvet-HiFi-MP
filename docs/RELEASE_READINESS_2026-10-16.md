# VELVET PR #1 — Final Merge-Gate Report

Repository `Edwynl/Velvet-HiFi-MP` · branch `fix/auth-boundaries-and-soxr-resampling` · Draft PR #1 · base `main` (not merged). PR #2 was not touched.

## Verdict

**GO for code, NO-GO for merge until the hardware acceptance below is done.**

**PR #1 is code-ready; pending hardware acceptance and owner approval.**

All automatable gates pass: the Python and frontend suites, Linux and Windows CI, Windows installer verification, real-FFmpeg output checks, a live HTTP smoke test, and a native-audio smoke test on this PC's PortAudio. No open P0/P1 remains. The PR stays in Draft.

## Findings

### Final review (this round)

| Sev | Finding | Status |
|-----|---------|--------|
| P1 | Round 2 awaited a per-track ticket fetch **before `audio.play()`**. That breaks the user-gesture chain `playTrack` deliberately preserves for mobile Safari, so the first tap-to-play could fail with auth on. | **Partly fixed** in `5a5585e` (prefetch); **completed** after the independent review below. The browser now holds one stream-only ticket, prefetched at startup, login and key change, and renewed in the background. The tap-to-play path has no await when a ticket is cached. A frontend test asserts that the only possible await before `play()` is the no-ticket fallback, and that test fails on the round-2 code. |
| P1 | Enforcing the transcode limit created a race. A track switch overlaps the closing and new FFmpeg streams (twice over with the VU meter, which mirrors the stream). The transient 503 is caught by the frontend's DSP error path, which **auto-disables DSP**. | **Fixed.** Slot acquisition waits up to 3 s for the previous decoder to be reaped before returning 503. Tested. |
| P1 | DLNA: `HEAD /api/stream/*` and `/api/covers/*` returned **405**. Renderers commonly probe with HEAD. This predates the PR. | **Fixed.** Both routes accept GET and HEAD. HEAD returns `Content-Length`/`Content-Range` and no body, and a transcoded HEAD never starts FFmpeg. Verified under real uvicorn. |
| P1 | Renderer URLs used a hard-coded port 8765 instead of `VELVET_PORT`, so casting broke on non-default ports. This was a round-2 regression. | **Fixed.** URLs now use the configured port. Tested. |
| P2 | At `latency="low"` (5 ms) on the Windows **MME** host API, PortAudio consumes far too slowly and playback crawls. This was measured on real hardware. The UI always requests `medium`, which plays correctly, and the 5 ms map entry predates this PR. | Accepted. Document it, or remove `low` in a follow-up. |
| P2 | The `underruns` counter counts PortAudio status (xrun) flags, not ring-buffer starvation. It is not exposed anywhere. | Accepted. |
| P2 | Native `set_volume` is a no-op and `get_position` always returns 0. Volume is applied at track start. Both predate this PR, and the UI does not call them, so no existing function regresses. | Accepted. |
| P2 | A very short native track can finish before `play_local` marks the state as playing, leaving it "playing". This is an edge case. | Accepted. |
| P2 | Responses lack DLNA `contentFeatures.dlna.org` / `transferMode.dlna.org` headers. Some strict renderers want them. | Follow-up if renderer testing shows a need. |

### Independent review of `b79e47d` (CHANGES REQUESTED) and resolution

| Sev | Review finding | Resolution |
|-----|----------------|------------|
| P1 | First tap right after login or cold start, before the background prefetch resolved, still awaited `/api/stream-ticket` before `audio.play()`. Single-flight renewal was not key-aware: an old-key response could be cached after rotation. | **Fixed.** Every credential response (`/api/security/login`, `generate-api-key`, `reset-api-key`) now carries `stream_ticket` and `stream_ticket_expires_at`. The startup session refresh is awaited before the first navigation renders anything tappable, and login and rotation adopt the ticket before re-rendering. The first authenticated tap therefore has a ticket without any network wait. Renewal is single-flight **per API key**: stale responses are discarded and a new key starts its own request. The only remaining await is for a ticket that expired while the page slept; there, a `NotAllowedError` shows "Tap play again". Regression tests use the real `index.html` in jsdom: a cold start with a hung ticket endpoint calls `play()` synchronously, and a gated fallback shows the retry toast. Additional vm tests cover login (ticket present at `render()`), in-flight key rotation, fetch failure and retry, and renewal. |
| P1 (found while adding the review's timeout test) | `iter_ffmpeg_stdout` wrapped `yield` in `asyncio.timeout`. When the cap expired while the consumer was paused, the cancellation hit an unrelated await in the consumer's task (Starlette's `send()`). `asyncio.timeout` also requires Python 3.11, while `install.ps1` accepts 3.10, so transcodes failed on 3.10. | **Fixed.** The deadline is enforced per read with `asyncio.wait_for`, so cancellation stays inside the generator. Verified on Python 3.10.6 and 3.12. A regression test (paused client outlives the cap) fails on the old code. |
| P2 | `sw.js` serves cached authenticated cover images before network authorization, and the cache is not purged on key or password rotation. | **Accepted / documented.** Offline cover caching is intentional for the PWA. Covers already viewed stay readable on that device after a credential change until the cache rotates (400 thumbnails / 80 covers). Purging on credential change is tracked in the P2 follow-up. |
| P2 | The FFmpeg total cap is wall-clock, so pauses count. The docstring implied otherwise. | **Documented and tested.** Docstrings now state the cap includes pauses (duration + 30 min is a margin, not a pause-independent guarantee). A new test pins this behavior. |
| Scope | `local_playback.py` never requests WASAPI exclusive mode. | **Corrected wording.** Exclusive output is *not implemented*; native playback uses each device's shared/default stream. Nothing in this report claims exclusive output is validated. |

Sample-rate reporting was checked and is correct. The UI badges show source metadata. The native API returns the negotiated output rate (verified on hardware: 44.1/48/96 kHz reported as played). Upsampled responses carry `X-Upsampled-Rate`.

### Second review round (`37decb5`, all resolved)

| Sev | Finding | Resolution |
|-----|---------|------------|
| P0 | Long-lived API key embedded in stream URLs; credentials accepted in query strings | HMAC media tickets; credentials accepted only in headers. **Browser:** a stream-only ticket (`stream-any`: GET/HEAD `/api/stream/*` only, 4 h). **DLNA:** per-track/per-cover tickets valid for track length + 30 min (12 h cap). Any password change, key rotation or disable revokes all tickets. |
| P1 | DLNA browse and cast disabled under auth | Cast sends ticketed URLs. Browsing requires a time-limited window (≤ 8 h) opened by an authenticated user, and only private or link-local clients may browse. |
| P1 | `max_concurrent_streams` not enforced; hard 60-minute transcode cap | The limit is enforced (now with the wait described above). The time limit is the larger of the configured value and duration + 30 min, capped at 12 h. A 120 s idle timeout ends stalled decoders. |
| P1 | Pipe reads, process kills and logging inside the PortAudio callback | A decoder thread fills a bounded 2 s PCM ring buffer; the callback only copies memory. |
| P1 | `sounddevice` never installed; installer ignored pip failures; PortAudio `OSError` crashed the import | Fixed, plus a Windows installer CI job. |

## Tests actually run

| Gate | Where | Result |
|------|-------|--------|
| Python suite | Local Windows 11 / Python 3.12 | **140 passed**, 12 failed. The 12 are the expected 503s because the local FFmpeg lacks libsoxr; they pass on CI. |
| Frontend suite | Local | **8/8** scripts OK (adds the jsdom cold-start test) |
| CI · Python Linux / Windows | GitHub Actions [run 38863168866](https://github.com/Edwynl/Velvet-HiFi-MP/actions/runs/38863168866) on head `5a5585e` | **pass**: Linux 137 passed, 1 skipped (Windows-only test), 50 subtests; Windows **138 passed**, 50 subtests |
| CI · Frontend Linux / Windows | 〃 | **pass** / **pass** (7/7 scripts each) |
| CI · Windows installer + native audio detection | 〃 | **pass**: `install.ps1` → `[OK] Native audio output is available`, sounddevice 0.5.6 / PortAudio V19.7.0 |
| **FFmpeg output** | CI (real FFmpeg with libsoxr) | SoXR→WAV `pcm_s32le` at 2× the source rate (44.1k/48k families) for 16/24-bit FLAC and MP3, plus DSP→FLAC, all checked with ffprobe |
| **FFmpeg output** | Local (real FFmpeg) | 24-bit/96 kHz FLAC with DSP → **FLAC, 96 kHz, 24-bit (`s32`)**, checked with ffprobe |
| **Live HTTP smoke** (real uvicorn) | Local | **16/16**: loopback bootstrap; 401 without credentials and for `?api_key=`; ticket issuance with `no-store`; ticketed GET and Range; HEAD full/range/DSP with empty body; browser ticket rejected for covers and API; honest 503 without libsoxr; DLNA window; key rotation revokes the ticket; no leftover `ffmpeg.exe` |
| **Native audio smoke** (real PortAudio) | Local PC, Realtek onboard via **MME**, **silent** audio | **11/11** at `medium` latency: 44.1/48/96 kHz playback reports the actual rate; 10 rapid switches; one decoder at a time; pause/resume; natural end clears state; **0 xruns**; no `ffmpeg.exe` or decoder threads left |
| Auth and stream-ticket regression tests | Local + CI | Included above (`test_media_tickets.py`, `test_server_api.py`, `test_security_boundaries.py`, `test_upnp_server.py`, `stream-ticket.test.mjs`, `pwa-auth.test.mjs`) |

**Not hardware validation:** the mocks, CI runners and the onboard-MME smoke test above do not count as DAC, DLNA or mobile acceptance.

## Remaining hardware acceptance (owner)

| Area | Required checks |
|------|-----------------|
| **Hi-Fi DAC (Windows)** | USB DAC through WASAPI **shared** mode and ASIO host APIs at 44.1–192 kHz (exclusive mode is not implemented); 16/24-bit FLAC and MP3; DSP preamp/EQ; rapid switching; USB disconnect/reconnect mid-track; end-of-track advance; audible glitches at `medium` |
| **DLNA** | HEOS (Marantz M1), StreamMagic (EXN100), BubbleUPnP: browsing refused outside the window and allowed inside it; HEAD/GET probes; cover art; tracks longer than 1 h play to the end; pause over 30 min then resume or seek; cast from the UI with auth on |
| **iOS Safari + PWA** | First tap-to-play after login and after a cold start; lock-screen auto-advance; resume after a pause longer than the remaining ticket lifetime; DSP stream with VU meter while switching |
| **Android Chrome + PWA** | Same as iOS, plus switching between Wi-Fi and mobile data mid-track |
| **Clean Windows machine** | `install.bat` → Zones lists native devices |

## Known limitations and accepted risks

- A leaked browser stream URL lets anyone stream any track for up to 4 h. It grants no covers, API or mutation access, and it is revoked by key rotation. A leaked DLNA URL exposes only one track or cover. Tickets are not bound to a client IP, because mobile clients and renderers change addresses.
- While the DLNA window is open, any device on the private subnet can browse. Closing the window stops browsing, but URLs already issued stay valid until they expire.
- If a ticket expires mid-track, the next Range request returns 401. The error-recovery or resume path restarts playback at the current position with the current ticket, which can cause a brief gap.
- The concurrency limit is per process. Direct (untranscoded) streams are not limited.
- Not implemented: true DoP/DSD, bit-perfect ASIO/exclusive output, multi-room sync.

## Migration notes

- Scripts must send `X-API-Key` / `X-Password` as headers; `?api_key=` is rejected.
- `VELVET_MAX_CONCURRENT_STREAMS` (default 3) is enforced for DSP/upsampled streams. `VELVET_STREAM_TIMEOUT_MINUTES` accepts 5–720 and acts as a floor.
- To browse from HEOS or StreamMagic, open Remote → Security Protection → *DLNA Library Browsing*. Casting from the UI needs no window.
- On Windows, re-run `install.bat` to install `sounddevice`. `security.json` gains a `media_secret` field. There are no database or library changes, and the UI styling is unchanged.

## Commits on this branch since the first review

- `37decb5` second-round fixes
- `0ef3aed` report
- `5a5585e` final-review P1 fixes
- `b79e47d` report
- `ad98298` independent-review fixes (ticket in credential responses, key-aware renewal, per-read FFmpeg deadline)
