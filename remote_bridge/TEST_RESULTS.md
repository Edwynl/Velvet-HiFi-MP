# Velvet Remote Bridge — Validation

## 0.1.5 (fifth review: two P1 + P2) — 2026-10-25

- `python -m unittest discover -s tests -v`: **81 tests passed**, five consecutive runs;
  `node tests/queue_sync.test.mjs`: **13/13**; `tests/manual_e2e_real_main.py`: 20/20.
- **P1-A user command vs auto-advance.** The monitor now checks for users' requests in
  flight and takes its arrival number in one critical section. Barrier tests reproduce the
  review's interleaving (monitor decides -> user Stop / new cast arrives and waits in its
  admin check -> monitor numbers its advance): on 0.1.4 (`519b1a3`) the Stop is answered
  `superseded` and the next track plays; now the Stop stops the renderer and ends the
  session, and the new track plays.
- **P1-B cold Safari/PWA reload.** `GET /bridge/session` returns the newest playing or paused
  session (renderer, track, queue ids, index, state, position); `queue_sync.js` reattaches a
  fresh page that has not chosen an output or played anything, filling track details from
  the parent, without any cast. Following the bridge (its own advance, or another phone's
  change) now also sets the page state directly, never re-casting. Node tests: reattach,
  never after the user takes over, idle/absent sessions ignored, another phone followed,
  stale revisions ignored. **Real browser** (`tests/manual_browser_stack.py`, 4 x 15 s
  FLACs): Play All, page discarded, two unattended advances, fresh page opened: it showed
  the EXN100 zone, Song 3 playing, the album queue with titles; then followed to Song 4;
  then switching to Local Web Player stopped the renderer and ended the session. Renderer
  log: one load per song, no load from the reattach.
- **P2 Stop near the end.** End of track = the renderer reports RelTime within 1 s of the end,
  or enough wall time passed since the last PLAYING read for the remainder to have played
  (renderers that reset RelTime). Tests (injected clock): Stop 3 s before the end, RelTime
  kept or reset -> no advance; natural end with reset RelTime, or reported end -> advance.
  A Stop within ~1.5 s of the end stays indistinguishable (documented).
- **P2 unplayable track.** 404 / unsupported format / renderer refusal: skipped, reported via
  the session (`skipped`, `error`) and a toast; at most once round the queue (test: whole
  queue gone with repeat 'all' looks each track up once). Parent unreachable (502): not
  skipped; retried 4 times with growing pauses, then stops with an error (test: recovers
  on retry; gives up after the retries). `upstream_json` now keeps the parent's 404.
- **P2 reproducible browser acceptance:** `tests/manual_browser_stack.py` with the steps.
- Mutation check of the new logic: any-near-end-stop, never-skip, stale snapshot, no retry
  backoff, and a looser skip bound each fail a test; a redundant bound was removed.

## 0.1.4 (fourth review: bridge-owned queue + three P2) — 2026-10-25

- `python -m unittest discover -s tests -v`: **71 tests passed**, three consecutive runs;
  `node tests/queue_sync.test.mjs`: **8/8** (no packages needed).
- **P1 unattended auto-next.** The bridge adds `/bridge/queue-sync.js` to the relayed Velvet
  page; it sends the queue (track ids, index, repeat) with each cast and on queue edits.
  A monitor thread (1 s) advances at the end of a track with no client involved, mirroring
  Velvet's cast-mode `playNext()`. Deterministic tests, monitor driven tick by tick with no
  client requests: album plays on exactly once per track (stale STOPPED reads included),
  queue end stops, Stop ends the queue, Pause and a stop on the device mid-track never
  advance, manual jump + repeat 'all' wraps, 4000 s track with RelTime reset to 0 on STOPPED,
  another source taking the renderer ends the session, queue edits and input limits (5000
  ids, ints, index must name the track), malformed queue still plays alone, the UI's
  catch-up cast is confirmed without restarting the track, the page's own late auto-next
  and the monitor never both advance, the monitor waits for a user's cast in flight, and
  a lagging TrackURI read after an advance is not counted. Mutation check: removing each
  of absorb / in-flight wait / end-of-track test / TrackURI identity / Stop-ends-session /
  repeat-all makes at least one test fail.
