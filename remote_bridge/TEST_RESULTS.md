# Velvet Remote Bridge — Validation

## 0.1.7 (seventh review: one P1 + two P2) — 2026-10-25

- `python -m unittest discover -s tests -v`: **97 tests passed**, five consecutive runs;
  `node tests/queue_sync.test.mjs`: **26/26**; `tests/manual_e2e_real_main.py`: **20/20**. Repository suites (unchanged by
  this PR): pytest 568 passed; frontend Node 23/23 (`frontend-contract` only from a path
  without spaces: it does not decode `%20` in its own path).
- **Real browser, administrator password set, two devices** (`manual_browser_stack.py` +
  `manual_admin_two_devices.mjs`; the fake EXN100 now keeps RelTime on Stop, as some
  renderers do, and can be taken over by "another source"): **all checks passed in three runs on fresh stacks** (34 or 35 checks: when Shuffle
  puts the playing song last, one extra same-track edit moves it first so a next song
  exists; an earlier run without that step failed only because the queue correctly ended).
  The same driver against 0.1.6 (`4a4cbbe`) reproduces the review's P1: 2.3 s after phone
  A's Stop, tablet B cast Song 4 (`load` + `play` on the renderer), and when the other
  source's song ended a page cast Song 4 over it (30/35).
- **P1 a Stop from one device is final for all.** Two layers:
  1. Bridge (independent of any page's timers): `/api/cast/status` reports a STOPPED /
     NO_MEDIA_PRESENT renderer at position 0. Velvet's own cast auto-next fires only on
     STOPPED with position >= duration - 2, so no page can cast the next track over
     another device's Stop, an external source, or the bridge's own advance. The bridge
     monitor alone plays what follows (it already did, unattended). Status also stops
     serving the monitor's last read once the session has ended (it was up to 1.2 s old,
     from before the Stop). `Play` with no bridge session (after a Stop, or with another
     source on the renderer) is refused with "choose a track" instead of resuming a revoked
     stream or someone else's.
  2. Page: when its session disappears or goes idle, queue_sync.js disarms
     `castAutoNextArmed`, marks the cast unverified, stops Velvet's cast polling, shows
     "<renderer> stopped playing" once, and sends nothing; a later cast from any device
     (or Play on the renderer) is followed again.
  Python: B at 198/200 s, A stops, renderer keeps RelTime 198 with the revoked TrackURI:
  three status polls report STOPPED/0/no track, session inactive, zero SOAP actions;
  Play afterwards restarts nothing, a chosen track plays; StreamMagic taking the renderer
  mid-song ends the session and its song's end (RelTime kept) reports 0. Node: Stop
  disarms and stops polling, toast once, only session reads sent; idle then playing again
  is followed; a new cast after a Stop is followed; a page that never saw its session is
  not told. Browser: song within 2.4 s of its end, phone A switches output (Velvet sends
  Stop), the tablet's queue sync cut off (its `/api/bridge/session` requests aborted) so
  only Velvet's own polling runs there: no load, no Play, no cast for 9 s past the song's
  end; with sync restored the tablet shows stopped, auto-next disarmed, polling ended; 4 s
  later still nothing. Then phone casts again and the tablet follows; another source takes
  the EXN100: session ends, both pages disarm, nothing casts over it before or after its
  song ends.
- **P2 session read across a Stop or cast.** `session_info` answers only if the session it
  read is still the renderer's after the (unlocked) renderer reads, else looks again (3
  tries, then 503 so the page simply retries). Tests hold the renderer read: a concurrent
  Stop gives `{active: false}`, a concurrent new cast gives the new track.
- **P2 Windows asleep before the first remote cast.** New `stay_awake` (default true): the
  monitor thread holds `ES_SYSTEM_REQUIRED` for as long as the bridge runs, not only during
  a session. Tests: requested with no session, released at shutdown; config default,
  false, invalid value. On this PC `SetThreadExecutionState` returned the held
  `ES_CONTINUOUS|ES_SYSTEM_REQUIRED`; `powercfg /requests` needs an elevated prompt (not
  available here) and another process already held the system flag, so the per-process view
  remains a hardware-acceptance item. The README now lists the required host setup:
  `stay_awake`, power plan never sleep/hibernate on AC, lid action, Tailscale *Run
  unattended*, start at logon with auto-logon or "run whether user is logged on or not";
  what `stay_awake` cannot prevent (Start-menu sleep, power button, lid, update restarts).
- Mutation check: status keeping the end position, no session re-check, stale snapshot
  after Stop, blind Play, `stay_awake` ignored (Python); no release, release leaving
  auto-next armed, no re-follow (Node): each makes at least one test fail.
- Not covered here: physical EXN100 (including whether it keeps RelTime on Stop), real
  Tailscale LAN/WAN, iPhone/PWA lock and reload, 30-minute pause, FLAC 44.1/96/192 kHz on
  hardware, Windows power settings on the real host.

