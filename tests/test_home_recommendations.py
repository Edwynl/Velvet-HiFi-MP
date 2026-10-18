import sqlite3
import unittest

from db_schema import apply_migrations
from home_recommendations import fetch_home_shelves, parse_home_ids
from library_discovery import fetch_recently_added


class HomeRecommendationsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        apply_migrations(self.db)
        # English-titled Asian artists must stay in the main shelf.
        artists = [(1, '陳奕迅'), (2, '周杰倫'), (3, '宇多田ヒカル'),
                   (4, 'Adele'), (5, 'Taylor Swift'), (6, 'Bach')]
        self.db.executemany('INSERT INTO artists(id, name) VALUES (?, ?)', artists)
        for album_id in range(1, 49):
            artist_id = (album_id - 1) % 6 + 1
            genre = {1: 'Cantopop', 2: 'Mandopop', 3: 'J-Pop', 4: 'Pop', 5: 'Pop', 6: 'Classical'}[artist_id]
            self.db.execute('INSERT INTO albums(id, artist_id, title, genre, created_at, year) VALUES (?, ?, ?, ?, ?, ?)',
                            (album_id, artist_id, f'Album {album_id}', genre, album_id, 2020))
            self.db.execute('INSERT INTO tracks(id, album_id, artist_id, title, file_path, sample_rate, bit_depth) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (album_id, album_id, artist_id, f'Track {album_id}', f'{album_id}.flac', 96000, 24))
        self.db.executemany('INSERT INTO play_history(track_id) VALUES (?)', [(1,)] * 5 + [(4,)] * 3 + [(10,)] * 2)

    def tearDown(self):
        self.db.close()

    def test_local_shelves_limits_order_and_deduplication(self):
        result = fetch_home_shelves(self.db, [25, 1, 999999], [3, 2])
        self.assertEqual([a['id'] for a in result['recently_opened']], [25, 1])
        self.assertEqual([a['id'] for a in result['frequent_artists'][:2]], [3, 2])
        self.assertLessEqual(len(result['frequent_artists']), 8)
        self.assertLessEqual(len(result['for_you']), 10)
        self.assertEqual(len(result['global_picks']), 2)
        self.assertTrue(all(a['artist_id'] in [1, 2, 3] for a in result['for_you']))
        self.assertTrue(all(a['artist_id'] in [4, 5] for a in result['global_picks']))
        selected = result['for_you'] + result['global_picks']
        ids = [a['id'] for a in selected]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(set(ids).isdisjoint(set(range(25, 49)) | {25, 1}))
        for artist in {a['artist_id'] for a in selected}:
            self.assertLessEqual(sum(a['artist_id'] == artist for a in selected), 2)
        self.assertNotIn('recent_tracks', result)
        self.assertTrue(all(a['is_hires'] for a in result['recently_opened']))

    def test_click_and_listening_affinity_influence_order(self):
        result = fetch_home_shelves(self.db, [2], [2])
        self.assertEqual(result['for_you'][0]['artist_id'], 2)
        self.assertEqual(result['for_you'][0]['recommendation_reason'], 'From an artist you return to')
        self.assertEqual(result['global_picks'][0]['id'], 4, 'Library listening popularity ranks global picks')

    def test_no_fabricated_visits_or_external_picks(self):
        result = fetch_home_shelves(self.db, [], [])
        self.assertEqual(result['recently_opened'], [])
        self.assertEqual({a['id'] for a in result['frequent_artists']}, {1, 4})
        self.db.execute('DELETE FROM play_history')
        self.db.execute('DELETE FROM tracks')
        self.db.execute('DELETE FROM albums')
        self.assertEqual(fetch_home_shelves(self.db, [1], [1]),
                         {'recently_opened': [], 'frequent_artists': [], 'for_you': [], 'global_picks': []})

    def test_artist_shelf_replaces_compilation_credits_with_real_artists(self):
        for credit in ['Various Artists', '  VARIOUS   ARTISTS  ', '群星', 'V.A.', 'Unknown Artist']:
            with self.subTest(credit=credit):
                self.db.execute('UPDATE artists SET name = ? WHERE id = 3', (credit,))
                self.db.execute('INSERT INTO play_history(track_id) VALUES (3)')
                result = fetch_home_shelves(self.db, [3], [3, 2])
                self.assertEqual([a['id'] for a in result['frequent_artists']], [2, 4, 1])
                self.assertEqual(result['recently_opened'][0]['id'], 3,
                                 'Compilation albums remain accessible in album shelves')

    def test_id_parsing_is_bounded_and_ignores_invalid_values(self):
        self.assertEqual(parse_home_ids('3,2,3,-1,no,0,999999999999999999999'), [3, 2])
        self.assertEqual(len(parse_home_ids(','.join(str(i) for i in range(1, 90)))), 32)
        self.assertEqual(parse_home_ids('1,2,3', 2), [1, 2])

    def test_recently_added_limits_before_track_join_and_breaks_ties(self):
        self.db.execute('UPDATE albums SET created_at = 100')
        recent = fetch_recently_added(self.db, 24)
        self.assertEqual([a['id'] for a in recent], list(range(48, 24, -1)))
        self.assertTrue(all(a['is_hires'] for a in recent))


if __name__ == '__main__':
    unittest.main()