- **Real browser, real Velvet UI** (`main` server.py + bridge + a fake EXN100 that plays in
  real time, three 6 s FLACs, built-in browser): selected the EXN100 zone and pressed
  *Play All*, then
  1. navigated the tab away (no Velvet JS at all): the renderer loaded Song 1, 2, 3 once
     each, ~0.1 s apart, and stopped at the end of the queue;
  2. kept the page open: same single loads; the UI showed 1 → 2 → 3, never "unverified";
  3. froze the page's JS for 16 s (as iOS suspends it) during Song 1: the bridge played
     2 and 3; on wake the UI went straight to Song 3, confirmed, with no extra load.
- **P2 sleep:** the monitor thread holds the Windows keep-awake request while any queue
  session is playing or paused (gaps between tracks and long pauses included) and releases
  it on Stop / queue end (tested with the real monitor thread).
- **P2 connect/header wait:** media requests now use a socket registered for cancellation
  before it connects; Stop while the parent never answers frees the relay in under 3 s
  (open timeout 15 s, read timeout 60 s).
- **P2 packaging:** `build_zip.py` builds `Velvet-Remote-Bridge-<version>-<commit>.zip` from
  committed files only (refuses with uncommitted changes); the extracted package's own
  tests were run. Old 0.1.0 ZIPs are obsolete; no package is published before review.
- `tests/manual_e2e_real_main.py` against real `main` `server.py`: 20/20.

## 0.1.3 (third review: two P1 + stalled-source P2) — 2026-10-25

- `python -m unittest discover -s tests -v`: **55 tests passed**, three consecutive runs.
  The four control-race tests fail on 0.1.2 (`8174af5`) as the review describes; the
  security tests need the new verification hooks and error there.
