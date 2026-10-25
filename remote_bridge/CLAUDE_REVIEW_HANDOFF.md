# Claude Code Review Handoff - Velvet Remote Bridge v0.1.0

**Status:** Independent prototype for code review, **NOT production accepted**. Do not merge into `main` until Windows + Tailscale + physical Cambridge Audio EXN100 acceptance is complete. This PR intentionally adds **only** `remote_bridge/` and does not alter the running parent Velvet server. **Do not trigger GitHub Actions**; use local validation.

## 1. User's actual problem / why this exists

The user owns one large library of local FLAC/high-resolution music files on a Windows PC at their parents' home, where their main `Velvet-HiFi-MP` server runs. That installation streams losslessly to a Marantz Model M1 on the parents' LAN. From the user's own home, iPhone/iPad can access the parent Velvet over Tailscale and play in the browser, but the Cambridge Audio **EXN100** is on the user's *local* LAN and cannot access a private Tailscale-only HTTP media URL or be found by the remote Velvet's multicast SSDP scan. Using AirPlay is not an acceptable guaranteed original-file/bit-perfect path. StreamMagic can use local UPnP servers, but its library/search experience is too cumbersome for the user's huge library (artists, albums, aliases, simplified/traditional Chinese search).

**User target:** Search and browse using the **existing Velvet UI** on iPhone; choose `EXN100` as Velvet's audio output/Zone; EXN100 directly fetches the source FLAC over its normal Ethernet/Wi-Fi interface; no USB, no AirPlay, no second copy of music files, no second music database or search indexing, no need to manage the collection in StreamMagic, no modifications to the parents' live Velvet installation.

## 2. Intended playback architecture

```text
Parents' Windows: main Velvet (:8765) + ONLY FLAC files + ONLY database
           |       authenticated /api and /api/stream over Tailscale
           v
Own home's Windows: remote_bridge/bridge.py (:8877 Tailscale control/UI)
           |       :8878 on private home LAN only, relay audio bytes
           v
Cambridge Audio EXN100: DLNA/UPnP SetAVTransportURI + HTTP GET/HEAD/Range

Phone Safari --Tailscale--> own-home Bridge :8877 --> proxied Velvet UI/API
Phone never needs to relay the audio data itself.
```

The bridge proxies the upstream Velvet UI, browsing/search, playlist, login, covers and other existing APIs to the parent server. It **intercepts only local renderer** discovery and casting paths (`/api/zones`, `/api/devices`, `/api/cast/...`). It contacts the EXN100 using LAN SSDP and UPnP AVTransport SOAP, hands EXN100 a local HTTP URL on `:8878`, and streams the *unmodified* original file bytes from the parent Velvet `/api/stream/{track_id}` to EXN100. The local relay supports `HEAD`, `GET`, `Range`, `206`, `Content-Length`/`Content-Range`. No FFmpeg, DSP, ReplayGain, upsampling or resampling on this path; if the user requests DSP or ReplayGain, **fail explicitly** instead of silently misrepresenting processing/bit-perfect status.

The parent server is **not modified**. The local bridge keeps temporary in-memory per-track media grants, and does not persist audio, a copied SQLite music DB, credentials, or an index. A second Windows PC is needed and must remain powered while listening.

## 3. What was implemented

- `bridge.py`: stdlib-only HTTP servers for UI/control and LAN media relay; strict upstream URL configuration; Tailscale/LAN interface selection; local SSDP discovery; basic AVTransport SOAP; per-renderer serialization and superseded cast handling; short-lived random grants bound to the selected renderer's source IP; `GET/HEAD/Range` byte relay; original Velvet API reverse proxy; admin and API credential checks; Origin protections.
- `start_bridge.bat`: create `config.json` from example on first run, open Notepad, start Python.
- `config.example.json`: default separate control Tailscale `:8877` and media LAN `:8878`, preferred EXN100 name.
- `README_安装说明.md`: physical installation/configuration/troubleshooting.
- `tests/test_bridge.py` and `run_tests.bat`: simulated main Velvet and renderer tests.
- `TEST_RESULTS.md`: exact local test record and untested acceptance cases.

No changes to the parent `server.py`, `upnp_server.py`, front-end, library scanner or database are intended in v0.1. Please evaluate that deliberate separation instead of assuming the bridge belongs in a full second Velvet installation.

## 4. Compatibility assumptions to challenge

Main Velvet API as inspected 2026-10-25:
- `GET /api/zones` returns `{browser,native,network:{devices}}`; the UI treats `network.devices[].udn` as a cast target.
- `POST /api/cast/{udn}` receives `track_id`, optional `dsp_profile_id`, `replaygain`, `start_seconds`, `recast`, `request_id`.
- `GET /api/cast/status/{udn}` and `GET /api/cast/result/{udn}/{request_id}`; `POST /api/cast/control/{udn}/{Play|Pause|Stop}`.
- `GET /api/tracks/{track_id}` provides format, title/artist/album/duration.
- `GET/HEAD /api/stream/{track_id}?upsample=none&replaygain=off` supplies the original audio stream, including range requests.
- `X-API-Key` authorizes Velvet API access. Existing admin-session cookies may be needed for `/api/cast` controls. The bridge forwards the parent's UI/login flow so the phone can use the existing Velvet authentication.

