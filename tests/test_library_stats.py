import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import apply_migrations
from library_stats import (
    fetch_genre_albums,
    fetch_history,
    fetch_library_stats,
    fetch_listening_stats,
    fetch_most_played,
)


class LibraryStatsTests(unittest.TestCase):
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
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (1, 'Artist One', 'Artist One')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (2, 'Artist Two', 'Artist Two')")
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, year, genre, cover_path) VALUES (1, 1, 'Album One', 2001, 'Classical', NULL)"
        )
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, year, genre, cover_path) VALUES (2, 2, 'Album Two', 2002, 'Classical', 'C:/covers/two.jpg')"
        )
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, duration, format, file_path)
            VALUES
                (1, 1, 1, 'Track One', 300, 'FLAC', 'C:/music/one.flac'),
                (2, 2, 2, 'Track Two', 200, 'MP3', 'C:/music/two.mp3')
            """
        )
        conn.execute("INSERT INTO play_history (track_id, played_at, duration_played) VALUES (1, strftime('%s','now'), 300)")
        conn.execute("INSERT INTO play_history (track_id, played_at, duration_played) VALUES (1, strftime('%s','now') - 60, 300)")
        conn.execute("INSERT INTO play_history (track_id, played_at, duration_played) VALUES (2, strftime('%s','now') - 120, 200)")
        conn.commit()

    def test_fetch_library_stats_counts_entities(self):
        with self._connect() as conn:
            result = fetch_library_stats(conn, music_dir="C:/Music")

        self.assertEqual(result["artists"], 2)
        self.assertEqual(result["albums"], 2)
        self.assertEqual(result["tracks"], 2)
        self.assertEqual(result["albums_missing_cover"], 1)

    def test_fetch_genre_albums_returns_sorted_subset(self):
        with self._connect() as conn:
            result = fetch_genre_albums(conn, genre="Classical", limit=10, offset=0, sort="year")

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["albums"][0]["title"], "Album Two")

    def test_fetch_history_and_most_played_return_play_data(self):
        with self._connect() as conn:
            history = fetch_history(conn, limit=10)
            most_played = fetch_most_played(conn, limit=10)

        self.assertEqual(len(history), 3)
        self.assertEqual(most_played[0]["title"], "Track One")
        self.assertEqual(most_played[0]["play_count"], 2)

    def test_fetch_listening_stats_aggregates_totals(self):
        with self._connect() as conn:
            result = fetch_listening_stats(conn)

        self.assertEqual(result["total_plays"], 3)
        self.assertEqual(result["unique_artists_played"], 2)
        self.assertGreater(result["total_hours_listened"], 0)


if __name__ == "__main__":
    unittest.main()
