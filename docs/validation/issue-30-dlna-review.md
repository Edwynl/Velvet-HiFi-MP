# Issue #30: UPnP reliability follow-up

Branch: `codex/dlna-request-reliability`; reviewed baseline: `5175bf7`.
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
- Browse counts the matching children before loading rows. Zero means all
  remaining children. Positive counts retain their requested meaning. If the
  actual result would exceed 1000 items, it returns HTTP 500 / UPnP fault 720
  (`Cannot process the request`), rather than a successful truncated result.
  A request for a large positive count succeeds when fewer than 1001 matching
  children remain. Oversized serialized SOAP replies (>8 MiB) also fault.
  `StartingIndex`, `NumberReturned`, `TotalMatches` and BrowseMetadata remain
  covered. Catalog counts use SQL COUNT rather than loading every ID.

## Regression evidence

Three regression cases were executed against the in-memory source of reviewed
commit `5175bf7`, without altering this checkout: slow-drip headers/body,
connection saturation, and real 1505-item catalog browsing. They produced
15 expected failed assertions/subtests and no test errors on that baseline.

New tests use ephemeral loopback ports and temporary SQLite databases. They
cover total deadlines despite continuous input, normal-latency SOAP, overload
refusal and recovery, worker-start failure, broken pipes, incomplete input,
concurrent Browse, 1505 tracks/albums, zero/positive counts, sequential pages,
BrowseMetadata and the serialized response size limit. Existing body/framing,
authentication and DSP HEAD/GET/Range regressions remain in place.

## Remaining risks and scope

- Cambridge EXN100/StreamMagic, HEOS/M1 and BubbleUPnP have not been tested on
  physical devices. Controllers receiving fault 720 must request smaller pages;
  clients that never paginate cannot browse oversized containers in one reply.
- Deadline enforcement closes the transport; it does not forcibly terminate a
  Python thread executing SQLite or serialization. Its concurrency slot stays
  reserved until the handler really exits. Result rows are bounded before
  fetching; the byte check is applied after serialization, so unusually large
  metadata can temporarily allocate more than the wire response limit.
- The pre-existing SSDP LOCATION/control-URL validation concern in Issue #30
  remains outside this P1 follow-up. Discovery, Tailscale, cast controls, DSP,
  streaming service 8765 and PWA sources are not changed.

## Local results

- Focused: `python -m pytest tests/test_upnp_server.py tests/test_server_api.py -q`
  — 127 passed, 131 subtests passed, 1 existing Starlette deprecation warning.
- `npm run test:pwa` — passed.
- `git diff --check` — passed.
- Full: `python -m pytest tests -q` — 564 passed, 817 subtests passed,
  no failures/skips, 1 existing Starlette deprecation warning (218.96 seconds).
- Final UPnP-only check: 20 passed, 44 subtests passed.
