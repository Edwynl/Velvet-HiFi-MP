# Issue #30: UPnP reliability follow-up

Branch: `codex/dlna-request-reliability`; latest reviewed baseline: `73c473d`.
Local Windows validation, 2026-10-22. No merge, deployment, PR creation or
GitHub Actions dispatch is part of this change. The commit uses `[skip ci]`.
Runtime: Python 3.12.10, pytest 9.1.1, using the project's existing virtualenv.

## P1 changes

- `BoundedUPnPServer` limits live handlers to 16. Excess connections are closed
  immediately before starting a worker. A slot is released in `finally` after
  the worker exits, including errors, disconnects and timeouts; worker-start
  failures also release their reservation.
- The existing server loop checks a monotonic deadline for every accepted
  connection. After 15 seconds it shuts down that socket regardless of incoming
  activity, including slow-drip headers/body and a blocked response writer.
  Production polling is 100 ms; the existing 5-second idle timeout still applies.
  No per-connection timer or watchdog threads are added. Disconnects and broken
  pipes are handled without unhandled worker exceptions.
- Browse counts the matching children and reads rows in batches of 32 using
  one cursor. Zero means all remaining children; positive counts retain their
  requested meaning. There is no item-count cap: 1505-item track and album
  responses succeed when they fit within the 8 MiB response budget.
  Each item is checked against the actual UTF-8 SOAP size, including double XML
  escaping, DIDL/SOAP wrappers and NumberReturned digits, before it is retained.
  An oversized response faults with HTTP 500 / UPnP 720 and immediately closes
  the row iterator/connection. It never sends successful incomplete results.
  SQL ordering includes ID tie-breakers for equal names, years and track numbers.
  `StartingIndex`, `NumberReturned`, `TotalMatches` and BrowseMetadata remain
  covered. Catalog counts use SQL COUNT rather than loading every ID.

## Regression evidence

During the initial follow-up, three regression cases ran against the source of
commit `5175bf7`, without altering this checkout: slow-drip headers/body,
connection saturation, and real 1505-item catalog browsing. They produced
15 expected failed assertions/subtests and no test errors on that baseline.
The item-count fault introduced in that follow-up was rejected in the review of
`73c473d`; this revision removes it and tests successful complete large results.
The updated real-SQL/HTTP large-container test was executed against the in-memory
source of `73c473d`: it produced 12 expected failed assertions/subtests, with no
test errors. The same test succeeds on this revision. Instrumented SQLite
cursors also verify fetchmany(32), no fetchall for item rows, and closed
connections; oversized responses stop rendering before consuming all 1505 rows.

New tests use ephemeral loopback ports and temporary SQLite databases. They
cover total deadlines despite continuous input, normal-latency SOAP, overload
refusal and recovery, worker-start failure, broken pipes, incomplete input,
concurrent Browse, 1505 tracks/albums, zero/positive counts, sequential pages,
BrowseMetadata, batched DB reads/cleanup, stable paging across tied sort fields,
actual oversized metadata, and exact UTF-8/escaping/envelope byte boundaries.
Existing body/framing,
authentication and DSP HEAD/GET/Range regressions remain in place.

## Remaining risks and scope

- Cambridge EXN100/StreamMagic, HEOS/M1 and BubbleUPnP have not been tested on
  physical devices. Only responses exceeding the actual byte budget receive
  fault 720 and require smaller pages; 1505 items alone no longer cause failure.
- Deadline enforcement closes the transport; it does not forcibly terminate a
  Python thread executing SQLite or serialization. Its concurrency slot stays
  reserved until the handler really exits. Rows are fetched in batches of 32,
  and retained serialized items stay within the byte budget. A single unusually
  large metadata row or batch can still temporarily allocate more memory than
  the wire limit; final joining/escaping/encoding also uses temporary buffers.
- The pre-existing SSDP LOCATION/control-URL validation concern in Issue #30
  remains outside this P1 follow-up. Discovery, Tailscale, cast controls, DSP,
  streaming service 8765 and PWA sources are not changed.

## Previous local results (`73c473d`)

- Focused: `python -m pytest tests/test_upnp_server.py tests/test_server_api.py -q`
  — 127 passed, 131 subtests passed, 1 existing Starlette deprecation warning.
- `npm run test:pwa` — passed.
- `git diff --check` — passed.
- Full: `python -m pytest tests -q` — 564 passed, 817 subtests passed,
  no failures/skips, 1 existing Starlette deprecation warning (218.96 seconds).
- Final UPnP-only check: 20 passed, 44 subtests passed.

## Latest local results (large-library compatibility revision)

- `python -m pytest tests/test_upnp_server.py -q` — 24 passed, 46 subtests
  passed (12.37 seconds).
- `python -m pytest tests/test_upnp_server.py tests/test_server_api.py -q` —
  131 passed, 133 subtests passed, no failures/skips; 1 existing Starlette
  deprecation warning (81.66 seconds).
- `python -m pytest tests -q` — 568 passed, 819 subtests passed, no failures/skips;
  1 existing Starlette deprecation warning (226.77 seconds).
- `npm run test:pwa` — passed. Streaming and frontend/PWA sources have no diff
  from `73c473d`.
- `git diff --check` — passed.
