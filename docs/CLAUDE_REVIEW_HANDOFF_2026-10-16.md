# Claude review handoff — Velvet audio/security stabilization

Target: Draft PR #1 on `fix/auth-boundaries-and-soxr-resampling`. **Do not merge before validation.**

## New commits
- `audio_streaming.py`: shared async FFmpeg stdout generator; closes process on cancellation, generator close, timeout, or exception.
- `server.py`: validates single RFC 9110 byte ranges including suffix; returns HTTP 416 with `Content-Range: bytes */size`; correct WAV MIME for PCM resampling; removes fake DoP path and explicitly returns 501 for requested DoP.
- `tests/test_audio_streaming.py`: regressions for ranges, subprocess completion and early stream close.

## Items Claude should review carefully
1. **Decoder shutdown:** verify asynchronous generator finalization when Starlette's StreamingResponse disconnects or a client closes an audio tab. Simulate a subprocess that produces bytes then hangs and confirm no child process is left behind. Confirm cancellation propagates.
2. **Range semantics:** tests for `bytes=0-3`, open-ended ranges, suffix ranges, large numbers, empty files, invalid/multi-ranges and 416 response headers. A missing `Range` must keep using the original direct-file response.
3. **Format correctness:** FFmpeg upsamples into `pcm_s32le` WAV, not FLAC. The endpoint now returns `audio/wav`, whereas DSP-only emits FLAC. Test actual browser and DAC support, particularly iOS/Safari.
4. **DoP:** the previous implementation emitted PCM while labelling it DoP. `dop != none` now returns HTTP 501 rather than deceptive sound output. Plan true DoP separately with proper marker/bit packing, native DAC transport and hardware acceptance tests.
5. **Security:** inspect PR's earlier auth changes, including first-time localhost bootstrap, key rotation login flow, proxy/Tailscale behaviour and the difference between local-network bypass and management endpoints.
6. **Test coverage:** run Python suite (`python -m pytest tests -q`) and frontend suite (`npm run test:frontend`), then manually test play/pause/seek/next/previous and mobile background recovery.
7. **Potential follow-up:** rate limiting for FFmpeg processes and stream concurrency (present settings don't prove enforcement); limit PWA cover cache; avoid long-lived keys in stream URLs.

## Verification matrix
| Case | Expected |
|---|---|
| Direct FLAC/MP3 Range `bytes=0-3` | 206, exactly 4 bytes, correct `Content-Range` |
| Direct suffix Range `bytes=-4` | 206, final 4 bytes |
| Invalid/out-of-bounds Range | 416 + `Content-Range: bytes */N` |
| PCM resampling | Valid WAV header, correct output sample rate |
| DSP-only | FLAC stream and explicit DSP indicator |
| DoP requested | 501 until true DoP is implemented |
| Stop streaming midway | FFmpeg child terminated and reaped |
| Password enabled | All credential-changing endpoints reject missing auth |
| New first password from external IP | 403 |
| Login and API key renewal | Browser retains current valid token |

**Limits:** GitHub files were edited via the connector; the full Python/JS suites have not been executed in the actual project environment. FFmpeg option syntax was validated independently on FFmpeg 7.1.5. No main branch changes.
