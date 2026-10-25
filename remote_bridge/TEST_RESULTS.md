# Velvet Remote Bridge — Validation

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
