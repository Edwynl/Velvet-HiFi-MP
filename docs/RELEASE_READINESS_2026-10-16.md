# VELVET PR #1 — Release-Readiness Report (second review round)

Repository `Edwynl/Velvet-HiFi-MP` · branch `fix/auth-boundaries-and-soxr-resampling` · Draft PR #1 · base `main` (not merged)

## Recommendation

**Do not merge yet.** The code findings from the second independent review are fixed and covered by regression tests, and CI passes on Linux and Windows (see below). Before merging, the owner should run the hardware/device checks in the last section. They cover real-time audio, DLNA interoperability and mobile background behavior, which CI cannot reproduce. Keep the PR in draft until those pass.

## Findings fixed in this round

| # | Sev | Finding | Fix | Regression tests |
|---|-----|---------|-----|------------------|
| 1 | P0 | Long-lived API key embedded in `/api/stream/...?api_key=` URLs (leaks via logs, history, renderer metadata); query-string credentials accepted on every route | HMAC-SHA256 media tickets scoped to **one track** (`stream`) or **one album cover** (`cover`), `expires = now + duration + 30 min` (cap 12 h). Signing secret is rotated on password change, API-key rotation/reset and disable, which revokes every outstanding ticket. Credentials are now accepted **only in headers**. Browser requests a ticket per track via authenticated `POST /api/stream-ticket`. | `test_media_tickets.py::MediaTicketTests` (scope, tamper, expiry, revocation, TTL), `test_server_api.py::test_stream_ticket_requires_authentication_and_is_track_scoped`, `test_auth_middleware_ignores_credentials_in_query_string`, `tests/frontend/stream-ticket.test.mjs` |
| 2 | P1 | UPnP/DLNA browsing and casting broken under authentication (cast returned 501; control points cannot send headers; unconfigured server blocked LAN renderers) | **Cast:** the server hands the renderer ticketed stream and cover URLs; SOAP calls run in a worker thread instead of blocking the event loop. **Browse:** an authenticated user opens a time-limited window (`POST /api/dlna/browse-access`, default 60 min, max 8 h, in-memory, treated as a sensitive route even with LAN-auth opt-out). During the window only **private/link-local** clients may browse; CGNAT/Tailscale (100.64/10), public and mapped-public IPv6 addresses are refused. Listed tracks carry per-track tickets valid until the window closes plus the track length. The UI control is in Remote → Security Protection → *DLNA Library Browsing*, built with the existing classes. | `test_media_tickets.py::DlnaBrowseAuthorizationTests`, `test_server_api.py::test_protected_cast_hands_renderer_track_scoped_ticket_urls`, `test_ticket_from_unconfigured_host_lets_lan_renderer_play_only_that_track`, `test_cover_ticket_is_scoped_to_cover_reads`, `test_dlna_browse_window_requires_credentials_and_is_bounded`, `test_upnp_server.py` |
| 3 | P1 | `max_concurrent_streams` setting was never enforced; the transcode limit was a hard 60 minutes (query `le=60`) that ignored `VELVET_STREAM_TIMEOUT_MINUTES` and cut long-form music | `StreamSlots` limits concurrent FFmpeg transcodes (DSP/upsampling) to `VELVET_MAX_CONCURRENT_STREAMS` and returns `503` + `Retry-After: 5` when the limit is reached. A lease is released when the decoder is reaped, or by GC if the body never started. Direct file streams are not limited because a browser preload/seek would otherwise trip it. The time limit now uses `max(configured or requested minutes, duration + 30 min)`, capped at 12 h, and the setting range is raised to 5–720. A separate 120 s **idle** timeout ends stalled decoders without counting time a paused client spends not reading. | `test_server_api.py::test_transcoded_streams_are_limited_to_configured_concurrency`, `test_transcode_time_limit_respects_configuration_and_long_tracks`, `test_media_tickets.py::StreamSlotTests`, `TranscodeLifecycleLimitTests` |
| 4 | P1 | Windows native audio read the FFmpeg pipe, terminated processes, logged and took state locks **inside the PortAudio callback** | A dedicated `velvet-ffmpeg-decoder` thread fills a bounded `PcmRingBuffer` (2 s). The callback only copies memory, applies gain and zero-fills on underrun (counted, not logged). End of track, process reaping and state updates run on the decoder thread and are ignored if a newer track or an explicit stop owns the state. `stop()` closes the ring, terminates FFmpeg from the control thread and joins the worker. There is a 250 ms prebuffer before the stream starts. | `test_native_audio.py::PcmRingBufferTests`, `DecoderWorkerTests` (pipe reads only on the decoder thread; natural end handled off the audio thread; prompt stop with a blocked producer; no stale-state clobbering) plus the existing native tests |
| 5 | P1 | `sounddevice` was missing from `requirements.txt`; `install.ps1` reported success even when pip failed; a missing PortAudio library (`OSError`) crashed the import | `sounddevice>=0.4.6,<1.0.0; sys_platform == "win32" or "darwin"`. `install.ps1` checks `$LASTEXITCODE`, verifies `local_playback.SOUNDDEVICE_AVAILABLE` in the venv and force-reinstalls once if needed. `OSError` is caught and its reason is reported in `/api/zones` → `native.error`. CI adds a **Windows installer job** that runs `install.ps1` and asserts native audio imports. | `test_native_audio.py::NativeAudioInstallTests`, CI job `Windows installer and native audio detection` |

