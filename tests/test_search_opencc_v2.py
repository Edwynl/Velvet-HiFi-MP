"""OpenCC Search V2 integration regressions; run on Windows before merging."""
import sqlite3
import unittest

from chinese_variants import search_fold
from db_schema import LATEST_SCHEMA_VERSION, apply_migrations
from library_search_index import refresh_track_search_index
from library_search_match import match_track_ids, match_catalog_ids
from library_paging import fetch_album_page, fetch_artist_page
from library_discovery import search_tracks
from chinese_variants import get_all_variants, to_simplified, to_traditional


class OpenCCSearchV2Tests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.assertEqual(apply_migrations(self.db), LATEST_SCHEMA_VERSION)
        self.db.executemany(
            "INSERT INTO artists(id,name,sort_name) VALUES(?,?,?)",
            [(1,"周杰倫","周杰倫"),(2,"陳綺貞","陳綺貞"),
             (3,"鄧紫棋","鄧紫棋"),(4,"Other","Other")],
        )
        self.db.executemany(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(?,?,?,?)",
            [(1,1,"快樂生活",2000),(2,2,"頭髮與發展",2001),
             (3,3,"後來的愛",2002),(4,4,"Other",2003)],
        )
        self.db.executemany(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) VALUES(?,?,?,?,?)",
            [(1,1,1,"聽海","C:/music/1.flac"),(2,2,2,"發展","C:/music/2.flac"),
             (3,3,3,"後來","C:/music/3.flac"),(4,4,4,"100%_","C:/music/4.flac")],
        )
        self.db.execute("INSERT INTO track_artists VALUES(1,1)")
        self.db.execute("INSERT INTO artist_aliases(artist_id,name) VALUES(1,'Jay Chou')")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def search(self, query):
        return [row["id"] for row in search_tracks(
            self.db, query, 100, get_all_variants,
            to_simplified, to_traditional)["tracks"]]

    def test_phrase_converter(self):
        for lhs, rhs in [("周杰倫","周杰伦"),("陳綺貞","陈绮贞"),
                         ("鄧紫棋","邓紫棋"),("快樂","快乐"),
                         ("頭髮","头发"),("發展","发展"),("後來","后来")]:
            with self.subTest(lhs=lhs):
                self.assertEqual(search_fold(lhs), search_fold(rhs))

    def test_full_search_artist_album_song_alias(self):
        self.assertIn(1,self.search("周杰伦"))
        self.assertIn(1,self.search("听海"))
        self.assertIn(1,self.search("Jay Chou"))
        self.assertIn(2,self.search("陈绮贞"))
        self.assertIn(3,self.search("邓紫棋"))
        self.assertIn(3,self.search("后来"))
        self.assertIn(1,self.search("快乐"))
        self.assertIn(2,self.search("发展"))
        self.assertIn(4,self.search("100%_"))

    def test_artist_album_catalog_count_and_page(self):
        self.assertEqual(fetch_artist_page(self.db,search="周杰伦")["total"],1)
        self.assertEqual(fetch_artist_page(self.db,search="Jay Chou")["total"],1)
        self.assertEqual(fetch_album_page(self.db,search="快乐")["total"],1)
        self.assertEqual(fetch_album_page(self.db,search="头发")["total"],1)
        self.assertEqual(fetch_album_page(self.db,search="后来的爱")["total"],1)

    def test_dirty_update_before_reindex(self):
        self.db.execute("UPDATE tracks SET title='繁體樂曲' WHERE id=1")
        self.db.execute("UPDATE artists SET name='新歌手' WHERE id=1")
        self.db.execute("UPDATE albums SET title='新專輯' WHERE id=1")
        self.db.commit()
        self.assertIn(1,self.search("繁体乐曲"))
        self.assertIn(1,match_catalog_ids(self.db,"artist","新歌手"))
        self.assertIn(1,match_catalog_ids(self.db,"album","新专辑"))
        self.assertNotIn(1,self.search("听海"))
        refresh_track_search_index(self.db)
        self.db.commit()
        self.assertIn(1,self.search("繁体乐曲"))

    def test_index_and_unindexed_match(self):
        probes=["周杰伦","聽","快乐","快樂","陈绮贞","发展","后来的爱",
                "Jay Chou","100%_","不存在","琴"]
        before={q:match_track_ids(self.db,q) for q in probes}
        self.db.execute("UPDATE search_index_meta SET value='obsolete' WHERE key='signature'")
        after={q:match_track_ids(self.db,q) for q in probes}
        self.assertEqual(before,after)

    def test_deletion_removes_catalog_and_track(self):
        self.db.execute("DELETE FROM tracks WHERE id=3")
        self.db.execute("DELETE FROM albums WHERE id=3")
        self.db.commit()
        self.assertNotIn(3,self.search("后来"))
        self.assertNotIn(3,match_catalog_ids(self.db,"album","后来的爱"))


if __name__ == "__main__":
    unittest.main()
