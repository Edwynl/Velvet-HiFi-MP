"""OpenCC Search V2 integration regressions; run on Windows before merging."""
import sqlite3
import unittest

from chinese_variants import search_fold, literal_fold
from db_schema import LATEST_SCHEMA_VERSION, apply_migrations
from library_search_index import refresh_track_search_index
from library_search_match import (
    _MAX_QUERY_LENGTH,
    _ready,
    match_track_ids,
    match_catalog_ids,
    normalize_query,
)
from library_browse import fetch_tracks
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
        # Reverse matching only runs for queries with script variants, so the
        # parity probes need a Chinese credited name and alias as well.
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(3,'張學友','張學友')")
        self.db.execute("INSERT INTO track_artists(track_id,artist_id) VALUES(1,3)")
        self.db.execute("INSERT INTO artist_aliases(artist_id,name) VALUES(3,'歌神')")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _both_paths(self, query):
        """Compare the indexed and exact-scan answers for ONE query.

        The exact path is reached by invalidating the index signature. That
        change must be undone every time: leaving it stale makes every later
        call take the exact path too, so the comparison silently degrades into
        exact-versus-exact and stops testing anything.
        """
        self.assertTrue(_ready(self.db), "index must be live before the indexed run")
        indexed = match_track_ids(self.db, query, reverse=True)
        try:
            self.db.execute(
                "UPDATE search_index_meta SET value='stale' WHERE key='signature'")
            self.db.commit()
            self.assertFalse(_ready(self.db),
                             "exact path must be active for the unindexed run")
            exact = match_track_ids(self.db, query, reverse=True)
        finally:
            refresh_track_search_index(self.db)
            self.db.commit()
        self.assertTrue(_ready(self.db), "index must be restored after each probe")
        return indexed, exact

    def test_terms_include_credited_artists_and_aliases(self):
        terms = {
            row[0] for row in self.db.execute(
                "SELECT term FROM track_search_terms WHERE track_id=1")
        }
        self.assertIn("guest", terms)
        self.assertIn("ab", terms)

    def test_reverse_match_on_alias_matches_indexed_and_exact(self):
        # Each query contains the alias 歌神 in either script, so only the
        # reverse direction can match.
        for query in ("聽歌神的歌", "听歌神的歌"):
            with self.subTest(query=query):
                indexed, exact = self._both_paths(query)
                self.assertEqual(indexed, exact)
                self.assertIn(1, exact)

    def test_reverse_match_on_credited_artist_name(self):
        for query in ("我聽張學友", "我听张学友"):
            with self.subTest(query=query):
                indexed, exact = self._both_paths(query)
                self.assertEqual(indexed, exact)
                self.assertIn(1, exact)

    def test_latin_query_never_matches_in_reverse(self):
        # "ab" and "guest" sit inside these queries; with no script variant
        # that must not pull the track in, on either path.
        for query in ("zabz", "xxabyy", "a guest b", "the guest"):
            with self.subTest(query=query):
                indexed, exact = self._both_paths(query)
                self.assertEqual(indexed, exact)
                self.assertEqual(exact, set())


class FoldIdempotenceTests(unittest.TestCase):
    """``search_fold`` must be a fixed point.

    OpenCC converts phrases, so one pass can leave text that a second pass
    still changes (陳昇 -> 陈昇 -> 陈升). Stored keys are compared with
    ``instr`` against folded query keys, so a key that moves on a second pass
    cannot match itself.
    """

    def test_known_non_idempotent_input_reaches_fixed_point(self):
        # A single OpenCC pass turns 陳昇 into 陈昇, which a second pass would
        # take further to 陈升. Folding to a fixed point must land on the final
        # canonical spelling, so assert that value rather than comparing the
        # call with itself.
        self.assertEqual(search_fold("陳昇"), "陈升")
        self.assertEqual(search_fold(search_fold("陳昇")), search_fold("陳昇"))
        self.assertEqual(literal_fold("陳昇"), "陳昇")

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


