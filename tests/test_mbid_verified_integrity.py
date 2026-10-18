"""Integrity of verified MusicBrainz identities across interacting features.

Covers the review findings on #26:

* P1 - automatic fingerprint metadata must not overwrite a verified MBID, and
  a row that changes identity while a lookup is in flight must not end up with
  the wrong ID marked verified.
* P2 - re-saving the same MBID must not discard a completed sync checkpoint.
* P2 - an artist parked in ``needs_review`` because it had only one album must
  become eligible again once a second distinctive album supplies the evidence
  that automatic corroboration needs.

All HTTP calls are replaced by deterministic fake responses; no network access.
"""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import enrichment
import artist_alias_sync
from artist_alias_batch import _select_candidates
from artist_aliases import fetch_aliases
from artist_alias_sync import resolve_search_alias, sync_artist_aliases
from db_schema import apply_migrations
from tests.test_artist_alias_corroboration import (
    A,
    B,
    MusicBrainzFake,
    group,
    no_wait,
)


class FingerprintMbidGuardTests(unittest.TestCase):
    """P1: AcoustID metadata is a guess and must not outrank a confirmed ID."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "library.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
                        "VALUES(1,'徐佳瑩','徐佳瑩',?,1)", (A,))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _write(self, mbid):
        with patch.object(enrichment, "DB_PATH", self.path):
            return enrichment._set_unverified_artist_mbid(mbid, 1)

    def _row(self):
        return self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()

    def test_refuses_to_overwrite_a_verified_mbid(self):
        self.assertFalse(self._write(B))
        row = self._row()
        self.assertEqual(row["mbid"], A)
        self.assertEqual(row["mbid_verified"], 1)

    def test_writes_when_the_mbid_is_unverified(self):
        self.db.execute("UPDATE artists SET mbid_verified=0 WHERE id=1")
        self.db.commit()
        self.assertTrue(self._write(B))
        row = self._row()
        self.assertEqual(row["mbid"], B)
        self.assertEqual(row["mbid_verified"], 0, "changing the ID must clear trust")


class SyncIdentityRaceTests(unittest.TestCase):
    """P1: evidence gathered for one identity must not be committed to another."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
                        "VALUES(1,'徐佳瑩','徐佳瑩',?,1)", (A,))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    class RacingFake(MusicBrainzFake):
        """Overwrites the artist row the first time it is asked for anything."""

        def __init__(self, db, artist_id, mbid, **kwargs):
            super().__init__(**kwargs)
            self.db = db
            self.artist_id = artist_id
            self.incoming = mbid
            self.raced = False

        async def get(self, url, *, params=None, headers=None):
            if not self.raced:
                self.raced = True
                self.db.execute(
                    "UPDATE artists SET mbid=? WHERE id=?", (self.incoming, self.artist_id),
                )
                self.db.commit()
            return await super().get(url, params=params, headers=headers)

    def test_in_flight_change_does_not_verify_the_new_id(self):
        fake = self.RacingFake(self.db, 1, B)
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            result = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(result["status"], "identity_changed")
        row = self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()
        self.assertEqual(row["mbid"], B, "the competing writer's value stands")
        self.assertEqual(
            row["mbid_verified"], 0,
            "an identity that appeared mid-lookup must never be marked verified",
        )
        self.assertEqual(result["added"], [])
        self.assertEqual(fetch_aliases(self.db, 1), [])

    def test_unchanged_identity_still_imports(self):
        """The guard must not break the normal path it is protecting."""
        fake = MusicBrainzFake()
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            result = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(result["status"], "ok")
        row = self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()
        self.assertEqual(row["mbid"], A)
        self.assertEqual(row["mbid_verified"], 1)
        self.assertIn("LaLa Hsu", fetch_aliases(self.db, 1))

    def test_identity_changed_is_retryable(self):
        """It is transient, not a verdict about the artist."""
        from artist_alias_batch import _RETRY_STATUSES
        self.assertIn("identity_changed", _RETRY_STATUSES)
        self.assertNotIn("needs_review", _RETRY_STATUSES)


