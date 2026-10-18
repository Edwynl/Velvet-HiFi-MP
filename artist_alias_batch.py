"""Opt-in, rate-limited, resumable MusicBrainz artist alias enrichment.

No automatic activity at startup or during search. A user explicitly starts a
small batch, with one checkpoint committed per artist in schema 14. Failures
remain eligible for a separate user-requested retry; success is not repeated.
The worker owns its SQLite connection on its own thread.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from typing import Callable, Awaitable

import httpx

from artist_alias_sync import AliasSyncUnavailable, sync_artist_aliases
from library_jobs import JobState
from library_search_index import refresh_track_search_index

log = logging.getLogger("velvet.artist_alias_batch")
MAX_BATCH_SIZE = 50
MAX_ALL_ARTISTS = 20000
DEFAULT_BATCH_SIZE = 25
REQUEST_INTERVAL_SECONDS = 1.25
# No long writer transaction: refresh a small number of dirty rows after each
# handful of imported artists, then commit every remaining slice separately.
REFRESH_ROW_BUDGET = 800
REFRESH_BATCH_SIZE = 200
INDEX_REFRESH_EVERY_ARTISTS = 5
MAX_FINAL_REFRESH_PASSES = 150

# New artists are never skipped. Completed artists are excluded from next
# batches, including those with no verified aliases; retry is explicit.
_RETRY_STATUSES = (
    "not_found", "ambiguous", "identity_mismatch", "invalid_mbid",
    "unavailable", "error",
    # The row changed identity while the remote lookup was in flight, so no
    # evidence was committed. The next attempt starts from the current
    # identity, which makes it retryable without weakening review.
    "identity_changed",
)
# needs_review is deliberately not retryable. It needs a human identity choice.


class RateLimitedMusicBrainzClient:
    """Wrap the shared HTTP client; globally pace requests within one batch."""

    rate_limited = True

    def __init__(
        self,
        client,
        *,
        interval: float = REQUEST_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.client = client
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self.last_request_at: float | None = None

    async def get(self, url: str, **kwargs):
        if self.last_request_at is not None:
            remaining = self.last_request_at + self.interval - self._clock()
            if remaining > 0:
                await self._sleep(remaining)
        self.last_request_at = self._clock()
        return await self.client.get(url, **kwargs)


def _select_candidates(db: sqlite3.Connection, mode: str, limit: int) -> list[tuple[int, str]]:
    if mode not in ("pending", "retry"):
        raise ValueError("Mode must be pending or retry")
    if not 1 <= limit <= MAX_ALL_ARTISTS:
        raise ValueError("Batch selection must be between 1 and 20000")
    if mode == "pending":
        sql = """
            SELECT a.id, a.name FROM artists a
            LEFT JOIN artist_alias_enrichment_state e ON e.artist_id=a.id
            WHERE e.artist_id IS NULL AND LENGTH(TRIM(a.name)) > 0
            ORDER BY CASE WHEN NULLIF(a.mbid,'') IS NOT NULL THEN 0 ELSE 1 END, a.id
            LIMIT ?
        """
        params = (limit,)
    else:
        marks = ",".join("?" for _ in _RETRY_STATUSES)
        sql = f"""
            SELECT a.id, a.name FROM artists a
            JOIN artist_alias_enrichment_state e ON e.artist_id=a.id
            WHERE e.status IN ({marks})
            ORDER BY e.attempted_at, a.id LIMIT ?
        """
        params = (*_RETRY_STATUSES, limit)
    return [(int(row[0]), str(row[1])) for row in db.execute(sql, params)]


def _checkpoint(db: sqlite3.Connection, artist_id: int, status: str,
                added: int = 0, error: str = "") -> None:
    db.execute(
        """INSERT INTO artist_alias_enrichment_state
             (artist_id,status,aliases_added,attempted_at,error)
           VALUES (?,?,?,strftime('%s','now'),?)
           ON CONFLICT(artist_id) DO UPDATE SET
             status=excluded.status,
             aliases_added=artist_alias_enrichment_state.aliases_added+excluded.aliases_added,
             attempted_at=excluded.attempted_at,
             error=excluded.error""",
        (artist_id, status, max(0, added), str(error or "")[:300]),
    )


class ArtistAliasEnrichmentJob:
    """Single background worker plus persistent per-artist checkpoints.

    Construct with the thread-local db factory from db_pool.get_db. The worker
    never receives a connection from the request thread.
    """

    def __init__(
        self,
        db_factory: Callable[[], sqlite3.Connection],
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ):
        self.db_factory = db_factory
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = JobState({
            "running": False, "stopping": False, "status": "idle",
            "mode": "pending", "processed": 0, "total": 0,
            "synced": 0, "unresolved": 0, "aliases_added": 0,
            "current_artist": "", "started_at": None, "finished_at": None,
            "error": None,
        })

    def overview(self, db: sqlite3.Connection) -> dict:
        counts = db.execute(
            """SELECT
                 (SELECT COUNT(*) FROM artists) AS total_artists,
                 (SELECT COUNT(*) FROM artist_alias_enrichment_state) AS attempted,
                 (SELECT COUNT(*) FROM artist_alias_enrichment_state
                  WHERE status='ok') AS synced_artists,
                 (SELECT COUNT(*) FROM artist_alias_enrichment_state
                  WHERE status!='ok') AS unresolved_artists,
                 (SELECT COALESCE(SUM(aliases_added),0)
                  FROM artist_alias_enrichment_state) AS aliases_imported,
                 (SELECT COUNT(*) FROM artist_alias_enrichment_state
                  WHERE status='needs_review') AS needs_review_artists,
                 (SELECT COUNT(*) FROM artist_alias_enrichment_state
                  WHERE status IN ('not_found','ambiguous','identity_mismatch',
                                   'invalid_mbid','unavailable','error')) AS retryable_artists"""
        ).fetchone()
        result = self.state.snapshot()
        result.update(
            total_artists=int(counts[0]),
            attempted_artists=int(counts[1]),
            synced_artists=int(counts[2]),
            unresolved_artists=int(counts[3]),
            aliases_imported=int(counts[4]),
            needs_review_artists=int(counts[5]),
            retryable_artists=int(counts[6]),
            remaining_artists=max(0, int(counts[0]) - int(counts[1])),
        )
        return result

    def review_queue(self, db: sqlite3.Connection, *, limit: int = 25) -> list[dict]:
        """Artists who need explicit MBID selection, not automatic retry."""
        rows = db.execute(
            "SELECT a.id, a.name FROM artist_alias_enrichment_state e "
            "JOIN artists a ON a.id=e.artist_id "
            "WHERE e.status='needs_review' ORDER BY a.name COLLATE NOCASE "
            "LIMIT ?", (min(100, max(1, limit)),),
        ).fetchall()
        return [{"id": int(row[0]), "name": str(row[1])} for row in rows]

    def start(self, db: sqlite3.Connection, *, mode: str = "pending",
              limit: int = DEFAULT_BATCH_SIZE, all_remaining: bool = False) -> dict:
        """Claim and start one batch; raises RuntimeError if already running."""
        candidates = _select_candidates(
            db, mode, MAX_ALL_ARTISTS if all_remaining else limit,
        )
        with self._lock:
            if self.state.is_running():
                raise RuntimeError("Artist alias enrichment already running")
            self._stop.clear()
            if not candidates:
                self.state.finish(
                    status="complete", mode=mode, stopping=False, total=0,
                    processed=0, synced=0, unresolved=0, aliases_added=0,
                    current_artist="", error=None,
                )
                return self.state.snapshot()
            self.state.mark_running(
                status="running", mode=mode, all_remaining=all_remaining, stopping=False,
                total=len(candidates), processed=0, synced=0, unresolved=0,
                aliases_added=0, current_artist="", error=None,
                started_at=time.time(), finished_at=None,
            )
            self._thread = threading.Thread(
                target=self._worker, args=(candidates,),
                name="velvet-artist-alias-enrichment", daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self.state.finish(status="error", error="Could not start worker")
                raise
            return self.state.snapshot()

    def stop(self) -> dict:
        """Cooperative stop, checked between artists; current request may finish."""
        with self._lock:
            if self.state.is_running():
                self._stop.set()
                self.state.update(stopping=True, status="stopping")
            return self.state.snapshot()

    def _worker(self, candidates: list[tuple[int, str]]) -> None:
        try:
            asyncio.run(self._process(candidates))
        except Exception as exc:
            log.exception("Artist alias enrichment stopped on unexpected error")
            self.state.finish(
                status="error", current_artist="", stopping=False,
                error=str(exc)[:240],
            )
        else:
            current = self.state.snapshot()
            self.state.finish(
                status=("paused_error" if current.get("status") == "paused_error" else
                        "stopped" if self._stop.is_set() else "complete"),
                current_artist="", stopping=False,
                error=current.get("error"),
            )

    def _flush_dirty_keys(self, *, drain: bool = False) -> None:
        """Keep the dirty index small without monopolizing SQLite writers.

        Each call opens and commits its own short transaction; no connection
        or lock is held across a MusicBrainz request.
        """
        # A single heavily represented singer can dirty tens of thousands
        # of tracks. Catch up between artists with several SHORT transactions
        # instead of leaving the whole backlog until the import is finished.
        for _ in range(MAX_FINAL_REFRESH_PASSES if drain else 6):
            db = self.db_factory()
            try:
                row = db.execute(
                    "SELECT (SELECT COUNT(*) FROM track_search_dirty) + "
                    "(SELECT COUNT(*) FROM catalog_search_dirty)"
                ).fetchone()
                if not row or row[0] == 0:
                    return
                refresh_track_search_index(
                    db, batch_size=REFRESH_BATCH_SIZE,
                    max_rows=REFRESH_ROW_BUDGET,
                )
                db.commit()
            except (sqlite3.Error, ValueError):
                db.rollback()
                log.exception("Alias search index refresh deferred; dirty values remain searchable")
                return
            finally:
                db.close()

    async def _process(self, candidates: list[tuple[int, str]]) -> None:
        # One persistent HTTP session and rate limit across artists.
        added_since_refresh = 0
        artists_since_refresh = 0
        async with self.client_factory(timeout=15) as session:
            client = RateLimitedMusicBrainzClient(session)
            for artist_id, name in candidates:
                if self._stop.is_set():
                    break
                self.state.update(current_artist=name)
                db = self.db_factory()
                status = "error"
                added = 0
                error = ""
                try:
                    result = await sync_artist_aliases(db, artist_id, client=client)
                    status = str(result.get("status", "error"))
                    added = len(result.get("added") or [])
                    _checkpoint(db, artist_id, status, added)
                    db.commit()
                except AliasSyncUnavailable as exc:
                    db.rollback()
                    status, error = "unavailable", str(exc)
                    _checkpoint(db, artist_id, status, error=error)
                    db.commit()
                    # Network outage/rate limiting: do not hammer the server
                    # with the rest of the queued artists.
                    self._stop.set()
                    self.state.update(error=error, status="paused_error")
                except (sqlite3.Error, OSError):
                    db.rollback()
                    raise  # Persistence failures must stop rather than be hidden.
                except Exception as exc:
                    db.rollback()
                    status, error = "error", str(exc)
                    _checkpoint(db, artist_id, status, error=error)
                    db.commit()
                    log.exception("Alias enrichment failed for artist %s", artist_id)
                finally:
                    db.close()

                self.state.increment("processed")
                self.state.increment("aliases_added", added)
                self.state.increment("synced" if status == "ok" else "unresolved")
                artists_since_refresh += 1
                added_since_refresh += added
                if added or artists_since_refresh >= INDEX_REFRESH_EVERY_ARTISTS:
                    # Newly verified popular artists may dirty thousands of
                    # tracks at once. Keep draining even if the next few
                    # artists require review and do not add more aliases.
                    self._flush_dirty_keys()
                    artists_since_refresh = 0
                    added_since_refresh = 0
                if self._stop.is_set():
                    break

        # Drain the remaining queue in short transactions. Never wait until
        # the end of a multi-hour import to perform the *first* refresh.
        if self.state.snapshot().get("aliases_added"):
            self._flush_dirty_keys(drain=True)
