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


class ReverseMatchParityTests(unittest.TestCase):
    """The index must never return fewer ids than an exact scan.

    ``match_track_ids(reverse=True)`` also matches when a stored name is a
    substring of the query. The reverse direction is served by
    ``track_search_terms``, so every value that goes into ``search_text`` must
    also produce terms. Credited artists and aliases used to be left out, which
    silently dropped them from reverse matches: a real library query "A-lin"
    lost a track whose credited name folded to "i".
    """

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(1,'Primary','Primary')")
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(2,'Guest','Guest')")
        self.db.execute("INSERT INTO albums(id,artist_id,title) VALUES(1,1,'Album')")
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(1,1,1,'Song','C:/music/1.flac')")
        # Track 1 is credited to artist 2, who is not the primary artist.
        self.db.execute("INSERT INTO track_artists(track_id,artist_id) VALUES(1,1),(1,2)")
        self.db.execute("INSERT INTO artist_aliases(artist_id,name) VALUES(2,'AB')")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _both_paths(self, query):
        indexed = match_track_ids(self.db, query, reverse=True)
        self.db.execute(
            "UPDATE search_index_meta SET value='stale' WHERE key='signature'")
        self.db.commit()
        exact = match_track_ids(self.db, query, reverse=True)
        return indexed, exact

    def test_terms_include_credited_artists_and_aliases(self):
        terms = {
            row[0] for row in self.db.execute(
                "SELECT term FROM track_search_terms WHERE track_id=1")
        }
        self.assertIn("guest", terms)
        self.assertIn("ab", terms)

    def test_reverse_match_on_alias_matches_indexed_and_exact(self):
        # Each query contains the alias "ab" or the credited name "guest", so
        # only the reverse direction can match.
        for query in ("zabz", "xxabyy", "zz ab zz", "a guest b"):
            with self.subTest(query=query):
                indexed, exact = self._both_paths(query)
                self.assertEqual(indexed, exact)
                self.assertIn(1, exact)

    def test_reverse_match_on_credited_artist_name(self):
        for query in ("the guest", "xguestx"):
            with self.subTest(query=query):
                indexed, exact = self._both_paths(query)
                self.assertEqual(indexed, exact)
                self.assertIn(1, exact)


class FoldIdempotenceTests(unittest.TestCase):
    """``search_fold`` must be a fixed point.

    OpenCC converts phrases, so one pass can leave text that a second pass
    still changes (陳昇 -> 陈昇 -> 陈升). Stored keys are compared with
    ``instr`` against folded query keys, so a key that moves on a second pass
    cannot match itself.
    """

    def test_known_non_idempotent_input_reaches_fixed_point(self):
        self.assertEqual(search_fold("陳昇"), search_fold("陳昇"))
        self.assertEqual(search_fold(search_fold("陳昇")), search_fold("陳昇"))

    def test_simplified_query_reaches_the_same_key_as_stored_text(self):
        # A user typing the mainland spelling must still find the record.
        self.assertEqual(search_fold("陳昇"), search_fold("陈升"))

    def test_fold_is_idempotent_over_a_mixed_sample(self):
        sample = [
            "陳昇", "周杰倫", "陳綺貞", "頭髮與發展", "後來的愛", "聽海",
            "快樂生活", "鄧紫棋", "A-lin", "100%_", "陳昇的專輯", "昇華",
        ]
        for text in sample:
            with self.subTest(text=text):
                once = search_fold(text)
                self.assertEqual(search_fold(once), once)


if __name__ == "__main__":
    unittest.main()
