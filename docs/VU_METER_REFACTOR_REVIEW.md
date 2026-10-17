# VELVET VU Meter refactor — review handoff

**Branch:** `feat/standards-based-vu-meter-ballistics` (based on `main` at `8274dbf`). Keep separate from PR #2 and PR #10. **Do not merge without owner approval.** All commits use `[skip ci]`; do not start or rerun GitHub Actions.

## What changed

- `static/js/vu-meter-engine.js`: independent, testable PCM time-domain full-wave rectified average detector, calibrated to a sine-wave RMS reference. No FFT byte averages or peak-hold lock.
- 2nd-order spring/damping approximation: design step reaches 99% in ~300 ms, ~1.11% overshoot. Integration accounts for real elapsed time (30/60 fps equivalence).
- `static/index.html`: stereo analysers use `getFloatTimeDomainData`, and connect to `AudioContext.destination` through a zero-gain node to keep the silent graph pulled. Default Classic VU at -18 dBFS sine RMS; -20/-18/-15/-12/-9/-6 selectable; Responsive is explicitly visual-only, not a calibrated meter. VU numerals are positioned on a voltage/amplitude-derived arc.
- Audio overlay mirrors the current browser stream for display without changing the main playback routing. Playback retries are throttled and stream-seek sync is reduced; the duplicate audio is silenced through an AudioContext gain node. **This architecture still uses a second stream and can require a second transcoder** — benchmark with network and CPU tools before shipping.
- Service worker cache increment to load new meter script on PWA update. JSDOM harness allowlist extended.
- `tests/frontend/vu-meter.test.mjs` plus `npm run test:vu` and existing `npm run test:frontend` command integration.

## Required local commands (Windows)

```powershell
git switch feat/standards-based-vu-meter-ballistics
npm ci
npm run test:vu
npm run test:frontend
python -m pytest tests -q
```

Only run the last command if local Python dependencies are already installed. Do not claim these suites have passed until actually executed on a checked-out branch. Verify no workflows are queued. Test the full PWA under browser hardware.

## Important reviewer acceptance checks

1. Confirm the new inline classic-script module is served before the main script in `index.html` and the JSDOM fixtures; inspect browser console with DevTools.
2. Confirm stereo L/R independence, relative loudness accuracy at multiple sine test levels, zero-input release, 300-ms response, slight overshoot, and equal time response at 30/60/120 fps.
3. Confirm standard/near-standard ballistics are *a sampled-browser approximation*, not certified IEC 60268-17 compliance. Digital -18 dBFS alignment is an optional engineering convention.
4. Check mobile Chrome Android and Safari iOS VU overlay, lock/unlock, rapid song changes, seek/pause, 30-minute pause, network interruptions, and output mode switching. Confirm the VU never breaks the independent main HTML5 audio playback chain.
5. Check waveforms and CPU usage for 44.1/48/96/192 kHz stereo FLAC; inspect whether the second VU mirror stream requests an additional FFmpeg decode and whether mobile/remote traffic is tolerable.
6. Validate CSS mark/needle geometry on small phones, tablets, desktop and McIntosh-inspired blue mode; in particular the pivot and labels align at 0 VU, -20 VU and +3 VU. Avoid changing cover-art quality.
7. Security: check short-lived media tickets on mirrored request, auth renewal/revocation, failure throttling; do not expose long-lived credentials.
8. Review skip-CI effect carefully: GitHub Actions budgets are exhausted. Do not manually trigger GitHub CI. Keep PR draft, if eventually created.

## Known limitations

- VU works only when using Velvet's browser output; when playing to native DAC / DLNA, the browser stream is not automatically the actual remote signal. The UI explicitly notes browser-only metering.
- The PCM analyzer samples the browser-decoded mirror, not actual external DAC analogue output. Digital meter values are approximate and neither LUFS nor True Peak.
- Browser PCM windows are read on the display frame. Future AudioWorklet integration could improve capture granularity if mobile/browser compatibility is validated.
- Physical devices and full local regression suites have **not** been run in this ChatGPT session; review and validate before merging.