class CheckpointHygieneTests(unittest.TestCase):
    """P2: a no-op write must not throw away finished work."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
                        "VALUES(1,'徐佳瑩','徐佳瑩',?,1)", (A,))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _mark_ok(self):
        self.db.execute(
            "INSERT INTO artist_alias_enrichment_state(artist_id,status,aliases_added) "
            "VALUES(1,'ok',3)"
        )
        self.db.commit()

    def _status(self):
        row = self.db.execute(
            "SELECT status FROM artist_alias_enrichment_state WHERE artist_id=1"
        ).fetchone()
        return row["status"] if row else None

    def test_same_value_update_keeps_the_checkpoint(self):
        self._mark_ok()
        self.db.execute("UPDATE artists SET mbid=mbid WHERE id=1")
        self.db.commit()
        self.assertEqual(self._status(), "ok")

    def test_changed_mbid_still_invalidates_the_checkpoint(self):
        self._mark_ok()
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (B,))
        self.db.commit()
        self.assertIsNone(self._status())

    def test_changed_name_still_invalidates_the_checkpoint(self):
        self._mark_ok()
        self.db.execute("UPDATE artists SET name='Lala Hsu' WHERE id=1")
        self.db.commit()
        self.assertIsNone(self._status())


class NeedsReviewRecheckTests(unittest.TestCase):
    """P2: one album is not the end of the story for automatic corroboration."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name) "
                        "VALUES(1,'徐佳瑩','徐佳瑩')")
        self.db.execute(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(1,1,'理想人生',2012)"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _sync(self, fake):
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            return asyncio.run(sync_artist_aliases(self.db, 1, client=fake))

    def _candidate_ids(self, mode):
        return [artist_id for artist_id, _ in _select_candidates(self.db, mode, 25)]

    def _mark_review(self):
        self.db.execute(
            "INSERT INTO artist_alias_enrichment_state(artist_id,status,aliases_added) "
            "VALUES(1,'needs_review',0)"
        )
        self.db.commit()

    def test_one_album_is_not_corroborated(self):
        result = self._sync(MusicBrainzFake())
        self.assertEqual(result["status"], "needs_review")

    def test_review_artist_is_in_neither_queue(self):
        self._mark_review()
        self.assertNotIn(1, self._candidate_ids("pending"))
        self.assertNotIn(1, self._candidate_ids("retry"))

    def test_second_album_reinstates_the_artist(self):
        self._mark_review()
        self.db.execute(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(2,1,'心里学',2017)"
        )
        self.db.commit()
        self.assertIn(
            1, self._candidate_ids("pending"),
            "new evidence must put the artist back in the queue",
        )

    def test_completed_sync_survives_a_new_album(self):
        self.db.execute(
            "INSERT INTO artist_alias_enrichment_state(artist_id,status,aliases_added) "
            "VALUES(1,'ok',3)"
        )
        self.db.commit()
        self.db.execute(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(2,1,'心里学',2017)"
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT status FROM artist_alias_enrichment_state WHERE artist_id=1"
        ).fetchone()
        self.assertEqual(row["status"], "ok", "only review status is cleared")

    def test_second_album_allows_automatic_corroboration(self):
        """One album => needs_review => two albums => verified, no user action."""
        self.assertEqual(self._sync(MusicBrainzFake())["status"], "needs_review")
        self.db.execute(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(2,1,'心里學',2017)"
        )
        self.db.commit()
        fake = MusicBrainzFake(release_groups={A: [group("理想人生", A), group("心里學", A)]})
        result = self._sync(fake)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verification"], "release_corroborated")
        row = self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()
        self.assertEqual(row["mbid"], A)
        self.assertEqual(row["mbid_verified"], 1)


class TwoConnectionAtomicityTests(unittest.TestCase):
    """P1 follow-up: the identity check and the writes are one transaction.

    A plain SELECT takes no writer lock in SQLite, so comparing the snapshot
    and then writing separately leaves a window *after* the comparison. This
    reproduces the interleaving the review described with two real WAL
    connections: an artist carrying a guessed, unverified MBID A, the user
    confirming that same A (`known_mbid=True`, so the code would not restore
    it), and a fingerprint writer on the second connection repointing the row
    to B in the gap before the first alias insert. The outcome that must never
    happen is B ending up verified while carrying A's aliases.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "library.db"
        self.db = self._connect(5.0)
        self.db.execute("PRAGMA journal_mode=WAL")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
                        "VALUES(1,'徐佳瑩','徐佳瑩',?,0)", (A,))
        self.db.execute("INSERT INTO albums(id,artist_id,title,year) "
                        "VALUES(1,1,'理想人生',2012)")
        self.db.execute("INSERT INTO albums(id,artist_id,title,year) "
                        "VALUES(2,1,'心里學',2017)")
        self.db.commit()
        # No wait: a contended write must fail fast instead of hanging.
        self.other = self._connect(0.0)
        self.competitor = {"blocked": False, "wrote": False, "saw_transaction": None}

    def _connect(self, timeout):
        conn = sqlite3.connect(self.path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def tearDown(self):
        self.db.close()
        self.other.close()
        self._tmp.cleanup()

    def _competing_write(self, db, artist_id):
        if not (self.competitor["wrote"] or self.competitor["blocked"]):
            self.competitor["saw_transaction"] = db.in_transaction
            try:
                self.other.execute(
                    "UPDATE artists SET mbid=? WHERE id=?", (B, artist_id),
                )
                self.other.commit()
                self.competitor["wrote"] = True
            except sqlite3.OperationalError:
                self.competitor["blocked"] = True

    def test_competitor_cannot_land_between_check_and_first_alias_write(self):
        real_add_alias = artist_alias_sync.add_alias

        def instrumented(db, artist_id, name):
            self._competing_write(db, artist_id)
            return real_add_alias(db, artist_id, name)

        fake = MusicBrainzFake()
        with patch("artist_alias_sync.asyncio.sleep", no_wait), \
                patch("artist_alias_sync.add_alias", instrumented):
            result = asyncio.run(sync_artist_aliases(
                self.db, 1, client=fake, confirmed_mbid=A,
            ))
        self.db.commit()

        self.assertFalse(self.competitor["wrote"],
                         "a competing writer must not be able to repoint the row")
        self.assertTrue(self.competitor["blocked"], "the competing write was locked out")
        self.assertTrue(
            self.competitor["saw_transaction"],
            "a write transaction must already be open at the first alias insert",
        )
        self.assertEqual(result["status"], "ok")
        row = self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()
        self.assertEqual(row["mbid"], A)
        self.assertEqual(row["mbid_verified"], 1)
        self.assertIn("LaLa Hsu", fetch_aliases(self.db, 1))

    def test_window_is_closed_not_merely_reordered(self):
        """Sanity: without the lock the same interleaving does succeed."""
        db = self._connect(5.0)
        self.db.execute("INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
                        "VALUES(2,'另一位','另一位',?,0)", (A,))
        self.db.commit()
        self.other.execute("UPDATE artists SET mbid=? WHERE id=2", (B,))
        self.other.commit()
        row = db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=2"
        ).fetchone()
        self.assertEqual(row["mbid"], B)
        db.close()

    def test_identity_changed_rolls_back_the_locked_transaction(self):
        """A conflict must release the write lock, not hold it until commit."""

        class MovingTarget(MusicBrainzFake):
            async def get(self, url, *, params=None, headers=None):
                if not getattr(self, "moved", False):
                    self.moved = True
                    self.db.execute(
                        "UPDATE artists SET mbid=? WHERE id=?", (B, 1),
                    )
                    self.db.commit()
                return await super().get(url, params=params, headers=headers)

        fake = MovingTarget(release_groups={A: [group("理想人生", A), group("心里學", A)]})
        fake.db = self.db
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            result = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(result["status"], "identity_changed")
        self.assertFalse(self.db.in_transaction, "the lock must not stay held")
        row = self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()
        self.assertEqual(row["mbid"], B)
        self.assertEqual(row["mbid_verified"], 0)
        # The competing connection can still write, so nothing is still locked.
        self.other.execute("UPDATE artists SET mbid=? WHERE id=1", (A,))
        self.other.commit()


class ResolveSearchAliasSnapshotTests(unittest.TestCase):
    """P2 follow-up: the trusted-ID fallback must validate its snapshot too.

    `resolve_search_alias` trusts an already verified local MBID, so it skips
    restoring the ID and would stamp `mbid_verified` onto whatever the row
    holds by the time the locked store runs. It now folds a pre-network
    snapshot of every local artist and passes it through.
    """

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute(
            "INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
            "VALUES(1,'徐佳瑩','徐佳瑩',?,1)", (A,)
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _mutating_fake(self, mutate):
        """Mutate during a detailed lookup, i.e. after the snapshot is taken.

        The search endpoint returns last path segment `artist`; the detailed
        lookups that follow return the MBID, which is where the snapshot has
        already been captured.
        """

        class Racing(MusicBrainzFake):
            async def get(self, url, *, params=None, headers=None):
                if not getattr(self, "done", False):
                    if url.rstrip("/").rsplit("/", 1)[-1] != "artist":
                        self.done = True
                        mutate()
                return await super().get(url, params=params, headers=headers)

        return Racing()

    def _resolve(self, fake):
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            return asyncio.run(
                resolve_search_alias(self.db, "Lala Hsu", client=fake)
            )

    def test_rename_during_lookup_is_rejected(self):
        def rename():
            self.db.execute("UPDATE artists SET name='另一個名字' WHERE id=1")
            self.db.commit()

        result = self._resolve(self._mutating_fake(rename))
        self.assertEqual(result["status"], "identity_changed")
        self.assertEqual(fetch_aliases(self.db, 1), [])

    def test_mbid_change_during_lookup_fails_even_earlier(self):
        """Repointing the ID makes the trusted path inapplicable, not merely stale.

        The row no longer carries the candidate's verified ID, so the store is
        never reached and the artist goes back to review with nothing written.
        """
        def repoint():
            self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (B,))
            self.db.commit()

        result = self._resolve(self._mutating_fake(repoint))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(fetch_aliases(self.db, 1), [])
        self.assertEqual(self._stored()["mbid"], B)

    def _stored(self):
        return self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()

    def test_unverified_row_does_not_use_the_trusted_path(self):
        """Without a verified flag there is nothing to trust or snapshot."""
        self.db.execute("UPDATE artists SET mbid_verified=0 WHERE id=1")
        self.db.commit()
        result = self._resolve(MusicBrainzFake())
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(fetch_aliases(self.db, 1), [])

    def test_unchanged_row_still_resolves(self):
        """The new snapshot must not break the path it protects."""
        result = self._resolve(MusicBrainzFake())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verification"], "trusted_mbid")
        self.assertIn("LaLa Hsu", fetch_aliases(self.db, 1))
        row = self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()
        self.assertEqual(row["mbid"], A)
        self.assertEqual(row["mbid_verified"], 1)


class SchemaProbeFailClosedTests(unittest.TestCase):
    """P2 follow-up: a probe that cannot answer must not disable the guard."""

    class _BrokenConn:
        def __init__(self, exc):
            self.exc = exc

        def execute(self, *args, **kwargs):
            raise self.exc

        def commit(self):
            pass

        def close(self):
            pass

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "library.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name,mbid,mbid_verified) "
                        "VALUES(1,'徐佳瑩','徐佳瑩',?,1)", (A,))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _stored(self):
        return self.db.execute(
            "SELECT mbid, mbid_verified FROM artists WHERE id=1"
        ).fetchone()

    def test_the_swallowing_schema_probe_is_gone(self):
        """A failed probe used to be indistinguishable from an old schema."""
        self.assertFalse(
            hasattr(enrichment, "_has_mbid_verified_column"),
            "the fail-open probe must not come back",
        )

    def test_transient_database_error_fails_closed(self):
        broken = self._BrokenConn(sqlite3.OperationalError("database is locked"))
        with patch.object(enrichment, "_db", lambda: broken):
            wrote = enrichment._set_unverified_artist_mbid(B, 1)
        self.assertFalse(wrote)
        self.assertEqual(self._stored()["mbid"], A)
        self.assertEqual(self._stored()["mbid_verified"], 1)

    def test_locked_database_fails_closed(self):
        broken = self._BrokenConn(sqlite3.OperationalError("cannot start a transaction"))
        with patch.object(enrichment, "_db", lambda: broken):
            wrote = enrichment._set_unverified_artist_mbid(B, 1)
        self.assertFalse(wrote)
        self.assertEqual(self._stored()["mbid"], A)

    def test_explicit_missing_column_still_falls_back(self):
        """A pre-schema-15 database has no flag to protect, so the write is safe."""
        legacy = Path(self._tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy)
        conn.execute("CREATE TABLE artists(id INTEGER PRIMARY KEY, name TEXT, "
                     "sort_name TEXT, mbid TEXT)")
        conn.execute("INSERT INTO artists(id,name,sort_name,mbid) "
                     "VALUES(1,'徐佳瑩','徐佳瑩',?)", (A,))
        conn.commit()
        conn.close()
        with patch.object(enrichment, "DB_PATH", legacy):
            self.assertTrue(enrichment._set_unverified_artist_mbid(B, 1))
        conn = sqlite3.connect(legacy)
        self.assertEqual(conn.execute("SELECT mbid FROM artists WHERE id=1").fetchone()[0], B)
        conn.close()


if __name__ == "__main__":
    unittest.main()