Questions for Claude: Does reverse proxying all regular endpoints without HTML rewriting safely preserve absolute URLs, cookies, Origin and CSP? Is the custom media relay reliable for EXN100/StreamMagic's HEAD-before-GET and long tracks? Is status/next track/race handling compatible with parent Velvet's front-end cast state? Does EXN100 actually expose an AVTransport endpoint to receive a push URI for files? Is the UPnP discovery and SOAP reply parsing strict enough? Do not mark these confirmed without device evidence.

## 5. Prioritized review checklist (please produce concrete findings with file/line references)

**P0 - Security and functional correctness:**
1. SSRF/open-proxy/UPnP XML control URL redirects, multicast spoofing, IPv6/host checks, private LAN limits, DNS rebinding, HTTP request smuggling, URL normalization and restricted listen addresses. Verify no untrusted device can fetch the audio grant and no credentials leak in logs, HTML, playlist metadata or persisted files.
2. Cross-origin protections, CSRF/login/admin-session forwarding, CORS/CSP compatibility, internal-only endpoints; test ticket expiry and authorization revocation after upstream API key rotation.
3. Preserve audio exact bytes and HTTP semantics: Range suffix/partial, `206/416`, `HEAD`, Content-Length/Content-Range, proper streaming, disconnect handling, timeouts and resource release. Avoid unbounded memory or threads for a hostile LAN client.
4. Device discovery and actual AVTransport control: `SetAVTransportURI`, `Play`, `Pause`, `Stop`, `Seek`, state, SOAP Fault handling; no unintended renderer reset after a failed cast.
5. Concurrency: back-to-back track switches, late status responses, rapid Pause/Play/Stop, grant lifecycle, natural end-of-track auto-next, requests timed out client-side, stale cast generation.

**P1 - UX and integration:**
6. From phone on Tailscale, login to the proxied **original Velvet UI**, search 50k+ tracks / Chinese aliases, choose `EXN100` Zone; verify no StreamMagic catalog browsing or second library scan needed.
7. EXN100 display retains album art/title and correct original file sample rate/bit depth; note that `byte-identical FLAC transport` does not itself prove DAC bit-perfect operation.
8. Windows installer and startup on clean Windows 10/11: `py` launcher, Tailscale network adapter selection, firewall prompts, startup errors and troubleshooting.
9. Edge cases: wrong LAN adapter, 172.16 networks, non-/24 LAN, multiple EXN100 or DLNA renderers, sleep/wake, parent Velvet shutdown, Tailscale disconnect, poor WAN upload speed, remote file seek, long playback >1h, pause >30m, actual EXN100 firmware compatibility.
10. Verify that `config.json`, secrets, logs, user media and development artifacts are not committed. Do not add GitHub Actions jobs; run validation locally.

## 6. Tests/acceptance gates

Locally run:

```powershell
cd remote_bridge
python -m unittest discover -s tests -v
python -m py_compile bridge.py
```

Initial snapshot: **14 unit/integration-simulation tests passed** on Python 3.13 (2026-10-25); these include simulated parent Velvet API, dummy DLNA renderer, relay byte equality, HEAD and Range, expiration, admin checks, and origin checks. **Not yet verified** on physical Windows/EXN100 or actual Tailscale WAN. No GitHub Actions were run.

Physical-device acceptance before merge/release:

1. Parents' Velvet untouched; playback in parents' home on Marantz M1 still works.
2. Own-home Windows has *no* music file backup and no library scanner; iPhone on Tailscale opens Bridge URL and sees the parent's full Velvet library/search/playlist/favorites.
3. EXN100 discovered on own LAN; Velvet output Zones show it; selecting and playing a 16/44.1 FLAC succeeds without phone/USB/AirPlay relaying sound.
4. 24/96 and 24/192 FLAC play when supported; note original file format and sample rate in EXN100/StreamMagic status; distinguish delivery format from verifiable bit-perfect path.
5. Repeated skip/seek, pause/resume, >60-minute track, >30-minute pause, quick network failure/recovery, parent restart: errors understandable, no privilege leaks, safe recovery.
6. Stop/zone switch revokes old grant, no free external exposure, media URL is accessible only from the selected EXN100 IP via private LAN.

## 7. Review and release instructions

Please inspect **all changed files under `remote_bridge/`** against the live `main` API and security behavior. Return (a) P0/P1/P2 issues with exact paths and line numbers; (b) minimal fixes; (c) local test evidence; (d) unverified device assumptions; (e) a merge/no-merge decision. Prefer a dedicated follow-up commit on this branch, not sweeping changes to main. Do not merge until the user explicitly approves and device tests have passed. Do not trigger GitHub Actions due to existing CI quota limits.
