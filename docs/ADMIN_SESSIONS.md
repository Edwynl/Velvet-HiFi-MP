# Listening and administrator access

Direct localhost and verified Tailscale peers can browse, play in their browser,
read covers and view output devices without the shared library password.
Tailscale Serve is accepted only when its current local configuration, hostname
and forwarded peer match the running tailnet. Funnel and arbitrary forwarded
headers do not grant this exemption. Other network access keeps the existing
listening authentication boundary.
The supported server.py launcher retains the real socket peer by disabling
Uvicorn proxy rewriting. HTTPS origin/cookie handling is restored only for a
verified local Serve route, not for arbitrary forwarded headers.

Settings and Remote Access are gated before their private configuration loads.
The server separately checks cover changes, album/playlist deletions, metadata
rewrites, library paths/scans, shared DSP changes, Cast and native output control,
credentials, CORS and DLNA access. Device lists and browser playback remain
available without administrator access. An API key or image/media ticket cannot
unlock management.

An existing `admin-security.json` administrator verifier is preserved. If none
exists, the current password in `security.json` becomes the administrator
password; no password or security file is rewritten for this transition.

The first protected action opens one administrator dialog. Concurrent protected
requests share that dialog, cancellation performs no mutation, and a successful
unlock retries the pending operation. The password is cleared immediately and
never saved in browser storage. An opaque HttpOnly, SameSite=Strict session
cookie authorizes subsequent management requests. It is Secure on HTTPS and
does not have a persistent Max-Age. Server-side sessions expire after 12 hours,
on server restart or when their password verifier changes. A fresh session
requires one new unlock. Cross-origin management is rejected. Five failed
administrator attempts per client cause a five-minute lockout without blocking
normal listening.

The unmerged `feat/auth-admin-only` branch was inspected during integration.
Its whole-file UI/auth changes are not a replacement for main's PWA fixes or
this session-based policy; merge future work selectively against current main.

VU meters use a separate viewport modal outside the fullscreen player's
transformed and pointer-disabled container. Their close/style controls reserve
the iPhone safe area and stay fixed while the meter contents can scroll.
The existing iOS standalone 100vh repair and main-page safe areas are retained.
