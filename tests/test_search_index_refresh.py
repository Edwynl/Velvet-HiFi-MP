"""Bounded search-index refresh: a budget truncates and keeps the rest queued."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_schema import apply_migrations


class BoundedIndexRefreshTests(unittest.TestCase):
    """A bulk run must not end in one write transaction over the whole queue.

    `refresh_track_search_index` drains its queue completely when called
    without a budget, so the batch worker passes one. This proves the budget
    actually truncates, and that whatever stays dirty is still searchable.
    """

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tempdir.name) / "library.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def connection(self):
        db = sqlite3.connect(str(self.db_path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(db)
        db.commit()
        return db

    def _library(self, db, count):
        db.execute("INSERT INTO artists(id,name,sort_name) VALUES(1,'測試','測試')")
        db.executemany(
            "INSERT INTO albums(id,artist_id,title,genre) VALUES(?,1,'專輯','搖滾')",
            [(i,) for i in range(1, count + 1)],
        )
        db.executemany(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(?,?,1,?,?)",
            [(i, i, f"歌{i}", f"C:/m/{i}.flac") for i in range(1, count + 1)],
        )
        db.commit()

    def test_budget_truncates_and_leaves_remaining_rows_searchable(self):
        from library_search_index import refresh_track_search_index
        from library_search_match import match_track_ids

        db = self.connection()
        try:
            self._library(db, 40)
            # Full build so the index exists, then dirty everything again.
            while refresh_track_search_index(db, batch_size=500):
                pass
            db.execute("INSERT OR IGNORE INTO track_search_dirty(track_id) "
                       "SELECT id FROM tracks")
            db.commit()
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM track_search_dirty").fetchone()[0], 40)

            indexed = refresh_track_search_index(db, batch_size=10, max_rows=15)
            db.commit()
            # The budget is checked before a batch starts, so it indexes whole
            # batches only: at most max_rows + batch_size - 1 rows.
            self.assertGreaterEqual(indexed, 15)
            self.assertLessEqual(indexed, 24)
            remaining = db.execute(
                "SELECT COUNT(*) FROM track_search_dirty").fetchone()[0]
            self.assertEqual(remaining, 40 - indexed,
                             "unindexed rows stay queued, not lost")

            # The deferred rows are still findable through the live data path.
            self.assertEqual(len(match_track_ids(db, "歌17", reverse=False)), 1)

            # A later unbounded call finishes the job.
            while refresh_track_search_index(db, batch_size=10):
                pass
            db.commit()
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM track_search_dirty").fetchone()[0], 0)
            self.assertEqual(len(match_track_ids(db, "歌17", reverse=False)), 1)
        finally:
            db.close()

    def test_rejects_non_positive_budget(self):
        from library_search_index import refresh_track_search_index
        db = self.connection()
        try:
            self._library(db, 1)
            with self.assertRaises(ValueError):
                refresh_track_search_index(db, batch_size=10, max_rows=0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