- P1-A identity: a direct tailnet peer (parents' tablet, 100.80.1.20) sending
  `Tailscale-User-Login: <owner>` is refused (0.1.2 allowed it — reproduced). Identity
  comes only from the TCP source IP via `tailscale status`; the header counts only on a
  loopback connection while `tailscale serve status` shows a proxy to the control port
  and no Funnel. Loopback requests with `X-Forwarded-For`/`Forwarded`/`Tailscale-*` but
  no verified Serve get 403. `Tailscale-*`, `Forwarded`, `X-Real-IP`, `X-Forwarded-*`
  are stripped before the parent.
- P1-B ordering, each with the older request held in its admin check: Pause→Play ends
  playing (renderer saw only `Play`; the old Pause answers `superseded`), Play→Pause ends
  paused, Play→Stop stays stopped with the ticket revoked, Stop→Cast keeps the new track
  playing with its ticket. Uncontended Pause, Play, Stop all still reach the renderer.
- Kept behaviour: Cast then Pause/Play then a Range GET on the same stream (206); status at
  natural end reports STOPPED, position ≥ duration − 2 and the track_id (the UI's
  auto-next condition), and the following cast plays; cast-vs-Pause races from 0.1.2 pass.
- P2 stalled parent: with the relay blocked in `upstream.read()` on a parent that stopped
  sending, Stop frees the relay and the renderer sees EOF in under 3 s (not the 60 s read
  timeout): revoke now closes the parent connection (C-level close; on Windows a
  cross-thread `shutdown()` wakes neither a blocked recv nor a blocked send — measured).
- Found while testing: the relay used `read(128 KiB)`, which waits for the full amount;
  it now uses `read1()`, forwarding audio as it arrives on a slow link.
- `tests/manual_e2e_real_main.py` against real `main` `server.py`: 20/20.

## 0.1.2 (second review: six P1 fixes) — 2026-10-25

- `python -m unittest discover -s tests -v`: **42 tests passed**, three consecutive runs (Python 3.11).
  13 of the 14 new tests fail against 0.1.1 (`8a24f11`); the 14th, cast-after-Pause still plays,
  is the control case and passes on both.
- P1-1 proxy: a fresh interpreter with `http_proxy` pointing at a recording fake proxy — the
  default urllib opener goes through it, the bridge never does; the proxy never sees `X-API-Key`.
- P1-2 order: a Pause sent while a cast waits on a slow track lookup, and one sent while the
  cast's admin check is slow: in both, SOAP shows `Pause, SetAVTransportURI` with no `Play`,
  and the cast answers `transport: loaded`.
- P1-3 discovery: 30 description URLs on a host that accepts but never answers plus one EXN100:
  `describe()` returns the EXN100 within its 1 s deadline; total scan budget 5 s (< UI 8 s).
- P1-4 peer policy: owner's device allowed; other tailnet user, tagged device, unknown IP,
  Serve login of another user refused; `allowed_peers` by login and IP; fails closed with no
  `tailscale status`; unauthorized peers get 403 on `/`, `/api/*`, `/bridge/health`.
- P1-5 cancellation: Stop and a next-track cast end an already open, stalled media relay
  (server slot released, client sees EOF).
- P1-6 bounds: a reader that stops reading is dropped after the stall timeout, keep-awake is
  released, and a fresh `Range: bytes=4096-` GET on the same URL resumes (206); a second open
  stream over the per-renderer cap gets 503.
- `tests/manual_e2e_real_main.py` against real `main` `server.py`: 20/20 still pass.
- Real LAN: discovery 2.3 s (read-only); real `tailscale status --json` on this PC has the
  `Self.UserID` / `User` fields the peer policy uses.

**Still not verified:** EXN100 itself — in particular whether it resumes with a Range GET after
a pause longer than `MEDIA_STALL_TIMEOUT` (300 s); real two-site Tailscale; iPhone Safari.

## 0.1.1 (Claude review follow-up) — 2026-10-25

- `python -m unittest discover -s tests -v`: **28 tests passed** (Python 3.11);
  16 of them fail against the 0.1.0 `bridge.py`.
- `python -m py_compile bridge.py`: **passed**.
- **End-to-end against the real Velvet `main` `server.py`** (uvicorn, scanned 24-bit/96 kHz FLAC,
  fake renderer on 127.0.0.2 fetching like a DLNA client): 20/20 checks passed (`tests/manual_e2e_real_main.py`) —
  proxied UI, Chinese search, `/api/zones` with **no API key** (parent reports
  `passwordless_listening: true` for the bridge), cast, `HEAD` with `contentFeatures.dlna.org`
  / `transferMode.dlna.org`, full GET **sha256-identical** to the source file, `Range: bytes=1000-`
  and `bytes=-500` (206 + `Content-Range`), unsatisfiable range (416 + `bytes */size`), DIDL with
  `DLNA.ORG_OP=01` and 96000/24, status `track_id`, ticket refused from another LAN IP, phone
  browser stream through the bridge identical, Stop revokes the ticket, rebinding `Host` refused
  (421), cross-origin cast refused (403).
- **Real LAN SSDP discovery** on a Windows 11 PC with Hyper-V/WSL adapters: auto LAN IP now picks
  the Wi-Fi address (0.1.0 picked the Hyper-V switch), and discovery found a real DLNA renderer
  (Marantz M1) with an AVTransport control URL in 2.3 s. Read-only: no SOAP was sent to it.

**Still not verified:** Cambridge Audio EXN100 itself (SetAVTransportURI / Play / Pause / Seek,
display of art and format), iPhone Safari over a real Tailscale WAN link, >1 h tracks, >30 min
pause, Tailscale drop/recovery during playback, parent restart mid-track, Windows Defender
prompts on a clean machine.

## 0.1.0 — 2026-10-25

- `python -m unittest discover -s tests -v`: **14 tests passed**.
- `python -m py_compile bridge.py`: **passed**.
- Simulated upstream Velvet HTTP API, simulated DLNA renderer, media relay byte equality,
  `GET`/`HEAD`/`Range`, IP-bound grants, stop revocation, admin and Origin checks.

**Release style:** independent folder and standalone ZIP, no modification to parent Velvet main,
no GitHub Actions invocation.
