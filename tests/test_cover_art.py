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

    def test_disc_subfolder_uses_album_folder_cover(self):
        (self.album_dir / "folder.jpg").write_bytes(b"album-cover")
        disc_track = self.album_dir / "CD2" / "01.flac"
        disc_track.parent.mkdir()
        disc_track.write_bytes(b"track")
        self.assertEqual(find_folder_cover(str(disc_track)), b"album-cover")

    def test_artwork_subfolder_and_odd_names_prefer_front_cover(self):
        artwork = self.album_dir / "Artwork"
        artwork.mkdir()
        (artwork / "back.jpg").write_bytes(b"back")
        (artwork / "COVER.jpg").write_bytes(b"front")
        self.assertEqual(find_folder_cover(str(self.track_path)), b"front")

    def test_any_image_in_folder_skips_appledouble_files(self):
        other = self.root / "other"
        other.mkdir()
        track = other / "01.flac"
        track.write_bytes(b"track")
        (other / "._500x500.jpg").write_bytes(b"resource-fork")
        (other / "500x500.jpg").write_bytes(b"real")
        self.assertEqual(find_folder_cover(str(track)), b"real")


if __name__ == "__main__":
    unittest.main()
