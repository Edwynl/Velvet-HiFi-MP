import unittest

from path_compat import as_windows_extended_path


class WindowsPathCompatibilityTests(unittest.TestCase):
    def test_drive_path_gets_extended_prefix(self):
        path = r"E:\Tidal\Artist\Album\track.flac"
        self.assertEqual(as_windows_extended_path(path), r"\\?\E:\Tidal\Artist\Album\track.flac")

    def test_unc_path_gets_extended_unc_prefix(self):
        path = r"\\music-server\library\track.flac"
        self.assertEqual(
            as_windows_extended_path(path),
            r"\\?\UNC\music-server\library\track.flac",
        )

    def test_existing_extended_path_is_unchanged(self):
        path = r"\\?\E:\Tidal\track.flac"
        self.assertEqual(as_windows_extended_path(path), path)


if __name__ == "__main__":
    unittest.main()