class SearchRelevanceTests(unittest.TestCase):
    """Ordering must answer the query, not merely contain it.

    A Simplified query also matches titles that are only a substring of it via
    the reverse clause, so sorting on raw LOWER(title) alone put 愛 above the
    exact match 愛你 for the query 爱你.
    """

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(1,'陳奕迅','陳奕迅')")
        self.db.execute("INSERT INTO albums(id,artist_id,title,genre) VALUES(1,1,'專輯','搖滾')")
        for track_id, title in ((1, "愛"), (2, "愛你"), (3, "愛你的承諾")):
            self.db.execute(
                "INSERT INTO tracks(id,album_id,artist_id,title,file_path) VALUES(?,1,1,?,?)",
                (track_id, title, f"C:/music/{track_id}.flac"),
            )
            self.db.execute(
                "INSERT INTO track_artists(track_id,artist_id) VALUES(?,1)", (track_id,))
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def titles(self, limit=10):
        return [t["title"] for t in search_tracks(self.db, "爱你", limit, None, None, None)["tracks"]]

    def test_exact_canonical_match_ranks_first(self):
        self.assertEqual(self.titles(1), ["愛你"])

    def test_ranking_is_exact_then_prefix_then_rest(self):
        self.assertEqual(self.titles(3), ["愛你", "愛你的承諾", "愛"])

    def test_browse_uses_the_same_relevance_order(self):
        rows = fetch_tracks(self.db, 10, 0, "爱你", "title", False)
        self.assertEqual([t["title"] for t in rows["tracks"]],
                         ["愛你", "愛你的承諾", "愛"])

    def test_relevance_order_is_stable_across_pages(self):
        pages = [fetch_tracks(self.db, 1, offset, "爱你", "title", False)["tracks"][0]["title"]
                 for offset in (0, 1, 2)]
        self.assertEqual(pages, ["愛你", "愛你的承諾", "愛"])

    def test_long_query_still_matches_via_reverse(self):
        """Reverse matching is what lets a full title find a shorter one."""
        ids = match_track_ids(self.db, "01 愛你", reverse=True)
        self.assertEqual(ids, {1, 2})
        self.assertEqual(match_track_ids(self.db, "01 愛你", reverse=False), set())


