# Windows validation — administrator management and Gemini.bp attribution

Date: 2026-10-17, Australia/Sydney.

Work is isolated on `codex/manage-admin-password`, based on Claude's
`9882216` (schema 11), with the previously validated PR #10 library recovery
and association fixes carried forward as `705138b`. Main, PR #2 and PR #10
are unchanged. Commits use `[skip ci]`; no workflows are launched.

## Management protection

The server checks an independent administrator password on every management
operation, including from localhost and when LAN auth bypass is enabled.
Listening keys/passwords/cookies/media tickets do not elevate access. No
administrator password or reusable unlock session is saved in browser storage.
Initial setup requires authenticated, direct localhost access; the password
must differ from the shared listening password. Management is locked until
setup. Existing listening credentials are not changed.

Protected operations and setup instructions are in [ADMIN_MANAGEMENT.md](../../ADMIN_MANAGEMENT.md).
The preview is updated to this branch; its separate administrator password is
left for the owner to configure in Settings. No credential is chosen for them.

## Actual test results

| Execution | Result |
| --- | --- |
| Initial full Windows PowerShell runner, including venv setup, pip install and npm ci | PASS: 265 Python tests, 169 subtests, 13 frontend/PWA suites |
| Final full Windows PowerShell runner (`-SkipInstall`, full test selection; two album identity regressions added) | **PASS: 267 Python tests, 169 subtests, 13/13 frontend/PWA suites; exit 0** |
| Separate administrator + real HTTP proxy security regression | **PASS: 17 tests**, including 4 real Uvicorn/HTTP tests |
| Corrected Gemini.bp re-read on a copy of the real schema 11 library | **PASS: 9 tracks**, artist remains Gem; track/album IDs and playlist/history rows preserved |

No Python failures or skipped tests in the final run. One existing
FastAPI/Starlette TestClient deprecation warning remains. Browser playback
error/fallback messages in frontend output are expected fault-injection tests.
An extra mocked security-status route was added to the DSP fixture so the
new Settings panel is tested without an unhandled-request warning.

- [Full PowerShell output](full-powershell.txt)
- [Administrator and real HTTP security output](admin-security.txt)
- [Real-library copy rescan evidence](gemini-rescan.json)

The security suite checks shared-key rejection before handlers, repeated
verification after one successful change, missing/wrong passwords, local
bypass, first-setup origin/proxy restrictions, separate passwords, password
rotation, guess throttling, corrupted credentials and normal playback access.
Frontend tests exercise cancellation/Escape, retry after a wrong password,
approval for exactly one operation, repeated prompting, first-setup guidance
and unchanged browsing/radio behavior.

## Applied local library correction

`Gemini.bp` (album 8897; Tidal release 497747095) is assigned to the distinct
artist **Gem**, retaining **Cord Allman** as co-artist. Main library and preview
snapshot were updated. Exactly 9 original FLACs now have `artist` and
`albumartist` set to `Gem, Cord Allman`; no other tags changed.

Before changing anything, both databases and all 9 complete FLACs were backed
up in the primary data directory's
`gemini_attribution_backup_20261017T030053Z` folder. Its `report.json` records
the before/after state. FLAC compressed-frame SHA-256, PCM MD5, sample rate,
bit depth, channels, sample count, embedded images and the catalog cover hash
match. No Track IDs, playlist positions/timestamps or history rows were changed.

The real-library rescan runs on a database copy and sends generated cover-cache
output to its own validation directory. It confirms the corrected identity
persists without changing the original cover. Live HTTP checks confirm album
8897 now displays Gem, is absent from artist 80's album list, and refuses a
shared listener's refresh request with HTTP 403.

Browser automation became unavailable because local browser use was disabled
in Work settings. Existing Chrome content was not claimed to be refreshed;
reload the current preview page to fetch the verified updated server state.
