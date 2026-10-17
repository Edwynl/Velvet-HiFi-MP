"""Recoverable album archive regressions: never permanently delete audio."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library_safety import AlbumFileArchive, ArchiveError, list_album_archives, restore_album_files


class AlbumSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.archive_dir = self.root / "data" / "album-trash"

    def tearDown(self):
        self.temp.cleanup()

    def _file(self, name="A.flac", content=b"24-bit hi-res bytes"):
        f=self.music / name
        f.write_bytes(content)
        return f

    def test_archive_is_recoverable_without_data_loss(self):
        a,b=self._file(),self._file("B.flac",b"other FLAC")
        archive=AlbumFileArchive([str(a),str(b)],[self.music],self.archive_dir)
        archive.stage()
        archive.commit()
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())
        self.assertEqual(list_album_archives(self.archive_dir)[0]["state"],"archived")
        result=restore_album_files(self.archive_dir,archive.token)
        self.assertEqual(result["restored"],2)
        self.assertEqual(a.read_bytes(),b"24-bit hi-res bytes")
        self.assertEqual(b.read_bytes(),b"other FLAC")

    def test_outside_library_is_rejected_before_any_move(self):
        inside=self._file()
        outside=self.root / "outside.flac"
        outside.write_bytes(b"outside")
        with self.assertRaises(ArchiveError):
            AlbumFileArchive([str(inside),str(outside)],[self.music],self.archive_dir)
        self.assertTrue(inside.exists())
        self.assertTrue(outside.exists())

    def test_midstage_rename_failure_restores_original_files(self):
        a,b=self._file(),self._file("B.flac")
        archive=AlbumFileArchive([str(a),str(b)],[self.music],self.archive_dir)
        import library_safety
        original_replace=library_safety.os.replace
        def maybe_fail(src,dst):
            if str(src).lower().endswith("b.flac"):  # Windows may add \\?\\ prefix
                raise OSError("simulated file lock")
            return original_replace(src,dst)
        with patch.object(library_safety.os,"replace",side_effect=maybe_fail):
            with self.assertRaises(ArchiveError):
                archive.stage()
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())
        self.assertEqual(json.loads(archive.manifest_path.read_text())["state"],"restored")

    def test_restore_never_overwrites_new_file_at_original_path(self):
        a=self._file()
        archive=AlbumFileArchive([str(a)],[self.music],self.archive_dir)
        archive.stage()
        archive.commit()
        a.write_bytes(b"new file")
        with self.assertRaises(ArchiveError):
            restore_album_files(self.archive_dir,archive.token)
        self.assertEqual(a.read_bytes(),b"new file")
        self.assertTrue(Path(archive.entries[0]["staged"]).exists())

    def test_missing_staged_and_original_requires_recovery_for_either_moved_flag(self):
        for moved in (True, False):
            with self.subTest(moved=moved):
                audio = self._file()
                archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
                archive.stage()
                archive.commit()
                Path(archive.entries[0]["staged"]).unlink()
                archive.entries[0]["moved"] = moved
                archive._write_manifest()
                with self.assertRaisesRegex(ArchiveError, "missing"):
                    restore_album_files(self.archive_dir, archive.token)
                manifest = json.loads(archive.manifest_path.read_text())
                self.assertEqual(manifest["state"], "recovery_required")
                self.assertEqual(manifest["files"][0]["moved"], moved)
                self.assertIn(archive.token, [row["token"] for row in list_album_archives(self.archive_dir)])

    def test_missing_archive_with_unverified_original_does_not_claim_success(self):
        audio = self._file()
        archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
        archive.stage()
        Path(archive.entries[0]["staged"]).unlink()
        audio.write_bytes(b"unrelated replacement")
        with self.assertRaisesRegex(ArchiveError, "cannot be verified"):
            restore_album_files(self.archive_dir, archive.token)
        self.assertEqual(audio.read_bytes(), b"unrelated replacement")
        self.assertEqual(json.loads(archive.manifest_path.read_text())["state"], "recovery_required")

    def test_partial_restore_can_be_retried_without_losing_successful_entries(self):
        a, b = self._file(), self._file("B.flac", b"second FLAC")
        archive = AlbumFileArchive([str(a), str(b)], [self.music], self.archive_dir)
        archive.stage()
        archive.commit()
        staged = Path(archive.entries[0]["staged"])
        saved_bytes = staged.read_bytes()
        staged.unlink()
        with self.assertRaises(ArchiveError):
            restore_album_files(self.archive_dir, archive.token)
        manifest = json.loads(archive.manifest_path.read_text())
        self.assertEqual(manifest["state"], "recovery_required")
        self.assertTrue(manifest["files"][0]["moved"])
        self.assertFalse(manifest["files"][1]["moved"])
        self.assertEqual(b.read_bytes(), b"second FLAC")
        staged.write_bytes(saved_bytes)
        for _ in range(2):
            result = restore_album_files(self.archive_dir, archive.token)
            self.assertEqual(result["restored"], 2)
            self.assertFalse(result["track_ids_preserved"])
            self.assertFalse(result["index_restored"])
        self.assertEqual(a.read_bytes(), saved_bytes)
        self.assertEqual(b.read_bytes(), b"second FLAC")
        self.assertEqual(list_album_archives(self.archive_dir), [])

    def test_unmoved_pending_entry_is_safe_only_while_original_exists(self):
        audio = self._file()
        archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
        archive._write_manifest()
        self.assertEqual(restore_album_files(self.archive_dir, archive.token)["restored"], 1)
        audio.unlink()
        with self.assertRaisesRegex(ArchiveError, "missing"):
            restore_album_files(self.archive_dir, archive.token)

    def test_non_file_staged_path_requires_recovery(self):
        audio = self._file()
        archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
        archive._write_manifest()
        Path(archive.entries[0]["staged"]).mkdir()
        with self.assertRaisesRegex(ArchiveError, "not an audio file"):
            restore_album_files(self.archive_dir, archive.token)
        self.assertEqual(audio.read_bytes(), b"24-bit hi-res bytes")

    def test_locked_restore_retains_archive_and_can_be_retried(self):
        audio = self._file()
        archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
        archive.stage()
        archive.commit()
        import library_safety
        real_replace = library_safety.os.replace

        def locked(src, dst):
            if str(src) == library_safety.filesystem_path(archive.entries[0]["staged"]):
                raise PermissionError("simulated Windows file lock")
            return real_replace(src, dst)

        with patch.object(library_safety.os, "replace", side_effect=locked):
            with self.assertRaisesRegex(ArchiveError, "file lock"):
                restore_album_files(self.archive_dir, archive.token)
        self.assertFalse(audio.exists())
        self.assertTrue(Path(archive.entries[0]["staged"]).is_file())
        self.assertEqual(json.loads(archive.manifest_path.read_text())["state"], "recovery_required")
        self.assertEqual(restore_album_files(self.archive_dir, archive.token)["restored"], 1)
        self.assertEqual(audio.read_bytes(), b"24-bit hi-res bytes")

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics required")
    def test_actual_windows_open_handle_blocks_restore_without_losing_audio(self):
        audio = self._file()
        archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
        archive.stage()
        archive.commit()
        staged = Path(archive.entries[0]["staged"])
        with staged.open("rb") as locked:
            with self.assertRaises(ArchiveError):
                restore_album_files(self.archive_dir, archive.token)
            self.assertEqual(locked.read(), b"24-bit hi-res bytes")
            self.assertFalse(audio.exists())
            self.assertEqual(json.loads(archive.manifest_path.read_text())["state"], "recovery_required")
        self.assertEqual(restore_album_files(self.archive_dir, archive.token)["restored"], 1)
        self.assertEqual(audio.read_bytes(), b"24-bit hi-res bytes")

    def test_restore_validates_entire_manifest_before_moving_any_file(self):
        audio = self._file()
        archive = AlbumFileArchive([str(audio)], [self.music], self.archive_dir)
        archive.stage()
        body = json.loads(archive.manifest_path.read_text())
        for damaged in ([], None, {}, {**body, "files": []},
                        {**body, "token": "0" * 32}, {**body, "state": "invalid"},
                        {**body, "files": [body["files"][0], {"moved": False}]},
                        {**body, "files": [{**body["files"][0], "moved": "false"}]}):
            with self.subTest(damaged=damaged):
                archive.manifest_path.write_text(json.dumps(damaged), encoding="utf-8")
                with self.assertRaises(ArchiveError):
                    restore_album_files(self.archive_dir, archive.token)
                self.assertFalse(audio.exists())
                self.assertTrue(Path(archive.entries[0]["staged"]).is_file())


if __name__ == "__main__":
    unittest.main()
