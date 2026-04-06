import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import apply_migrations
from scanner_service import discover_audio_files, refresh_folder_records, scan_music_dirs, sort_name, split_artists


class ScannerServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "library.db"
        self.music_dir = self.root / "music"
        self.music_dir.mkdir()
        self.file_one = self.music_dir / "one.flac"
        self.file_two = self.music_dir / "two.flac"
        self.hidden_file = self.music_dir / "._ignore.flac"
        self.file_one.write_bytes(b"one")
        self.file_two.write_bytes(b"two")
        self.hidden_file.write_bytes(b"hidden")
        with self._connect() as conn:
            apply_migrations(conn)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return closing(conn)

    def test_sort_name_and_split_artists_handle_common_cases(self):
        self.assertEqual(sort_name("The Beatles"), "Beatles, The")
        self.assertEqual(split_artists("Alice & Bob feat. Carol"), ["Alice", "Bob", "Carol"])

    def test_discover_audio_files_skips_appledouble_files(self):
        files = discover_audio_files([str(self.music_dir)], {".flac"})
        self.assertEqual(sorted(Path(path).name for path in files), ["one.flac", "two.flac"])

    def test_scan_music_dirs_inserts_tracks_albums_and_artist_links(self):
        progress_events: list[tuple[int, str]] = []
        state = {"tracks_added": 0, "tracks_skipped": 0, "total": 0, "errors": []}

        metadata_map = {
            str(self.file_one): {
                "album_artist": "Artist One & Guest One",
                "artist": "Artist One & Guest One",
                "album": "Album One",
                "title": "Track One",
                "year": 2001,
                "genre": "Classical",
                "label": "Label A",
                "cover_data": None,
                "composer": "Composer A",
                "work_title": "Work A",
                "performer": "Artist One",
                "conductor": None,
                "ensemble": None,
                "track_number": 1,
                "disc_number": 1,
                "duration": 180,
                "format": "FLAC",
                "sample_rate": 96000,
                "bit_depth": 24,
                "channels": 2,
                "bitrate": 3000,
                "file_size": 3,
            },
            str(self.file_two): {
                "album_artist": "Artist Two",
                "artist": "Artist Two",
                "album": "Album Two",
                "title": "Track Two",
                "year": 2002,
                "genre": "Jazz",
                "label": "Label B",
                "cover_data": b"cover",
                "composer": None,
                "work_title": None,
                "performer": None,
                "conductor": None,
                "ensemble": None,
                "track_number": 1,
                "disc_number": 1,
                "duration": 220,
                "format": "FLAC",
                "sample_rate": 44100,
                "bit_depth": 16,
                "channels": 2,
                "bitrate": 1600,
                "file_size": 3,
            },
        }

        def extract_metadata(path: str) -> dict:
            return metadata_map[path]

        def on_track_added():
            state["tracks_added"] += 1
            return {"tracks_added": state["tracks_added"]}

        with self._connect() as conn:
            result = scan_music_dirs(
                conn,
                [str(self.music_dir)],
                audio_extensions={".flac"},
                scan_batch_size=10,
                extract_metadata=extract_metadata,
                find_folder_cover=lambda _: None,
                save_cover=lambda data, key: f"C:/covers/{key}.jpg",
                set_total=lambda total: state.__setitem__("total", total),
                on_progress=lambda index, name: progress_events.append((index, name)),
                on_track_added=on_track_added,
                on_track_skipped=lambda: state.__setitem__("tracks_skipped", state["tracks_skipped"] + 1),
                on_error=lambda message: state["errors"].append(message),
            )

            artists = conn.execute("SELECT name FROM artists ORDER BY name").fetchall()
            albums = conn.execute("SELECT title, cover_path, total_tracks FROM albums ORDER BY title").fetchall()
            tracks = conn.execute("SELECT title FROM tracks ORDER BY title").fetchall()
            track_artists = conn.execute("SELECT COUNT(*) FROM track_artists").fetchone()[0]

        self.assertEqual(result["discovered_files"], 2)
        self.assertEqual(state["total"], 2)
        self.assertEqual(state["tracks_added"], 2)
        self.assertEqual(state["tracks_skipped"], 0)
        self.assertEqual(state["errors"], [])
        self.assertEqual(progress_events, [(1, "one.flac"), (2, "two.flac")])
        self.assertEqual([row["name"] for row in artists], ["Artist One", "Artist Two", "Guest One"])
        self.assertEqual([row["title"] for row in tracks], ["Track One", "Track Two"])
        self.assertEqual(albums[0]["total_tracks"], 1)
        self.assertEqual(albums[1]["cover_path"], "C:/covers/3_Album Two.jpg")
        self.assertEqual(track_artists, 3)

    def test_refresh_folder_records_rebuilds_tracks_and_updates_missing_cover(self):
        state = {"tracks_added": 0, "total": 0, "errors": []}

        with self._connect() as conn:
            conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (1, 'Artist One', 'Artist One')")
            conn.execute(
                "INSERT INTO albums (id, artist_id, title, year, cover_path) VALUES (1, 1, 'Album One', 2001, NULL)"
            )
            conn.execute(
                """
                INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path)
                VALUES (1, 1, 1, 'Old Track', 100, ?)
                """,
                (str(self.file_one),),
            )
            conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 1)")
            conn.commit()

            result = refresh_folder_records(
                conn,
                str(self.music_dir),
                audio_extensions={".flac"},
                extract_metadata=lambda path: {
                    "album_artist": "Artist One",
                    "artist": "Artist One",
                    "album": "Album One",
                    "title": f"Refreshed {Path(path).stem}",
                    "year": 2001,
                    "genre": "Classical",
                    "label": "Label A",
                    "cover_data": b"cover-data",
                    "composer": None,
                    "work_title": None,
                    "performer": None,
                    "conductor": None,
                    "ensemble": None,
                    "track_number": 1 if path == str(self.file_one) else 2,
                    "disc_number": 1,
                    "duration": 200,
                    "format": "FLAC",
                    "sample_rate": 48000,
                    "bit_depth": 24,
                    "channels": 2,
                    "bitrate": 2000,
                    "file_size": 3,
                },
                find_folder_cover=lambda _: None,
                save_cover=lambda data, key: f"C:/covers/{key}.jpg",
                set_total=lambda total: state.__setitem__("total", total),
                on_progress=lambda index, name: None,
                on_track_added=lambda: state.__setitem__("tracks_added", state["tracks_added"] + 1),
                on_error=lambda message: state["errors"].append(message),
            )

            tracks = conn.execute("SELECT title FROM tracks ORDER BY title").fetchall()
            album = conn.execute("SELECT cover_path FROM albums WHERE id=1").fetchone()

        self.assertEqual(result["deleted_tracks"], 1)
        self.assertEqual(result["discovered_files"], 2)
        self.assertEqual(state["total"], 2)
        self.assertEqual(state["tracks_added"], 2)
        self.assertEqual(state["errors"], [])
        self.assertEqual([row["title"] for row in tracks], ["Refreshed one", "Refreshed two"])
        self.assertEqual(album["cover_path"], "C:/covers/1_Album One.jpg")


if __name__ == "__main__":
    unittest.main()
