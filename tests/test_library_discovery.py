import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import apply_migrations
from library_discovery import (
    fetch_album_radio,
    fetch_artist_detail_summary,
    fetch_artist_radio,
    fetch_discover,
    search_tracks,
)


class LibraryDiscoveryTests(unittest.TestCase):
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
        conn.execute("INSERT INTO artists (id, name, sort_name, bio) VALUES (1, 'Artist One', 'Artist One', 'Bio text')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (2, 'Artist Two', 'Artist Two')")
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, is_favorite)
            VALUES (1, 1, 'Album One', 2001, 'Classical', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, is_favorite)
            VALUES (2, 2, 'Album Two', 2003, 'Jazz', 0)
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (
                id, album_id, artist_id, title, composer, work_title, performer, conductor,
                ensemble, track_number, duration, sample_rate, bit_depth, file_path
            ) VALUES
                (1, 1, 1, 'Opening Work', 'Composer Alpha', 'Work A', 'Soloist One', 'Conductor One', 'Ensemble One', 1, 300, 96000, 24, 'C:/music/one.flac'),
                (2, 1, 1, 'Second Work', 'Composer Alpha', 'Work B', 'Soloist One', 'Conductor One', 'Ensemble One', 2, 280, 96000, 24, 'C:/music/two.flac'),
                (3, 2, 2, 'Night Jazz', 'Composer Beta', 'Work C', 'Soloist Two', '', '', 1, 260, 44100, 16, 'C:/music/three.flac')
            """
        )
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 1)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (2, 1)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (3, 2)")
        conn.execute("INSERT INTO play_history (track_id, duration_played) VALUES (1, 300)")
        conn.execute("INSERT INTO play_history (track_id, duration_played) VALUES (1, 300)")
        conn.execute("INSERT INTO play_history (track_id, duration_played) VALUES (3, 260)")
        conn.commit()

    def test_fetch_artist_detail_summary_returns_counts(self):
        with self._connect() as conn:
            result = fetch_artist_detail_summary(conn, 1)

        self.assertEqual(result["name"], "Artist One")
        self.assertEqual(result["album_count"], 1)
        self.assertEqual(result["track_count"], 2)
        self.assertEqual(result["play_count"], 2)

    def test_search_tracks_matches_credit_fields(self):
        with self._connect() as conn:
            result = search_tracks(
                conn,
                q="conductor one",
                limit=10,
                get_all_variants=lambda text: [text],
                to_simplified=lambda text: text,
                to_traditional=lambda text: text,
            )

        self.assertEqual(len(result["tracks"]), 2)
        self.assertEqual(result["tracks"][0]["artist_name"], "Artist One")

    def test_fetch_discover_returns_sections(self):
        with self._connect() as conn:
            result = fetch_discover(conn, 5)

        self.assertEqual(result["favorites"][0]["title"], "Album One")
        self.assertEqual(result["trending_artists"][0]["name"], "Artist One")
        self.assertGreaterEqual(len(result["recent_tracks"]), 1)

    def test_fetch_radio_builds_seeded_queue(self):
        with self._connect() as conn:
            album_radio = fetch_album_radio(conn, 1, 10)
            artist_radio = fetch_artist_radio(conn, 1, 10)

        self.assertEqual(album_radio["mode"], "album_radio")
        self.assertEqual(album_radio["seed"]["title"], "Album One")
        self.assertGreaterEqual(len(album_radio["tracks"]), 2)
        self.assertEqual(artist_radio["mode"], "artist_radio")
        self.assertEqual(artist_radio["seed"]["name"], "Artist One")


if __name__ == "__main__":
    unittest.main()