## 0.1.6 (sixth review: four P1 + P2) — 2026-10-25

- `python -m unittest discover -s tests -v`: **90 tests passed**, five consecutive runs;
  `node tests/queue_sync.test.mjs`: **22/22**; `tests/manual_e2e_real_main.py`: **20/20**.
- **Real browser, administrator password set** (`tests/manual_browser_stack.py` with
  `ADMIN_PASSWORD`, driven by `tests/manual_admin_two_devices.mjs`: real `main` server.py, the
  bridge, a fake EXN100 playing 4 x 25 s FLACs in real time, two Chromium sessions with
  separate cookie jars as phone and tablet): **23/23 checks, three runs on separate stacks**.
  The same script against 0.1.5 (`0116da0`) fails from the first queue edit: every
  `/bridge/queue` POST answered 403, the cold tablet page showed Repeat off and itself tried to
  post the queue.
- **P1-1 admin cookie path.** Velvet's admin cookie is `Path=/api`, so the session and queue
  endpoints moved to `/api/bridge/session[/udn]` and `/api/bridge/queue/{udn}` (answered by the
  bridge, never proxied); same admin and Origin checks. Test with a real `http.cookiejar`:
  login through the bridge's proxy, cast, then a queue edit return 200, no Cookie header added
  by hand; the jar sends the cookie to `/api/bridge/queue/x` and not to `/bridge/queue/x`.
  queue_sync.js posts through Velvet's `managementFetch`, so a second device's first edit
  opens Velvet's admin dialog (browser check) instead of failing silently; a cancelled prompt
  is not repeated for the same edit.
- **P1-2 repeat on reattach.** The reattached page takes the session's repeat mode and
  repeat-button colour, and records the session as its starting point, so attaching sends
  nothing. Browser: tablet cold page shows Repeat All, two cycles later the bridge revision
  is unchanged; phone set Repeat All, cold reload, still All on screen and on the bridge, then
  a jump to the last song wraps to the first.
- **P1-3 two devices.** `update_queue` is a compare-and-swap: `expected_revision` (required)
  must equal the session revision, else 409 with the current revision and nothing changes.
  queue_sync.js keeps the revision it last agreed with; a newer session revision is taken in
  even when the current song is the same, merged three-way with the page's unsent edits
  (each side's change kept where only it changed; both changing the tracks: the bridge's
  version stands and the page says so). Node tests with two pages on a CAS fake bridge:
  removal taken in and kept by a later repeat toggle; toggle before seeing the removal merged;
  a change landing between look and post refused then merged; conflicting reorders; edit
  across the bridge's own advance. Browser: phone removes an upcoming track, tablet shows it
  while the song plays on; tablet's repeat toggle keeps the removal; simultaneous phone
  Shuffle and tablet Repeat both kept and both pages converge.
- **P1-4 edits vs auto-advance.** Each advance try plays from a snapshot of one queue
  revision; `_play` re-checks the revision under the renderer lock before SetAVTransportURI
  and returns `stale`, and the advance chooses again from the edited queue (bounded).
  `update_queue` takes the renderer lock, so no edit lands between that check and the new
  session; an edit of the old track arriving then gets 409 and the page merges it. Any
  unexpected error in an advance now keeps the end pending and retries (bounded) instead of
  stranding the queue. The UI's catch-up cast no longer replaces the session's queue with
  the page's copy. Barrier tests (held `/api/tracks/6` lookup): next track removed ->
  plays 7; reordered -> plays the new next; queue cut to the current track -> ends cleanly,
  no exception; repeat changed -> kept and wraps; edit during SetAVTransportURI waits and
  gets 409. Browser: the bridge's advance played the next track of the edited queue and
  never loaded the removed one; both pages followed.
- **P2 metadata on reattach.** The current track first, then 5 before / 40 after it in
  the background (4 at a time); others load as their rows scroll into view in the queue
  panel (IntersectionObserver, rows updated in place). Loading stops once the page shows
  another queue. Node: a 5000-entry reattach makes at most 46 track requests.
- Mutation check: each of no revision re-check, unserialized edit, no CAS, no stuck guard,
  absorb overwriting, old `/bridge/queue` path (Python), and no repeat on adopt, no
  same-track adoption, fixed revision, merge dropping a side, unbounded or uncancelled
  loading, repeated admin prompt, no conflict detection, no resync after 409 (Node) makes
  at least one test fail.
- Not covered here: physical EXN100, real Tailscale LAN/WAN, 30-minute pause, FLAC
  44.1/96/192 kHz on hardware (separate acceptance gate). The fake renderer does not fetch
  audio bytes; byte/Range fidelity stays covered by the unit tests and
  `manual_e2e_real_main.py`.

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
