import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

from db_schema import apply_migrations
from maintenance_service import (
    enrich_missing_album_covers,
    refresh_album_from_files,
    refresh_artist_from_files,
    rescan_local_album_covers,
)


class FakeResponse:
    def __init__(self, *, json_data=None, status_code=200, content=b""):
        self._json_data = json_data or {}
        self.status_code = status_code
        self.content = content

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self):
        self.closed = False

    async def get(self, url, **kwargs):
        if "musicbrainz.org" in url:
            return FakeResponse(json_data={"releases": [{"id": "mbid-123"}]})
        if "coverartarchive.org" in url:
            return FakeResponse(status_code=200, content=b"cover-bytes")
        raise AssertionError(f"Unexpected URL {url}")

    async def aclose(self):
        self.closed = True


class MaintenanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "library.db"
        self.music_dir = self.root / "music"
        self.music_dir.mkdir()
        self.file_one = self.music_dir / "one.flac"
        self.file_two = self.music_dir / "two.flac"
        self.file_three = self.music_dir / "three.flac"
        self.file_one.write_bytes(b"one")
        self.file_two.write_bytes(b"two")
        self.file_three.write_bytes(b"three")
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
        conn.execute("INSERT INTO albums (id, artist_id, title, year, cover_path, mbid) VALUES (1, 1, 'Album One', 2001, NULL, NULL)")
        conn.execute("INSERT INTO albums (id, artist_id, title, year, cover_path, mbid) VALUES (2, 1, 'Album Two', 2002, NULL, NULL)")
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path, track_number, disc_number)
            VALUES
                (1, 1, 1, 'Old One', 100, ?, 1, 1),
                (2, 1, 1, 'Old Two', 110, ?, 2, 1),
                (3, 2, 1, 'Artist Track', 120, ?, 1, 1)
            """,
            (str(self.file_one), str(self.file_two), str(self.file_three)),
        )
        conn.commit()

    def test_refresh_album_from_files_updates_tracks_and_cover(self):
        with self._connect() as conn:
            result = refresh_album_from_files(
                conn,
                1,
                extract_metadata=lambda path: {
                    "title": f"Updated {Path(path).stem}",
                    "composer": "Composer A",
                    "work_title": "Work A",
                    "performer": "Performer A",
                    "conductor": "Conductor A",
                    "ensemble": "Ensemble A",
                    "track_number": 9 if path == str(self.file_one) else 10,
                    "disc_number": 2,
                    "duration": 321,
                    "sample_rate": 96000,
                    "channels": 2,
                    "bit_depth": 24,
                    "cover_data": b"cover",
                },
                write_album_cover=lambda album_id, data: f"C:/covers/album_{album_id}.jpg",
            )
            tracks = conn.execute("SELECT title, track_number FROM tracks WHERE album_id=1 ORDER BY id").fetchall()
            album = conn.execute("SELECT cover_path FROM albums WHERE id=1").fetchone()

        self.assertEqual(result["message"], "Refreshed 2 tracks")
        self.assertEqual([row["title"] for row in tracks], ["Updated one", "Updated two"])
        self.assertEqual(album["cover_path"], "C:/covers/album_1.jpg")

    def test_refresh_album_from_files_raises_for_missing_album(self):
        with self._connect() as conn:
            with self.assertRaises(HTTPException) as ctx:
                refresh_album_from_files(conn, 999, extract_metadata=lambda _: {}, write_album_cover=lambda *_: "")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_refresh_artist_from_files_updates_artist_name_and_tracks(self):
        with self._connect() as conn:
            result = refresh_artist_from_files(
                conn,
                1,
                extract_metadata=lambda path: {
                    "artist": "Renamed Artist",
                    "title": f"Artist {Path(path).stem}",
                    "composer": "Composer B",
                    "work_title": "Work B",
                    "performer": "Performer B",
                    "conductor": "Conductor B",
                    "ensemble": "Ensemble B",
                    "track_number": 5,
                    "disc_number": 1,
                },
            )
            artist = conn.execute("SELECT name FROM artists WHERE id=1").fetchone()
            track = conn.execute("SELECT title FROM tracks WHERE id=3").fetchone()

        self.assertEqual(result["artist_name_changed"], "Renamed Artist")
        self.assertEqual(artist["name"], "Renamed Artist")
        self.assertEqual(track["title"], "Artist three")

    def test_rescan_local_album_covers_updates_missing_cover_paths(self):
        cover_file = self.music_dir / "cover.jpg"
        cover_file.write_bytes(b"folder-cover")

        with self._connect() as conn:
            result = rescan_local_album_covers(
                conn,
                save_cover=lambda data, key: f"C:/covers/{key}.jpg",
            )
            albums = conn.execute("SELECT cover_path FROM albums WHERE id IN (1,2) ORDER BY id").fetchall()

        self.assertEqual(result["updated"], 2)
        self.assertEqual(albums[0]["cover_path"], "C:/covers/album_1.jpg")
        self.assertEqual(albums[1]["cover_path"], "C:/covers/album_2.jpg")

    def test_enrich_missing_album_covers_updates_mbid_and_cover_path(self):
        def get_db_factory():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

        client = FakeAsyncClient()

        async def no_sleep(_: float):
            return None

        result = asyncio.run(
            enrich_missing_album_covers(
                get_db=get_db_factory,
                http_client_factory=lambda: client,
                save_cover=lambda data, key: f"C:/covers/{key}.jpg",
                sleep_fn=no_sleep,
                logger=None,
            )
        )

        with self._connect() as conn:
            album = conn.execute("SELECT mbid, cover_path FROM albums WHERE id=1").fetchone()

        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(album["mbid"], "mbid-123")
        self.assertEqual(album["cover_path"], "C:/covers/mb_mbid-123.jpg")
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
