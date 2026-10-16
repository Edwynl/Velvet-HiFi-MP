"""Recoverable album archive regressions: never permanently delete audio."""
import json
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
            if str(src)==str(b):
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


if __name__ == "__main__":
    unittest.main()
