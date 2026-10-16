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


class LibraryFixture(unittest.TestCase):
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

    def _scan_with(self, extract):
        return scan_music_dirs(
            self.db, [str(self.music)], audio_extensions={".flac"},
            scan_batch_size=10, extract_metadata=extract,
            find_folder_cover=lambda _: None, save_cover=lambda data, key: key,
            set_total=lambda n: None, on_progress=lambda n, f: None,
            on_track_added=lambda: None, on_track_skipped=lambda: None,
            on_track_updated=lambda: None,
            on_error=lambda m: self.counters["errors"].append(m),
        )

    def _track_credits(self):
        return {
            row["title"]: (row["artist"], row["album"])
            for row in self.db.execute(
                "SELECT t.title, ar.name AS artist, al.title AS album FROM tracks t "
                "JOIN artists ar ON ar.id=t.artist_id JOIN albums al ON al.id=t.album_id"
            )
        }


class IncrementalLibraryTests(LibraryFixture):
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
        # Artist changed, so the album is legitimately reassigned; history and
        # playlist links must still point to the original track ID.
        self.assertNotEqual(track["album_id"], album_id)
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

    def test_rolled_back_file_does_not_poison_artist_album_caches(self):
        """A failed file must not leave rolled-back artist/album IDs cached."""
        for name in ("01 broken", "02 same album", "03 other artist", "04 first artist"):
            (self.music / f"{name}.flac").write_text(name)
        artists = {"01": "Artist A", "02": "Artist A", "03": "Artist B", "04": "Artist A"}

        def extract(path):
            data = metadata(path, artist=artists[Path(path).name[:2]])
            if Path(path).name.startswith("01"):
                del data["duration"]  # fails inside the SAVEPOINT, after artist/album rows exist
            return data

        self._scan_with(extract)
        self.assertEqual(len(self.counters["errors"]), 1, self.counters["errors"])
        self.assertEqual(self._track_credits(), {
            "02 same album": ("Artist A", "Album"),
            "03 other artist": ("Artist B", "Album"),
            "04 first artist": ("Artist A", "Album"),
        })
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_metadata_update_does_not_poison_caches(self):
        """Same guarantee for the changed-file (UPDATE) path."""
        old = self.music / "01 old.flac"
        old.write_text("v1")
        self._scan_with(lambda path: metadata(path, artist="Original"))
        old.write_text("v2 changed bytes")
        st = old.stat()
        os.utime(old, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        (self.music / "02 new.flac").write_text("new")
        (self.music / "03 newer.flac").write_text("newer")

        def extract(path):
            name = Path(path).name
            if name.startswith("03"):
                return metadata(path, artist="Other")
            data = metadata(path, artist="Renamed")
            data["album"] = "Retagged"
            if name.startswith("01"):
                data["bitrate"] = object()  # unbindable: UPDATE fails after new artist/album rows
            return data

        self._scan_with(extract)
        self.assertEqual(len(self.counters["errors"]), 1, self.counters["errors"])
        credits = self._track_credits()
        self.assertEqual(credits["01 old"], ("Original", "Album"), "failed update rolled back")
        self.assertEqual(credits["02 new"], ("Renamed", "Retagged"))
        self.assertEqual(credits["03 newer"], ("Other", "Album"))
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migration_9_has_file_state_and_target_indexes(self):
        self.assertEqual(apply_migrations(self.db), LATEST_SCHEMA_VERSION)
        indexes = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_tracks_duplicate_size", indexes)
        self.assertIn("idx_history_recent_track", indexes)
        self.assertIn("idx_tracks_album_quality", indexes)


class DuplicateAudioTests(LibraryFixture):
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

    def _rewrite_keeping_stat(self, path, data):
        """Change content while keeping size and mtime (e.g. tag editor 'keep timestamp')."""
        st = path.stat()
        self.assertEqual(len(data), st.st_size)
        path.write_bytes(data)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns, st.st_mtime_ns)

    def test_stale_cached_hash_never_reports_false_duplicate(self):
        (self.music / "a.flac").write_bytes(b"same music")
        (self.music / "b.flac").write_bytes(b"same music")
        self._scan()
        self.assertEqual(find_exact_duplicates(self.db, [self.music])["groups"][0]["count"], 2)
        self._rewrite_keeping_stat(self.music / "b.flac", b"retagged!!")
        result = find_exact_duplicates(self.db, [self.music])
        self.assertEqual(result["groups"], [], "b.flac no longer matches a.flac byte-for-byte")

    def test_reported_groups_are_rehashed_from_current_bytes(self):
        for name in ("a", "b", "c"):
            (self.music / f"{name}.flac").write_bytes(b"same music")
        self._scan()
        find_exact_duplicates(self.db, [self.music])  # caches three equal hashes
        self._rewrite_keeping_stat(self.music / "c.flac", b"retagged!!")
        result = find_exact_duplicates(self.db, [self.music])
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual({Path(t["file_path"]).name for t in result["groups"][0]["tracks"]},
                         {"a.flac", "b.flac"})
        # The refreshed digest replaces the obsolete one in the cache.
        cached = {Path(r[0]).name: r[1] for r in self.db.execute(
            "SELECT t.file_path, s.sha256 FROM tracks t JOIN track_file_state s ON s.track_id=t.id")}
        self.assertNotEqual(cached["c.flac"], cached["a.flac"])

    def test_hash_cache_writes_do_not_block_other_writers(self):
        """Playback history and scans must stay writable while hashing runs."""
        for name in ("a", "b", "c"):
            (self.music / f"{name}.flac").write_bytes(b"same music")
        self._scan()
        track_id = self.db.execute("SELECT id FROM tracks ORDER BY id").fetchone()[0]
        other = sqlite3.connect(self.root / "music.db", timeout=0.2)
        writes = []

        def progress(done, total, name):
            if done > 1:  # at least one hash has been cached by now
                other.execute(
                    "INSERT INTO play_history(track_id,duration_played) VALUES (?,1)",
                    (track_id,),
                )
                other.commit()
                writes.append(done)

        try:
            result = find_exact_duplicates(self.db, [self.music], on_progress=progress)
        finally:
            other.close()
        self.assertEqual(result["groups"][0]["count"], 3)
        self.assertEqual(writes, [2, 3])
        self.assertFalse(self.db.in_transaction)

    def test_failed_duplicate_scan_leaves_no_open_transaction(self):
        for name in ("a", "b"):
            (self.music / f"{name}.flac").write_bytes(b"same music")
        self._scan()

        def progress(done, total, name):
            if done > 1:
                raise RuntimeError("cancelled")

        with self.assertRaises(RuntimeError):
            find_exact_duplicates(self.db, [self.music], on_progress=progress)
        self.assertFalse(self.db.in_transaction)

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
