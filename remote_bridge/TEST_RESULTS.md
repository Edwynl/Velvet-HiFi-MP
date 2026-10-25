# Velvet Remote Bridge 0.1.0 — Validation

Date: 2026-10-25

- `python -m unittest discover -s tests -v`: **14 tests passed**.
- `python -m py_compile bridge.py`: **passed**.
- Simulated upstream Velvet HTTP API: original library/auth state, login and admin session, proxying read requests, cast and status contracts.
- Simulated DLNA renderer SOAP transport: device discovery XML filtering, action and XML request construction, SOAP Fault rejection.
- Media relay: byte-for-byte FLAC content unchanged; `GET`, `HEAD`, `Range`/206 and `Content-Range`, short-lived IP-bound media grant, stop revocation.
- Access boundary: authenticated zones; separated admin verification for playback mutations; local Origin protection; disallow public control bind and cross-host SSDP XML control endpoints.

**Not verified:** actual Windows device discovery; iPhone Safari/PWA; Cambridge Audio EXN100 SetAVTransportURI/play/seek implementation; long playback (1h+), network interruption, Windows Defender firewall, Tailscale throughput and WAN packet loss. Physical-device acceptance is required before release as production-ready.

**Release style:** independent folder and standalone ZIP, no modification to parent Velvet main, no GitHub Actions invocation.
