"""Incremental scan safety, exact duplicate detection and migration 9."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_schema import LATEST_SCHEMA_VERSION, apply_migrations
from library_duplicates import find_exact_duplicates
from scanner_service import scan_music_dirs


def metadata(file_path, artist="Artist", title=None):
    return {
        "album_artist": artist, "artist": artist, "album": "Album",
        "title": title or Path(file_path).stem, "year": 2026,
        "genre": "Jazz", "label": None, "cover_data": None,
        "composer": None, "work_title": None, "performer": None,
        "conductor": None, "ensemble": None,
        "track_number": 1, "disc_number": 1,
        "duration": 90, "format": "FLAC", "sample_rate": 96000,
        "bit_depth": 24, "channels": 2, "bitrate": 2000,
        "file_size": Path(file_path).stat().st_size,
    }


class IncrementalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.db = sqlite3.connect(self.root / "music.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.assertEqual(apply_migrations(self.db), LATEST_SCHEMA_VERSION)
        self.calls = []
        self.fail_extract = False
        self.counters = {"added": 0, "skipped": 0, "updated": 0, "errors": []}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _scan(self):
        def extract(path):
            self.calls.append(path)
            if self.fail_extract:
                raise ValueError("Bad metadata")
            return metadata(path, artist="New Artist" if "new" in Path(path).read_text() else "Artist")
        def increment(name):
            self.counters[name] += 1
            return {"tracks_added": self.counters["added"]}
        return scan_music_dirs(
            self.db, [str(self.music)], audio_extensions={".flac"},
            scan_batch_size=10, extract_metadata=extract,
            find_folder_cover=lambda _: None, save_cover=lambda data, key: key,
            set_total=lambda n: None, on_progress=lambda n, f: None,
            on_track_added=lambda: increment("added"),
            on_track_skipped=lambda: increment("skipped"),
            on_track_updated=lambda: increment("updated"),
            on_error=lambda m: self.counters["errors"].append(m),
        )

    def test_scan_detects_change_and_preserves_playlists_and_history(self):
        file_path = self.music / "one.flac"
        file_path.write_text("old")
        result1 = self._scan()
        track_id = self.db.execute("SELECT id FROM tracks").fetchone()[0]
        album_id = self.db.execute("SELECT album_id FROM tracks").fetchone()[0]
        self.db.execute("INSERT INTO playlists(name) VALUES ('Favourites')")
        self.db.execute("INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES (1,?,0)", (track_id,))
        self.db.execute("INSERT INTO play_history(track_id,duration_played) VALUES (?,90)", (track_id,))
        self.db.commit()
        self.assertEqual(result1["tracks_updated"], 0)
        self.assertEqual(len(self.calls), 1)
        self._scan()
        self.assertEqual(len(self.calls), 1, "Unchanged file is not decoded again")

        file_path.write_text("new metadata and longer file")
        # Ensure stat revision changes independent of filesystem timestamp precision.
        st = file_path.stat()
        os.utime(file_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        result = self._scan()
        self.assertEqual(result["tracks_updated"], 1)
        track = self.db.execute("SELECT id, album_id, file_size, artist_id FROM tracks").fetchone()
        self.assertEqual(track["id"], track_id)
        self.assertEqual(track["album_id"], album_id)
        self.assertEqual(track["file_size"], file_path.stat().st_size)
        self.assertEqual(self.db.execute("SELECT track_id FROM play_history").fetchone()[0], track_id)
        self.assertEqual(self.db.execute("SELECT track_id FROM playlist_tracks").fetchone()[0], track_id)
        name = self.db.execute("SELECT name FROM artists WHERE id=?", (track["artist_id"],)).fetchone()[0]
        self.assertEqual(name, "New Artist")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 1)

    def test_failed_refresh_rolls_back_and_retries(self):
        file_path = self.music / "change.flac"
        file_path.write_text("old")
        self._scan()
        track_id = self.db.execute("SELECT id FROM tracks").fetchone()[0]
        file_path.write_text("new bytes")
        self.fail_extract = True
        self._scan()
        self.assertTrue(any("Bad metadata" in msg for msg in self.counters["errors"]))
        self.assertEqual(self.db.execute("SELECT id, file_size FROM tracks").fetchone()["id"], track_id)
        self.fail_extract = False
        self._scan()
        self.assertEqual(self.db.execute("SELECT file_size FROM tracks").fetchone()[0], file_path.stat().st_size)

    def test_migration_9_has_file_state_and_target_indexes(self):
        self.assertEqual(apply_migrations(self.db), LATEST_SCHEMA_VERSION)
        indexes = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_tracks_duplicate_size", indexes)
        self.assertIn("idx_history_recent_track", indexes)
        self.assertIn("idx_tracks_album_quality", indexes)


class DuplicateAudioTests(IncrementalLibraryTests):
    def test_byte_identical_files_only_and_read_only(self):
        (self.music / "a.flac").write_bytes(b"same music")
        (self.music / "b.flac").write_bytes(b"same music")
        (self.music / "c.flac").write_bytes(b"other song")  # same size, not duplicate
        self._scan()
        count_before = self.db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        result = find_exact_duplicates(self.db, [self.music])
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["count"], 2)
        self.assertEqual(
            {Path(t["file_path"]).name for t in result["groups"][0]["tracks"]},
            {"a.flac", "b.flac"},
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], count_before)
        # Reuse full hashes for candidate files when stat snapshots match.
        cached = find_exact_duplicates(self.db, [self.music])
        self.assertGreaterEqual(cached["cached_hashes"], 2)
        # A file changed outside the scanner cannot reuse an obsolete hash.
        (self.music / "b.flac").write_bytes(b"new music!")
        stale = find_exact_duplicates(self.db, [self.music])
        self.assertEqual(stale["groups"], [])

    def test_never_reads_tracks_outside_configured_roots(self):
        (self.music / "a.flac").write_bytes(b"same")
        (self.music / "b.flac").write_bytes(b"same")
        self._scan()
        outside = self.root / "outside"
        outside.mkdir()
        result = find_exact_duplicates(self.db, [outside])
        self.assertEqual(result["groups"], [])


if __name__ == "__main__":
    unittest.main()
