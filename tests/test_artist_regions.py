import unittest
from artist_regions import classify


class ArtistRegionTests(unittest.TestCase):
    def test_curated_aliases_override_foreign_release_imprints(self):
        self.assertEqual(classify(['BIGBANG (빅뱅)'], [('Universal Music Japan', '')]), 'korea')
        self.assertEqual(classify(['Koji Tamaki', '玉置浩二'], [('WM Taiwan', '')]), 'japan')
        self.assertEqual(classify(['Jay Chou'], [('Columbia', '')]), 'taiwan')

    def test_ambiguous_names_and_tied_evidence_stay_other(self):
        self.assertEqual(classify(['Unidentified Latin Name'], []), 'unknown')
        self.assertEqual(classify(['未知歌手'], []), 'unknown')
        self.assertEqual(classify(['Artist'], [('WM Taiwan', ''), ('WM China', '')]), 'unknown')

    def test_genres_and_regional_imprints(self):
        self.assertEqual(classify(['Band'], [('', 'Musiques du monde, Asie, Corée, K-pop')]), 'korea')
        self.assertEqual(classify(['Band'], [('Sony Music Labels Inc.', '')]), 'japan')
        self.assertEqual(classify(['Artist'], [('HIM International Music Inc.', '')]), 'taiwan')