The UI styling and the music library schema/data are unchanged. The only UI addition is the DLNA browsing row, built from existing `settings-*`/`btn` classes. There are no database migrations.

## Tests run

| Suite | Environment | Result |
|-------|-------------|--------|
| `python -m pytest tests -q` | Local Windows 11, Python 3.12, FFmpeg **without libsoxr** | 135 passed, 12 failed. All 12 failures are `test_actual_wav_flac_and_mp3_transcodes` subtests returning the intended `503` because the local FFmpeg build lacks libsoxr. CI uses full builds. |
| `npm run test:frontend` | Local, Node | 7/7 scripts OK (including the new `stream-ticket`) |
| GitHub Actions run [38860960460](https://github.com/Edwynl/Velvet-HiFi-MP/actions/runs/38860960460), code head `37decb5` | Python · ubuntu-latest | **pass** — 134 passed, 1 skipped (Windows-only sounddevice test), 50 subtests passed (FFmpeg with libsoxr, real transcodes) |
| 〃 | Python · windows-latest | **pass** — 135 passed, 0 skipped, 50 subtests passed (includes real `import sounddevice` + PortAudio) |
| 〃 | Windows installer job | **pass** — `install.ps1 -SkipNode` installed `sounddevice 0.5.6`; logged `[OK] Native audio output is available`; venv assertion passed |
| 〃 | Frontend · ubuntu-latest / windows-latest | **pass** / **pass** (7 scripts each) |

## Migration notes

- **Query-string credentials are no longer accepted.** Scripts using `?api_key=` or `?password=` must send `X-API-Key` / `X-Password` headers. Browser media uses tickets automatically.
- `VELVET_MAX_CONCURRENT_STREAMS` (default 3) is now **enforced** for DSP/upsampled streams. Raise it (max 10) if several rooms transcode at the same time.
- `VELVET_STREAM_TIMEOUT_MINUTES` accepts 5–720. It is a floor: longer tracks always get their full duration plus 30 minutes.
- DLNA control apps (HEOS, StreamMagic, BubbleUPnP) need the browse window opened first. Casting from the VELVET UI does not.
- `security.json` gains `media_secret`, which is generated on first use. Changing the password or API key invalidates outstanding media tickets, so playing tracks may need a restart.
- On Windows, re-run `install.bat` (or `start.bat`) to install `sounddevice`.

## Residual risks (accepted / follow-up)

- A ticket is a bearer token for **one** track or cover until it expires. If a URL leaks, only that file is exposed, for at most track length + 30 min (12 h cap) or the browse window. Tickets are not bound to a client IP, because renderers and mobile clients change addresses.
- Closing the DLNA window stops new browsing. Tickets it already handed out stay valid until expiry or until credentials are rotated. Any device on the same private subnet (e.g. guest Wi-Fi bridged to the LAN) can browse while the window is open.
- If a browser track is paused longer than its ticket lifetime and then seeked, the request returns 401. The existing recovery path calls `startBrowserPlayback`, which fetches a fresh ticket. This needs device validation.
- The ring buffer uses a short `threading.Lock` held only for memcpy, never during I/O. Callback timing under heavy GIL contention is a hardware question.
- Renderer `HEAD` requests to `/api/stream` return 405. This behavior predates this round, but HEOS/StreamMagic behavior should be confirmed.
- The concurrency limit is per server process.

## Requires real-device validation (not provable in CI)

| Area | Cases |
|------|-------|
| **DAC / Windows native** | WASAPI shared at 44.1/48 kHz sources; exclusive-capable USB DAC at 88.2–192 kHz; 16/24-bit FLAC and MP3; DSP preamp/EQ; rapid track switching (10× in 5 s) with no stuck FFmpeg or audible glitches; pause/resume; end-of-track advances state; USB disconnect/reconnect mid-track; underrun counter at `low` latency on a loaded CPU; clean-machine `install.bat` → native devices listed in Zones |
| **DLNA** | Marantz M1 (HEOS), Cambridge EXN100 (StreamMagic), BubbleUPnP: browse refused outside the window and allowed inside it; cover art appears; tracks longer than 1 h play to the end; pause over 30 min then resume; cast from the VELVET UI with auth on; seek; whether renderers send `HEAD`; behavior after the window closes mid-queue |
| **iOS (Safari + PWA)** | Login; ticket fetch per track; lock-screen and background playback beyond ticket lifetime; resume after long pause; DSP/upsampled stream at the concurrency limit (503 path); cover caching |
| **Android (Chrome + PWA)** | Same as iOS, plus switching between Wi-Fi and mobile data mid-track (ticket is not IP-bound) |
| **Network** | Unconfigured server: LAN cast works through a ticket while LAN browsing of the API is refused; Tailscale peer refused for DLNA browsing but able to stream with auth |
