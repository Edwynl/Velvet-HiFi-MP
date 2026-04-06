import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import apply_migrations
from library_detail import (
    fetch_album_credits,
    fetch_composer_detail,
    fetch_credit_entity_detail,
    fetch_work_detail,
)


class LibraryDetailTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "library.db"
        with self._connect() as conn:
            apply_migrations(conn)
            self._seed(conn)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def _seed(self, conn: sqlite3.Connection):
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (1, 'Composer Alpha', 'Composer Alpha')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (2, 'Performer Beta', 'Performer Beta')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (3, 'Guest Artist', 'Guest Artist')")
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, label, is_favorite)
            VALUES (1, 2, 'Album One', 2001, 'Classical', 'Label X', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (
                id, album_id, artist_id, title, composer, work_title, performer,
                conductor, ensemble, track_number, duration, file_path
            ) VALUES
                (1, 1, 2, 'Movement I', 'Composer Alpha', 'Symphony A', 'Performer Beta',
                 'Conductor Gamma', 'Ensemble Delta', 1, 300, 'C:/music/one.flac'),
                (2, 1, 2, 'Movement II', 'Composer Alpha', 'Symphony A', 'Performer Beta; Guest Artist',
                 'Conductor Gamma', 'Ensemble Delta', 2, 320, 'C:/music/two.flac')
            """
        )
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 2)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (2, 2)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (2, 3)")
        conn.commit()

    def test_fetch_album_credits_aggregates_roles(self):
        with self._connect() as conn:
            result = fetch_album_credits(conn, 1)

        self.assertEqual(result["primary_artist"]["name"], "Performer Beta")
        self.assertEqual(result["composers"][0]["composer"], "Composer Alpha")
        self.assertEqual(result["performers"][0]["name"], "Performer Beta")
        self.assertEqual(result["conductors"][0]["name"], "Conductor Gamma")

    def test_fetch_work_detail_builds_versions(self):
        with self._connect() as conn:
            result = fetch_work_detail(conn, composer="Composer Alpha", title="Symphony A", limit=20)

        self.assertEqual(result["track_count"], 2)
        self.assertEqual(len(result["versions"]), 1)
        self.assertEqual(result["versions"][0]["conductor"], "Conductor Gamma")

    def test_fetch_credit_entity_detail_groups_related_works(self):
        with self._connect() as conn:
            result = fetch_credit_entity_detail(conn, role="performer", name="Guest Artist", limit=20)

        self.assertEqual(result["track_count"], 1)
        self.assertEqual(result["works"][0]["work_title"], "Symphony A")
        self.assertEqual(result["composers"][0]["name"], "Composer Alpha")

    def test_fetch_composer_detail_returns_work_summary(self):
        with self._connect() as conn:
            result = fetch_composer_detail(conn, composer="Composer Alpha", limit=20)

        self.assertEqual(result["track_count"], 2)
        self.assertEqual(result["works"][0]["work_title"], "Symphony A")


if __name__ == "__main__":
    unittest.main()