class GenreFoldingTests(unittest.TestCase):
    """A Simplified genre query must reach a Traditional stored label."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(1,'周杰倫','周杰倫')")
        self.db.execute("INSERT INTO albums(id,artist_id,title,genre) VALUES(1,1,'專輯','搖滾')")
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) VALUES(1,1,1,'歌','C:/m/1.flac')")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_simplified_genre_query_matches_traditional_genre(self):
        self.assertEqual(fetch_tracks(self.db, 10, 0, "摇滚", "title", False)["total"], 1)
        self.assertEqual(fetch_tracks(self.db, 10, 0, "搖滾", "title", False)["total"], 1)

    def test_genre_edit_requeries_the_album(self):
        self.db.execute("UPDATE albums SET genre='金屬' WHERE id=1")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()
        self.assertEqual(fetch_tracks(self.db, 10, 0, "摇滚", "title", False)["total"], 0)
        self.assertEqual(fetch_tracks(self.db, 10, 0, "金属", "title", False)["total"], 1)

    def test_album_search_does_not_start_matching_on_genre(self):
        self.assertNotIn(1, match_catalog_ids(self.db, "album", "摇滚"))

    def test_genre_edit_is_visible_before_a_refresh(self):
        """A pending genre must answer from albums.genre, not the stale key.

        The catalog row keeps the previous genre until the next refresh, so
        reading it unconditionally made 摇滚 still match and 金属 miss after
        the edit.
        """
        self.db.execute("UPDATE albums SET genre='金屬' WHERE id=1")
        self.db.commit()
        pending = [row[0] for row in self.db.execute(
            "SELECT entity_id FROM catalog_search_dirty WHERE kind='album'")]
        self.assertEqual(pending, [1])

        before = fetch_tracks(self.db, 10, 0, "摇滚", "title", False)["total"]
        after = fetch_tracks(self.db, 10, 0, "金属", "title", False)["total"]
        self.assertEqual(before, 0)
        self.assertEqual(after, 1)

        refresh_track_search_index(self.db)
        self.db.commit()
        self.assertEqual(fetch_tracks(self.db, 10, 0, "摇滚", "title", False)["total"], 0)
        self.assertEqual(fetch_tracks(self.db, 10, 0, "金属", "title", False)["total"], 1)

    def test_new_album_is_searchable_by_genre_before_a_refresh(self):
        """A freshly inserted album has no catalog index row yet."""
        self.db.execute(
            "INSERT INTO albums(id,artist_id,title,genre) VALUES(2,1,'新輯','金屬')")
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(2,2,1,'新歌','C:/music/2.flac')")
        self.db.commit()
        self.assertEqual(fetch_tracks(self.db, 10, 0, "金属", "title", False)["total"], 1)

    def test_deleted_album_drops_out_of_genre_search(self):
        self.db.execute("DELETE FROM tracks WHERE id=1")
        self.db.execute("DELETE FROM albums WHERE id=1")
        self.db.commit()
        self.assertEqual(fetch_tracks(self.db, 10, 0, "摇滚", "title", False)["total"], 0)
        self.assertEqual(fetch_tracks(self.db, 10, 0, "金屬", "title", False)["total"], 0)

    def test_genre_search_counts_match_the_page(self):
        """`total` and the returned rows must agree while a genre is pending."""
        self.db.execute("UPDATE albums SET genre='金屬' WHERE id=1")
        self.db.commit()
        result = fetch_tracks(self.db, 10, 0, "金属", "title", False)
        self.assertEqual(result["total"], len(result["tracks"]))
        self.assertTrue(all(t["genre"] == "金屬" for t in result["tracks"]))

    def test_genre_search_respects_favorites_filter(self):
        """The favorites filter must sit on top of the fold, not bypass it.

        Both spellings have to agree in both directions, otherwise a
        non-favorite album stays reachable through the other script.
        """
        self.db.execute("UPDATE albums SET genre='金屬' WHERE id=1")
        self.db.execute("UPDATE albums SET is_favorite=1 WHERE id=1")
        self.db.commit()
        for spelling in ("金属", "金屬"):
            self.assertEqual(
                fetch_tracks(self.db, 10, 0, spelling, "title", True)["total"], 1,
                f"favorited album must match {spelling}")

        self.db.execute("UPDATE albums SET is_favorite=0 WHERE id=1")
        self.db.commit()
        for spelling in ("金属", "金屬"):
            self.assertEqual(
                fetch_tracks(self.db, 10, 0, spelling, "title", True)["total"], 0,
                f"non-favorite album must not match {spelling}")


class PunctuationRankingTests(unittest.TestCase):
    """A user's % and _ are literals, never SQL wildcards.

    `LIKE ? || '%'` without an ESCAPE clause lets a query such as `a_b` match
    the unrelated title `aXb`, promoting a track that only entered the match
    set through its artist name above the literal title match.
    """

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(1,'a_b','a_b')")
        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(2,'Someone','Someone')")
        self.db.execute("INSERT INTO albums(id,artist_id,title) VALUES(1,2,'Album')")
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(1,1,1,'aXb','C:/music/1.flac')")
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(2,1,2,'za_b','C:/music/2.flac')")
        self.db.execute("INSERT INTO track_artists(track_id,artist_id) VALUES(1,1),(2,2)")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_underscore_query_does_not_promote_a_wildcard_title(self):
        titles = [t["title"] for t in
                  search_tracks(self.db, "a_b", 5, None, None, None)["tracks"]]
        self.assertEqual(titles, ["za_b", "aXb"])

    def test_prefix_rank_uses_literal_instr(self):
        row = self.db.execute(
            "SELECT instr(COALESCE(title_key,''), ?) FROM track_search_index "
            "WHERE track_id=1", ("a_b",)).fetchone()[0]
        self.assertEqual(row, 0)
        like = self.db.execute(
            "SELECT ? LIKE ? || '%'", ("axb", "a_b")).fetchone()[0]
        self.assertEqual(like, 1, "the wildcard behaviour this test guards against")

    def test_percent_query_is_literal(self):
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(3,1,2,'100% FLAC','C:/music/3.flac')")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()
        titles = [t["title"] for t in
                  search_tracks(self.db, "100%", 5, None, None, None)["tracks"]]
        self.assertEqual(titles, ["100% FLAC"])
        self.assertEqual(
            [t["title"] for t in
             fetch_tracks(self.db, 5, 0, "100%", "title", False)["tracks"]],
            ["100% FLAC"])


class QueryBoundsTests(unittest.TestCase):
    def test_long_query_is_truncated_for_matching(self):
        raw, folded = normalize_query("愛" * 5000)
        self.assertEqual(len(folded), _MAX_QUERY_LENGTH)

    def test_normal_query_is_untouched(self):
        self.assertEqual(normalize_query("谢谢你"),
                         (literal_fold("谢谢你"), search_fold("谢谢你")))


if __name__ == "__main__":
    unittest.main()
