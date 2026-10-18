"""Unified Velvet search: artists, albums and tracks, including manual aliases."""
import sqlite3
import unittest
from unittest.mock import patch

from artist_aliases import add_alias
from chinese_variants import get_all_variants, to_simplified, to_traditional
from db_schema import apply_migrations
from library_discovery import search_library, search_library_category
from library_search_index import refresh_track_search_index
from library_search_match import match_catalog_ids, match_track_ids


class UnifiedLibrarySearchTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute(
            "INSERT INTO artists(id,name,sort_name) "
            "VALUES(1,'徐佳瑩','徐佳瑩')"
        )
        self.db.execute(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(1,1,'理想人生',2012)"
        )
        self.db.execute(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) "
            "VALUES(1,1,1,'失落沙洲','C:/m/1.flac')"
        )
        self.db.execute("INSERT INTO track_artists(track_id,artist_id) VALUES(1,1)")
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def search(self, q, **kwargs):
        return search_library(
            self.db, q, 80, get_all_variants, to_simplified, to_traditional,
            **kwargs,
        )

    def test_simplified_name_returns_artists_albums_tracks(self):
        result = self.search("徐佳莹")
        self.assertEqual(list(result)[:3], ["artists", "albums", "tracks"])
        self.assertEqual([a["name"] for a in result["artists"]], ["徐佳瑩"])
        self.assertEqual([a["title"] for a in result["albums"]], ["理想人生"])
        self.assertEqual([t["title"] for t in result["tracks"]], ["失落沙洲"])
        self.assertEqual(result["total_artists"], 1)
        self.assertEqual(result["total_albums"], 1)

    def test_alias_is_searchable_before_refresh_in_all_three_categories(self):
        # Search must work even before dirty-track/catalog rows are reindexed.
        self.assertTrue(add_alias(self.db, 1, "LaLa Hsu"))
        self.db.commit()
        for q in ("lala hsu", "LALA HSU", "LaLa Hsu"):
            with self.subTest(q=q):
                result = self.search(q)
                self.assertEqual([a["id"] for a in result["artists"]], [1])
                self.assertEqual([a["id"] for a in result["albums"]], [1])
                self.assertEqual([t["id"] for t in result["tracks"]], [1])
        refresh_track_search_index(self.db)
        self.db.commit()
        self.assertEqual(self.search("lala hsu")["total_artists"], 1)

    def test_empty_query_returns_no_catalog_dump(self):
        self.assertEqual(
            self.search(""),
            {"artists": [], "albums": [], "tracks": [],
             "total_artists": 0, "total_albums": 0, "total_tracks": 0},
        )

    def test_search_result_page_sizes_are_bounded(self):
        self.assertEqual(len(self.search("徐", artist_limit=1, album_limit=1)["artists"]), 1)

    def _add_lee_catalog(self):
        # Alphabetically "Brenda Lee" and "Christopher Lee" sort before "Lee".
        for artist_id, name in ((2, "Brenda Lee"), (3, "Christopher Lee"), (4, "Lee"),
                                (5, "Lee Ritenour")):
            self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES(?,?,?)",
                            (artist_id, name, name))
        for album_id, artist_id, title in ((2, 2, "A Lee Album"), (3, 3, "Lee"),
                                           (4, 5, "Lee Sessions")):
            self.db.execute("INSERT INTO albums(id,artist_id,title) VALUES(?,?,?)",
                            (album_id, artist_id, title))
        self.db.commit()
        refresh_track_search_index(self.db)
        self.db.commit()

    def test_catalog_results_rank_exact_then_prefix_before_alphabetical(self):
        self._add_lee_catalog()
        result = self.search("lee")
        self.assertEqual([a["name"] for a in result["artists"]],
                         ["Lee", "Lee Ritenour", "Brenda Lee", "Christopher Lee"])
        # Exact title first, then a title starting with the query, then a
        # title that only contains it.
        self.assertEqual([a["title"] for a in result["albums"]],
                         ["Lee", "Lee Sessions", "A Lee Album"])
        self.assertEqual(result["total_artists"], 4)

    def test_catalog_rank_survives_paging(self):
        self._add_lee_catalog()
        names = [
            search_library_category(
                self.db, "artists", "lee", 1, offset,
                get_all_variants, to_simplified, to_traditional,
            )["items"][0]["name"]
            for offset in range(4)
        ]
        self.assertEqual(names, ["Lee", "Lee Ritenour", "Brenda Lee", "Christopher Lee"])

    def test_global_search_skips_artist_region_facets(self):
        # Classifying every artist into a region is for the Artists page
        # filter chips, not for every search keystroke.
        with patch("library_paging.artist_region_map",
                   side_effect=AssertionError("region map built for search")):
            result = self.search("徐佳瑩")
            page = search_library_category(
                self.db, "artists", "徐佳瑩", 10, 0,
                get_all_variants, to_simplified, to_traditional,
            )
        self.assertEqual([a["id"] for a in result["artists"]], [1])
        self.assertEqual(result["total_artists"], 1)
        self.assertEqual(page["total"], 1)
        self.assertIsNone(page["items"][0]["region"])

    def test_album_and_track_pagination_preserves_all_matches(self):
        from library_discovery import search_tracks_page
        self.db.executemany(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(?,1,?,2012)",
            [(album_id, f"理想人生 {album_id}") for album_id in range(2, 37)],
        )
        self.db.executemany(
            "INSERT INTO tracks(id,album_id,artist_id,title,file_path) VALUES(?,1,1,?,?)",
            [(track_id, f"失落沙洲 {track_id}", f"C:/m/{track_id}.flac")
             for track_id in range(2, 102)],
        )
        self.db.commit()
        initial = self.search("徐佳莹")
        self.assertEqual(initial["total_albums"], 36)
        self.assertEqual(len(initial["albums"]), 24)
        self.assertEqual(initial["total_tracks"], 101)
        self.assertEqual(len(initial["tracks"]), 80)

        album_more = search_library_category(
            self.db, "albums", "徐佳莹", 20, 24,
            get_all_variants, to_simplified, to_traditional,
        )
        self.assertEqual(album_more["total"], 36)
        self.assertEqual(len(album_more["items"]), 12)
        track_more = search_library_category(
            self.db, "tracks", "徐佳莹", 40, 80,
            get_all_variants, to_simplified, to_traditional,
        )
        self.assertEqual(track_more["total"], 101)
        self.assertEqual(len(track_more["items"]), 21)
        self.assertFalse({t["id"] for t in initial["tracks"]}
                         & {t["id"] for t in track_more["items"]})

    def test_artist_category_pagination(self):
        self.db.executemany(
            "INSERT INTO artists(id,name,sort_name) VALUES(?,?,?)",
            [(i, f"徐佳瑩 {i}", f"徐佳瑩 {i}") for i in range(2, 20)],
        )
        self.db.commit()
        first = self.search("徐佳莹")
        self.assertEqual(first["total_artists"], 19)
        self.assertEqual(len(first["artists"]), 12)
        more = search_library_category(
            self.db, "artists", "徐佳莹", 24, 12,
            get_all_variants, to_simplified, to_traditional,
        )
        self.assertEqual(more["total"], 19)
        self.assertEqual(len(more["items"]), 7)
        self.assertFalse({a["id"] for a in first["artists"]}
                         & {a["id"] for a in more["items"]})


if __name__ == "__main__":
    unittest.main()
