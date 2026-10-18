"""Offline integration tests for opt-in, durable batch MusicBrainz enrichment."""
import asyncio
from contextlib import contextmanager
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from artist_alias_batch import (
    ArtistAliasEnrichmentJob, RateLimitedMusicBrainzClient,
    _select_candidates,
)
from artist_alias_sync import AliasSyncUnavailable
from artist_aliases import add_alias
from db_schema import LATEST_SCHEMA_VERSION, apply_migrations


class DummySession:
    def __init__(self, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return {"ok": True}


class ArtistAliasBatchTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "library.db"
        with self.connection() as db:
            self.assertEqual(apply_migrations(db), LATEST_SCHEMA_VERSION)
            self.assertEqual(LATEST_SCHEMA_VERSION, 14)
            db.executemany(
                "INSERT INTO artists(id,name,sort_name,mbid) VALUES(?,?,?,?)",
                [(1, "徐佳瑩", "徐佳瑩", ""),
                 (2, "周杰倫", "周杰倫", ""),
                 (3, "陳綺貞", "陳綺貞", "a47ac8f5-6774-4540-842f-f1eb0a19845c")],
            )
            db.commit()

    def tearDown(self):
        self.tempdir.cleanup()

    def connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    def job(self):
        return ArtistAliasEnrichmentJob(
            self.connect, client_factory=DummySession,
        )

    def run_batch(self, job, mock, *, mode="pending", limit=2, all_remaining=False):
        with patch("artist_alias_batch.sync_artist_aliases", side_effect=mock):
            db = self.connect()
            try:
                result = job.start(
                    db, mode=mode, limit=limit, all_remaining=all_remaining,
                )
            finally:
                db.close()
            if result["running"]:
                job._thread.join(timeout=10)
                self.assertFalse(job._thread.is_alive(), "worker did not finish")
        return job.state.snapshot()

    @staticmethod
    async def fake_sync(db, artist_id, *, client):
        name = "English Alias " + str(artist_id)
        assert add_alias(db, artist_id, name)
        return {"status": "ok", "added": [name]}

    def test_migration_and_cleanup_when_artist_changes(self):
        db = self.connect()
        try:
            self.assertEqual(
                db.execute("SELECT name FROM sqlite_master WHERE "
                           "type='table' AND name='artist_alias_enrichment_state'"
                ).fetchone()[0], "artist_alias_enrichment_state"
            )
            db.execute("INSERT INTO artist_alias_enrichment_state "
                       "(artist_id,status) VALUES(1,'ok')")
            db.execute("UPDATE artists SET name='徐佳莹' WHERE id=1")
            self.assertIsNone(db.execute("SELECT artist_id FROM "
                    "artist_alias_enrichment_state WHERE artist_id=1").fetchone())
            db.execute("INSERT INTO artist_alias_enrichment_state "
                       "(artist_id,status) VALUES(2,'ok')")
            db.execute("DELETE FROM artists WHERE id=2")
            self.assertIsNone(db.execute("SELECT artist_id FROM "
                    "artist_alias_enrichment_state WHERE artist_id=2").fetchone())
        finally:
            db.close()

    def test_schema_13_database_upgrades_without_changing_artists(self):
        # An existing library gains checkpoint storage without touching names.
        with self.connection() as db:
            before = db.execute("SELECT id,name,mbid FROM artists ORDER BY id").fetchall()
            db.execute("DROP TRIGGER IF EXISTS trg_alias_enrichment_artist_rename")
            db.execute("DROP INDEX IF EXISTS idx_artist_alias_enrichment_status")
            db.execute("DROP TABLE artist_alias_enrichment_state")
            db.execute("DELETE FROM schema_migrations WHERE version=14")
            db.commit()
            self.assertEqual(apply_migrations(db), 14)
            after = db.execute("SELECT id,name,mbid FROM artists ORDER BY id").fetchall()
            self.assertEqual([tuple(row) for row in before],
                             [tuple(row) for row in after])
            self.assertIsNotNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='artist_alias_enrichment_state'"
            ).fetchone())

    def test_pending_batch_is_persistent_and_resumes_without_repeating(self):
        first = self.job()
        status = self.run_batch(first, self.fake_sync, limit=2)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["processed"], 2)
        with self.connection() as db:
            overview = first.overview(db)
            self.assertEqual(overview["attempted_artists"], 2)
            self.assertEqual(overview["remaining_artists"], 1)
            self.assertEqual(overview["aliases_imported"], 2)
            self.assertEqual([row[0] for row in _select_candidates(db, "pending", 50)], [2])
        restarted = self.job()
        status = self.run_batch(restarted, self.fake_sync, limit=2)
        self.assertEqual(status["processed"], 1)
        with self.connection() as db:
            self.assertEqual(restarted.overview(db)["remaining_artists"], 0)
            self.assertEqual(restarted.overview(db)["synced_artists"], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artist_aliases")
                             .fetchone()[0], 3)

    def test_retry_unresolved_is_separate_from_new_artists(self):
        attempts = []
        async def mixed(db, artist_id, *, client):
            attempts.append(artist_id)
            if artist_id == 3:
                return {"status": "not_found", "added": []}
            if artist_id == 1:
                raise AliasSyncUnavailable("MusicBrainz unavailable")
            return await self.fake_sync(db, artist_id, client=client)

        first = self.job()
        outcome = self.run_batch(first, mixed, all_remaining=True)
        self.assertEqual(outcome["status"], "paused_error")
        self.assertEqual(outcome["processed"], 2)
        self.assertEqual(attempts, [3, 1])
        with self.connection() as db:
            self.assertEqual(first.overview(db)["remaining_artists"], 1)
            self.assertEqual(first.overview(db)["unresolved_artists"], 2)
            self.assertEqual({row[0] for row in _select_candidates(db, "retry", 50)}, {3, 1})
        second = self.job()
        result = self.run_batch(second, self.fake_sync, mode="retry", limit=2)
        self.assertEqual(result["synced"], 2)
        with self.connection() as db:
            self.assertEqual(second.overview(db)["unresolved_artists"], 0)
            self.assertEqual(second.overview(db)["remaining_artists"], 1)

    def test_stop_and_duplicate_start_are_safe(self):
        entered, release = threading.Event(), threading.Event()
        async def blocked(db, artist_id, *, client):
            entered.set()
            await asyncio.to_thread(release.wait, 3)
            return await self.fake_sync(db, artist_id, client=client)
        job = self.job()
        with patch("artist_alias_batch.sync_artist_aliases", side_effect=blocked):
            with self.connection() as db:
                job.start(db, all_remaining=True)
            self.assertTrue(entered.wait(3))
            with self.connection() as db:
                with self.assertRaises(RuntimeError):
                    job.start(db)
            self.assertTrue(job.stop()["stopping"])
            release.set()
            job._thread.join(timeout=10)
            self.assertFalse(job._thread.is_alive())
        with self.connection() as db:
            self.assertEqual(job.overview(db)["attempted_artists"], 1)
            self.assertEqual(job.overview(db)["remaining_artists"], 2)
        self.assertEqual(job.state.snapshot()["status"], "stopped")

    def test_request_pacing_wrapper_delegates(self):
        async def check():
            fake = DummySession()
            paced = RateLimitedMusicBrainzClient(fake, interval=0)
            await paced.get("https://musicbrainz.org/ws/2/artist/1", params={"a": "b"})
            await paced.get("https://musicbrainz.org/ws/2/artist/2")
            self.assertEqual(len(fake.requests), 2)
            self.assertEqual(fake.requests[0][1]["params"], {"a": "b"})
            self.assertTrue(paced.rate_limited)
        asyncio.run(check())

    def test_validation_and_empty_job(self):
        with self.connection() as db:
            with self.assertRaises(ValueError):
                _select_candidates(db, "invalid", 5)
            with self.assertRaises(ValueError):
                _select_candidates(db, "pending", 20001)
            self.assertEqual([x[0] for x in _select_candidates(db, "pending", 50)],
                             [3, 1, 2])
        job = self.job()
        self.run_batch(job, self.fake_sync, all_remaining=True)
        with self.connection() as db:
            finished = job.start(db, limit=5)
        self.assertFalse(finished["running"])
        self.assertEqual(finished["status"], "complete")
        self.assertEqual(finished["total"], 0)


if __name__ == "__main__":
    unittest.main()
