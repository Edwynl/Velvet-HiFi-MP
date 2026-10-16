"""Album paging must be bounded before Python rows reach a mobile client."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_schema import apply_migrations
from library_paging import album_catalog_filters, fetch_album_page, fetch_artist_page


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

    def test_input_bounds(self):
        with self.assertRaises(ValueError):
            self.page(limit=201)
        with self.assertRaises(ValueError):
            self.page(offset=-1)
        with self.assertRaises(ValueError):
            self.page(decade="1990s OR 1=1")


if __name__=="__main__":
    unittest.main()
