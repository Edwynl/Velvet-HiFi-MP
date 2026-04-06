import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import apply_migrations
from library_browse import fetch_albums, fetch_artists, fetch_tracks


class LibraryBrowseTests(unittest.TestCase):
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
        conn.execute(
            "INSERT INTO artists (id, name, sort_name) VALUES (1, 'Composer Alpha', 'Composer Alpha')"
        )
        conn.execute(
            "INSERT INTO artists (id, name, sort_name) VALUES (2, 'Performer Beta', 'Performer Beta')"
        )
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, is_favorite)
            VALUES (1, 1, 'Album One', 2001, 'Classical', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, is_favorite)
            VALUES (2, 2, 'Album Two', 2002, 'Jazz', 0)
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (
                id, album_id, artist_id, title, composer, work_title, performer,
                conductor, ensemble, track_number, file_path, sample_rate, bit_depth
            )
            VALUES (
                1, 1, 1, 'Opening Work', 'Composer Alpha', 'Symphony A', 'Performer Beta',
                'Conductor Gamma', 'Ensemble Delta', 1, 'C:/music/one.flac', 96000, 24
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (
                id, album_id, artist_id, title, composer, work_title, performer,
                conductor, ensemble, track_number, file_path, sample_rate, bit_depth
            )
            VALUES (
                2, 2, 2, 'Night Piece', 'Composer Alpha', 'Suite B', 'Performer Beta',
                '', '', 1, 'C:/music/two.flac', 44100, 16
            )
            """
        )
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 1)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 2)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (2, 2)")
        conn.commit()

    def test_fetch_artists_returns_counts(self):
        with self._connect() as conn:
            artists = fetch_artists(
                conn,
                search="",
                limit=10,
                offset=0,
                get_all_variants=lambda text: [text] if text else [""],
                to_simplified=lambda text: text,
                to_traditional=lambda text: text,
            )

        self.assertEqual(len(artists), 2)
        self.assertEqual(artists[0]["name"], "Composer Alpha")
        self.assertGreaterEqual(artists[0]["track_count"], 1)

    def test_fetch_albums_can_filter_by_year(self):
        with self._connect() as conn:
            albums = fetch_albums(
                conn,
                search="Album",
                limit=10,
                offset=0,
                sort="title",
                year=2001,
                get_all_variants=lambda text: [text] if text else [""],
                to_simplified=lambda text: text,
                to_traditional=lambda text: text,
            )

        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["title"], "Album One")

    def test_fetch_tracks_searches_credit_fields(self):
        with self._connect() as conn:
            result = fetch_tracks(
                conn,
                limit=10,
                offset=0,
                search="conductor gamma",
                sort="title",
                favorites_only=False,
            )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["tracks"][0]["title"], "Opening Work")


if __name__ == "__main__":
    unittest.main()
