# Velvet Remote Bridge — Validation

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
