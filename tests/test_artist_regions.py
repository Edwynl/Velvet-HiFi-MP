import unittest
from artist_regions import _CURATED, _JP_NAMES, _NAMES, _NORTH_NAMES, _WEST_NAMES, _key, classify


class ArtistRegionTests(unittest.TestCase):
    def test_curated_aliases_override_foreign_release_imprints(self):
        self.assertEqual(classify(['BIGBANG (빅뱅)'], [('Universal Music Japan', '')]), 'korea')
        self.assertEqual(classify(['Koji Tamaki', '玉置浩二'], [('WM Taiwan', '')]), 'japan')
        self.assertEqual(classify(['Jay Chou'], [('Columbia', '')]), 'taiwan')

    def test_ambiguous_names_and_tied_evidence_stay_other(self):
        self.assertEqual(classify(['Unidentified Latin Name'], []), 'unknown')
        self.assertEqual(classify(['未知歌手'], []), 'unknown')
        self.assertEqual(classify(['Artist'], [('WM Taiwan', ''), ('WM China', '')]), 'unknown')

    def test_display_name_outranks_an_alias_shared_with_another_artist(self):
        """陳光榮 records as "Justin Lee", and so does a Western musician.
        The display name is the row's settled spelling, so it wins."""
        self.assertEqual(classify(['陳光榮', 'Justin Lee'], []), 'hongkong')
        # Without a curated display name, a genuine tie still stays Other.
        self.assertEqual(classify(['Someone Else', 'Justin Lee', '陳光榮'], []), 'unknown')

    def test_genres_and_regional_imprints(self):
        self.assertEqual(classify(['Band'], [('', 'Musiques du monde, Asie, Corée, K-pop')]), 'korea')
        self.assertEqual(classify(['Band'], [('Sony Music Labels Inc.', '')]), 'japan')
        self.assertEqual(classify(['Artist'], [('HIM International Music Inc.', '')]), 'taiwan')

    def test_asian_imprints_without_a_country_suffix(self):
        # Imprints used only by one market must resolve even though they carry
        # no country name; these carry the bulk of the library's Asian releases.
        self.assertEqual(classify(['Singer'], [('AsiaMuse Entertainment Co., Ltd.', '')]), 'taiwan')
        self.assertEqual(classify(['Singer'], [('East Asia Record Production Company Limited', '')]),
                         'hongkong')
        self.assertEqual(classify(['Singer'], [('Media Asia Music Limited', '')]), 'hongkong')
        self.assertEqual(classify(['Singer'], [('WOW Music Ltd.', '')]), 'hongkong')
        self.assertEqual(classify(['Singer'], [('THE FIRST TAKE MUSIC', '')]), 'japan')
        self.assertEqual(classify(['Singer'], [('Genie Music', '')]), 'korea')

    def test_western_imprints_do_not_claim_chinese_named_artists(self):
        # Karen Mok is Hong Kong: her Hong Kong imprint decides, and the two
        # Columbia/Epic releases must not drag her into International.
        self.assertEqual(
            classify(['莫文蔚', 'Karen Mok'],
                     [('MOK-A-BYE BABY MUSIC LTD', ''), ('MOK-A-BYE BABY MUSIC LTD', ''),
                      ('MOK-A-BYE BABY MUSIC LTD', ''), ('Columbia', ''), ('Epic', '')]),
            'hongkong')
        # With no Han character in any name the same imprint is a clean vote.
        self.assertEqual(classify(['Someone'], [('Verve', '')]), 'west')

    def test_country_suffixed_branches_are_not_collapsed(self):
        # A parent brand that carries the branch name must not drag the mainland
        # and Hong Kong arms of the same company into one region.
        self.assertEqual(classify(['Singer'], [('Gold Typhoon China', '')]), 'mainland')
        self.assertEqual(classify(['Singer'], [('Gold Typhoon Hong Kong', '')]), 'hongkong')
        self.assertEqual(classify(['Singer'], [('Gold Typhoon Taiwan', '')]), 'taiwan')

    def test_roster_covers_artists_whose_releases_point_elsewhere(self):
        # Release evidence for these is mainland Chinese imprints, but they are
        # Hong Kong / Taiwanese acts, so the roster has to win.
        self.assertEqual(classify(['黎瑞恩'], []), 'hongkong')
        self.assertEqual(classify(['林子祥'], []), 'hongkong')
        self.assertEqual(classify(['任賢齊', 'Richie Ren'], [('Gold Typhoon China', '')]), 'taiwan')
        self.assertEqual(classify(['魏如萱'], [('太合麦田', ''), ('北京智慧大狗文化傳媒有限公司', '')]),
                         'taiwan')
        self.assertEqual(classify(['Sukie S 石詠莉'], []), 'hongkong')

    def test_names_carrying_two_markets_stay_other(self):
        # A shared English alias must not be curated: it would make both artists
        # resolve to two regions, which classify() deliberately reports as Other.
        self.assertEqual(classify(['蘇慧倫', '苏慧伦'], []), 'taiwan')
        self.assertEqual(classify(['黃浩詩'], []), 'hongkong')

    def test_no_roster_entry_claims_two_regions(self):
        # _CURATED is built as a dict comprehension over an ordered list, so a
        # name listed in two regions silently resolves to whichever comes last.
        # That is how Connie Shih and Chintung Tse ended up classified as 'west'
        # despite being curated as Hong Kong acts. Catch it at the source.
        regions_by_key = {}
        for region, names in list(_NAMES.items()) + [('west', _WEST_NAMES),
                                                    ('korea', _NORTH_NAMES),
                                                    ('japan', _JP_NAMES)]:
            for raw in names.split('|'):
                key = _key(raw)
                if not key:
                    continue
                regions_by_key.setdefault(key, set()).add(region)

        collisions = {k: v for k, v in regions_by_key.items() if len(v) > 1}
        self.assertEqual(collisions, {}, f"roster names claimed by two regions: {collisions}")

    def test_roster_entries_are_clean_spellings(self):
        # A stray separator silently makes an entry unmatchable against the
        # library, e.g. '谢霆锋;' never equals '谢霆锋'.
        bad = sorted(_CURATED for _CURATED in _CURATED
                     if _CURATED != _CURATED.strip() or ';' in _CURATED)
        self.assertEqual(bad, [], f"malformed roster entries: {bad}")

