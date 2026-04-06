import tempfile
import unittest
from pathlib import Path

from cover_art import find_folder_cover, save_cover_bytes


class CoverArtTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.album_dir = self.root / "album"
        self.album_dir.mkdir()
        self.track_path = self.album_dir / "track.flac"
        self.track_path.write_bytes(b"track")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_folder_cover_returns_matching_cover_bytes(self):
        cover_path = self.album_dir / "cover.jpg"
        cover_path.write_bytes(b"cover-data")

        result = find_folder_cover(str(self.track_path))

        self.assertEqual(result, b"cover-data")

    def test_save_cover_bytes_is_stable_for_same_key(self):
        first = save_cover_bytes(b"one", "album-key", self.root)
        second = save_cover_bytes(b"two", "album-key", self.root)

        self.assertEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), b"one")


if __name__ == "__main__":
    unittest.main()
