"""Album paging must be bounded before Python rows reach a mobile client."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_schema import apply_migrations
from library_paging import album_catalog_filters, fetch_album_page, fetch_artist_page, album_genre_group


class AlbumPagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(Path(self.tmp.name) / "paging.db")
        self.db.row_factory = sqlite3.Row
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists (id,name,sort_name) VALUES(1,'Artist','Artist')")
        for id in range(1, 152):
            title = f"Album {id:03d}"
            genre = "Jazz" if id % 2 == 0 else "Classical"
            year = 1995 if id % 2 == 0 else 2024
            self.db.execute(
                "INSERT INTO albums (id,artist_id,title,genre,year,is_favorite) VALUES(?,?,?,?,?,?)",
                (id,1,title,genre,year,1 if id == 151 else 0),
            )
        self.db.execute(
            "INSERT INTO tracks(album_id,artist_id,title,file_path,sample_rate,bit_depth) "
            "VALUES(1,1,'Track','C:/Music/track.flac',96000,24)"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def page(self, **kwargs):
        return fetch_album_page(self.db, **kwargs)

    def test_three_pages_have_no_missing_or_duplicate_albums(self):
        results = [self.page(limit=60,offset=n) for n in (0,60,120)]
        self.assertEqual([len(p["albums"]) for p in results],[60,60,31])
        self.assertEqual({p["total"] for p in results},{151})
        ids = [a["id"] for p in results for a in p["albums"]]
        self.assertEqual(len(set(ids)),151)
        self.assertEqual(ids,list(range(1,152)))

    def test_server_filters_before_paging_and_preserves_hires(self):
        response = self.page(limit=10, offset=0,genre="Classical",decade="2020s")
        self.assertEqual(response["total"],76)
        self.assertEqual(len(response["albums"]),10)
        self.assertEqual(response["albums"][0]["is_hires"],1)
        favorites = self.page(favorites_only=True)
        self.assertEqual([a["id"] for a in favorites["albums"]],[151])
        self.assertIn("1990s", album_catalog_filters(self.db)["decades"])

    def test_genre_filter_matches_the_trimmed_label_it_offers(self):
        self.db.execute("UPDATE albums SET genre=' Soundtrack ' WHERE id=5")
        self.db.commit()
        self.assertIn("Soundtracks", album_catalog_filters(self.db)["genres"])
        result = self.page(genre="Soundtrack")
        self.assertEqual([a["id"] for a in result["albums"]], [5])

    def test_genre_groups_cover_subgenres_and_missing_tags_before_paging(self):
        labels = ['Musiques du monde, Asie', 'Musiques du monde, Asie, Japon, J-pop',
                  '国语流行', 'Pop, Rock', 'Alternatif et Indé', 'Jazz, Jazz vocal',
                  'Classique', 'Soul, Funk, R&B', 'Électronique, Dance',
                  'Film, Anime, Jeux vidéo', 'Blues, Country, Folk', None, 'Divers']
        self.db.execute('DELETE FROM tracks')
        self.db.execute('DELETE FROM albums')
        for index, label in enumerate(labels, 1):
            self.db.execute('INSERT INTO albums(id,artist_id,title,genre) VALUES(?,1,?,?)',
                            (index, f'Album {index:03}', label))
        offered = album_catalog_filters(self.db)['genres']
        self.assertEqual(len(offered), 9)
        all_ids = []
        for group in offered:
            page = self.page(genre=group, limit=1)
            ids = [self.page(genre=group, limit=1, offset=i)['albums'][0]['id']
                   for i in range(page['total'])]
            all_ids.extend(ids)
        self.assertCountEqual(all_ids, range(1, len(labels) + 1))
        self.assertEqual(self.page(genre='Asian Pop')['total'], 3)
        self.assertEqual(self.page(genre='Other')['total'], 2)
        self.assertEqual(self.db.execute('SELECT genre FROM albums WHERE id=1').fetchone()[0], labels[0])
        self.assertEqual(album_genre_group('Musiques du monde, Asie, Corée, K-pop'), 'Asian Pop')
        self.assertEqual(album_genre_group('Jazz, Jazz fusion & Jazz rock'), 'Jazz')

    def test_search_escapes_like_metacharacters(self):
        self.db.execute("UPDATE albums SET title='100% FLAC' WHERE id=3")
        self.db.commit()
        result=self.page(search="100%")
        self.assertEqual(result["total"],1)
        self.assertEqual(result["albums"][0]["id"],3)

    def test_artist_pages_have_stable_boundaries_and_search(self):
        for i in range(2, 153):
            name = f"Artist {i:03d}"
            self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(?,?,?)",(i,name,name))
        self.db.execute("INSERT INTO track_artists(track_id,artist_id) VALUES(1,1)")
        self.db.commit()
        pages = [fetch_artist_page(self.db,limit=60,offset=o) for o in (0,60,120)]
        self.assertEqual([len(page["artists"]) for page in pages],[60,60,32])
        self.assertEqual({page["total"] for page in pages},{152})
        ids = [a["id"] for page in pages for a in page["artists"]]
        self.assertEqual(len(set(ids)),152)
        result = fetch_artist_page(self.db,search="Artist 152")
        self.assertEqual(result["total"],1)
        self.assertEqual(result["artists"][0]["id"],152)
        first = fetch_artist_page(self.db,limit=1)
        self.assertEqual(first["artists"][0]["track_count"],1)

    def test_familiar_artist_ranking_precedes_local_popularity_and_pages_stably(self):
        for artist_id, name in [(2, '周杰倫'), (3, '陳奕迅'), (4, 'Various Artists')]:
            self.db.execute('INSERT INTO artists(id,name,sort_name) VALUES(?,?,?)', (artist_id, name, name))
        self.db.executemany('INSERT INTO play_history(track_id) VALUES(1)', [()] * 10)
        pages = [fetch_artist_page(self.db, sort='popular', limit=1, offset=i)['artists'][0]['id'] for i in range(4)]
        self.assertEqual(set(pages[:2]), {2, 3}, 'Known artists precede unfamiliar artists even with plays')
        self.assertEqual(pages[2:], [1, 4], 'Compilations come last')
        self.assertEqual(fetch_artist_page(self.db, sort='name')['artists'][0]['id'], 1)
        self.assertEqual(fetch_artist_page(self.db, search='周杰', sort='popular')['artists'][0]['id'], 2)

    def test_input_bounds(self):
        with self.assertRaises(ValueError):
            self.page(limit=201)
        with self.assertRaises(ValueError):
            self.page(offset=-1)
        with self.assertRaises(ValueError):
            self.page(decade="1990s OR 1=1")


if __name__=="__main__":
    unittest.main()
