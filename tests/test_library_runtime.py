import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import apply_migrations
from library_runtime import (
    add_library_path,
    build_config_payload,
    fetch_libraries,
    record_track_play,
    remove_library_path,
    sync_music_dir_library,
)


class LibraryRuntimeTests(unittest.TestCase):
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
        conn.execute("PRAGMA foreign_keys=ON")
        return closing(conn)

    def _seed(self, conn: sqlite3.Connection):
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (1, 'Artist One', 'Artist One')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year) VALUES (1, 1, 'Album One', 2001)")
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path)
            VALUES (1, 1, 1, 'Track One', 240, 'C:/music/one.flac')
            """
        )
        conn.commit()

    def test_record_track_play_inserts_history_row(self):
        with self._connect() as conn:
            result = record_track_play(conn, 1, 180)
            row = conn.execute("SELECT track_id, duration_played FROM play_history").fetchone()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(row["track_id"], 1)
        self.assertEqual(row["duration_played"], 180)

    def test_build_config_payload_returns_existing_libraries(self):
        with self._connect() as conn:
            add_library_path(conn, "C:/Music/Main")
            payload = build_config_payload(conn, music_dir="C:/Music/Main", port=8765, data_dir=Path("velvet_data"))

        self.assertEqual(payload["music_dir"], "C:/Music/Main")
        self.assertEqual(payload["port"], 8765)
        self.assertEqual(payload["data_dir"], "velvet_data")
        self.assertEqual(len(payload["libraries"]), 1)
        self.assertEqual(payload["libraries"][0]["path"], "C:/Music/Main")

    def test_build_config_payload_seeds_legacy_music_dir_when_empty(self):
        with self._connect() as conn:
            payload = build_config_payload(conn, music_dir="C:/LegacyMusic", port=8765, data_dir="velvet_data")
            libraries = fetch_libraries(conn)

        self.assertEqual(len(payload["libraries"]), 1)
        self.assertEqual(payload["libraries"][0]["path"], "C:/LegacyMusic")
        self.assertEqual(libraries[0]["path"], "C:/LegacyMusic")

    def test_add_library_path_deduplicates_entries(self):
        with self._connect() as conn:
            add_library_path(conn, "C:/Music/Main")
            add_library_path(conn, "C:/Music/Main")
            libraries = fetch_libraries(conn)

        self.assertEqual(len(libraries), 1)

    def test_remove_library_path_deletes_library(self):
        with self._connect() as conn:
            add_library_path(conn, "C:/Music/Main")
            library = fetch_libraries(conn)[0]
            remove_library_path(conn, library["id"])
            libraries = fetch_libraries(conn)

        self.assertEqual(libraries, [])

    def test_sync_music_dir_library_adds_missing_path(self):
        with self._connect() as conn:
            sync_music_dir_library(conn, "C:/SyncedMusic")
            libraries = fetch_libraries(conn)

        self.assertEqual(len(libraries), 1)
        self.assertEqual(libraries[0]["path"], "C:/SyncedMusic")


if __name__ == "__main__":
    unittest.main()
