"""Unified Velvet search and opt-in MusicBrainz aliases.

No network access. Fake MusicBrainz records have the same shape as its API.
"""
import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from artist_alias_sync import sync_artist_aliases, resolve_search_alias
from artist_aliases import add_alias
from chinese_variants import get_all_variants, to_simplified, to_traditional
from db_schema import apply_migrations
from library_discovery import search_library, search_library_category
from library_search_index import refresh_track_search_index
from library_search_match import match_catalog_ids, match_track_ids

MBID = "a47ac8f5-6774-4540-842f-f1eb0a19845c"


class MockResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class MockMusicBrainzClient:
    def __init__(self, *, artists=None, record=None):
        self.requests = []
        self.artists = artists if artists is not None else [
            {"id": MBID, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"}
        ]
        self.record = record if record is not None else {
            "id": MBID, "name": "徐佳瑩", "sort-name": "Hsu, LaLa",
            "aliases": [
                {"name": "徐佳莹", "locale": "zh_CN"},
                {"name": "Lala Hsu", "locale": "en"},
                {"name": "LaLa Hsu", "locale": "en"},
            ],
        }

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if url.endswith("/artist/"):
            return MockResponse({"artists": self.artists})
        return MockResponse(self.record)


async def _instant_sleep(_):
    return None


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

    def test_name_only_sync_requires_explicit_mbid_confirmation(self):
        # One identical-name search hit is NOT proof of identity; another
        # artist of the same name may be outside MusicBrainz's first page.
        fake = MockMusicBrainzClient()
        proposal = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(proposal["status"], "needs_review")
        self.assertEqual(proposal["candidates"][0]["mbid"], MBID)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM artist_aliases").fetchone()[0], 0)
        self.assertIsNone(self.db.execute("SELECT mbid FROM artists WHERE id=1").fetchone()[0])

        accepted = asyncio.run(sync_artist_aliases(
            self.db, 1, client=fake, confirmed_mbid=MBID,
        ))
        self.assertEqual(accepted["status"], "ok")
        self.db.commit()
        self.assertEqual(self.search("LaLa Hsu")["total_artists"], 1)
        self.assertEqual(self.search("LaLa Hsu")["total_albums"], 1)
        self.assertIn(1, match_track_ids(self.db, "LaLa Hsu"))
        self.assertEqual(self.db.execute("SELECT mbid FROM artists WHERE id=1").fetchone()[0], MBID)

    def test_two_same_named_remote_artists_require_review(self):
        other = "12345678-1234-1234-1234-123456789abc"
        fake = MockMusicBrainzClient(artists=[
            {"id": MBID, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"},
            {"id": other, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"},
        ])
        result = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(match_catalog_ids(self.db, "artist", "LaLa Hsu"), set())

    def test_one_click_alias_resolution_requires_confirmation_without_mbid(self):
        self.assertEqual(self.search("Lala Hsu")["artists"], [])
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            result = asyncio.run(resolve_search_alias(self.db, "Lala Hsu", client=fake))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["artist_id"], 1)
        self.assertEqual(result["candidate"]["mbid"], MBID)
        self.assertEqual(self.search("Lala Hsu")["artists"], [])
        accepted = asyncio.run(sync_artist_aliases(
            self.db, 1, client=fake, confirmed_mbid=MBID, query_alias="Lala Hsu",
        ))
        self.assertEqual(accepted["status"], "ok")
        self.db.commit()
        resolved = self.search("Lala Hsu")
        self.assertEqual([r["id"] for r in resolved["artists"]], [1])
        self.assertEqual([r["id"] for r in resolved["albums"]], [1])
        self.assertEqual([r["id"] for r in resolved["tracks"]], [1])

    def test_musicbrainz_alias_only_hit_gets_detailed_lookup(self):
        # Real search snippets may omit the aliases array entirely even
        # though the detailed record includes the queried stage name.
        fake = MockMusicBrainzClient(
            artists=[{"id": MBID, "name": "徐佳瑩", "sort-name": "徐佳瑩"}],
        )
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            proposal = asyncio.run(resolve_search_alias(self.db, "Lala Hsu", client=fake))
        self.assertEqual(proposal["status"], "needs_review")
        self.assertEqual(proposal["artist_id"], 1)

    def test_one_click_alias_resolution_rejects_unrelated_artist(self):
        fake = MockMusicBrainzClient(
            artists=[{"id": MBID, "name": "Other Person",
                      "sort-name": "Hsu, LaLa"}],
            record={"id": MBID, "name": "Other Person", "sort-name": "Hsu, LaLa",
                    "aliases": []},
        )
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            result = asyncio.run(resolve_search_alias(self.db, "Lala Hsu", client=fake))
        self.assertEqual(result["status"], "not_in_library")
        self.assertEqual(self.search("Lala Hsu")["artists"], [])

    def test_one_click_alias_resolution_never_guesses_on_ambiguous_artist(self):
        self.db.execute("INSERT INTO artists(id,name) VALUES(2,'徐佳莹')")
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            result = asyncio.run(resolve_search_alias(self.db, "Lala Hsu", client=fake))
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM artist_aliases").fetchone()[0], 0)

    def _confirm_local_mbid(self, mbid=MBID):
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (mbid,))
        self.db.execute("UPDATE artists SET mbid_verified=1 WHERE id=1")

    def test_musicbrainz_confirmed_mbid_needs_only_lookup(self):
        self._confirm_local_mbid()
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            status = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(fake.requests), 1)

    def test_resolve_bounds_paced_detail_lookups(self):
        # Each detailed record is a paced request the user waits for; ten of
        # them could outlast the client's timeout.
        hits = [{"id": f"00000000-0000-0000-0000-00000000000{i}", "name": "Other"}
                for i in range(1, 9)]
        fake = MockMusicBrainzClient(artists=hits)
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            asyncio.run(resolve_search_alias(self.db, "Lala Hsu", client=fake))
        details = [url for url, _ in fake.requests if not url.endswith("/artist/")]
        self.assertEqual(len(details), 5)

    def test_english_alias_found_in_candidate_six_with_user_continuation(self):
        # The first click remains fast. A second explicit click searches
        # candidates 6-10 without silently reporting not_in_library.
        other = [
            {"id": f"00000000-0000-0000-0000-{i:012x}", "name": "Other"}
            for i in range(1, 6)
        ]
        fake = MockMusicBrainzClient(
            artists=[*other, {"id": MBID, "name": "徐佳瑩"}],
        )
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            first = asyncio.run(resolve_search_alias(
                self.db, "Lala Hsu", client=fake, detail_offset=0,
            ))
            second = asyncio.run(resolve_search_alias(
                self.db, "Lala Hsu", client=fake, detail_offset=5,
            ))
        self.assertEqual(first["status"], "more_candidates")
        self.assertEqual(first["next_offset"], 5)
        self.assertEqual(second["status"], "needs_review")
        self.assertEqual(second["artist_id"], 1)
        self.assertEqual(second["candidate"]["mbid"], MBID)

    def test_unconfirmed_mbid_is_not_identity_evidence(self):
        # Bio enrichment stores the first name-search hit, which may be a
        # namesake whose record also carries the local name. It must not be
        # enough to import that record's aliases unattended.
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (MBID,))
        fake = MockMusicBrainzClient()
        result = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM artist_aliases WHERE artist_id=1"
        ).fetchone()[0], 0)

    def test_confirmation_may_replace_an_unconfirmed_guess(self):
        guessed = "11111111-2222-3333-4444-555555555555"
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (guessed,))
        fake = MockMusicBrainzClient()
        confirmed = asyncio.run(sync_artist_aliases(
            self.db, 1, client=fake, confirmed_mbid=MBID,
        ))
        self.assertEqual(confirmed["status"], "ok")
        row = self.db.execute("SELECT mbid, mbid_verified FROM artists WHERE id=1").fetchone()
        self.assertEqual((row[0], row[1]), (MBID, 1))

    def test_mbid_change_clears_confirmation(self):
        self._confirm_local_mbid()
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1",
                        ("11111111-2222-3333-4444-555555555555",))
        self.assertEqual(self.db.execute(
            "SELECT mbid_verified FROM artists WHERE id=1").fetchone()[0], 0)

    def test_resolve_ignores_an_unconfirmed_namesake_mbid(self):
        # An unconfirmed guess must neither bind silently nor exclude the
        # local artist from a verified match; the user confirms instead.
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1",
                        ("11111111-2222-3333-4444-555555555555",))
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            result = asyncio.run(resolve_search_alias(self.db, "Lala Hsu", client=fake))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["artist_id"], 1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM artist_aliases").fetchone()[0], 0)

    def test_preexisting_mbid_must_still_match_local_artist_identity(self):
        # Imported file tags can contain an incorrect MBID. Never import an
        # unrelated performer's aliases merely because an MBID was present.
        self._confirm_local_mbid()
        fake = MockMusicBrainzClient(record={
            "id": MBID, "name": "Different Artist",
            "sort-name": "Different Artist",
            "aliases": [{"name": "Unrelated Stage Name"}],
        })
        status = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(status["status"], "identity_mismatch")
        self.assertNotIn("Unrelated Stage Name", status["aliases"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM artist_aliases WHERE artist_id=1"
        ).fetchone()[0], 0)

    def test_musicbrainz_does_not_take_another_artist_primary_name(self):
        self.db.execute("INSERT INTO artists(id,name) VALUES(2,'LaLa Hsu')")
        fake = MockMusicBrainzClient()
        proposal = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(proposal["status"], "needs_review")
        confirmed = asyncio.run(sync_artist_aliases(
            self.db, 1, client=fake, confirmed_mbid=MBID,
        ))
        self.assertEqual(confirmed["status"], "ok")
        self.assertTrue(any(n.casefold() == "lala hsu" for n in confirmed["conflicts"]))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM artist_aliases WHERE artist_id=1 AND name=? COLLATE NOCASE",
            ("LaLa Hsu",),
        ).fetchone()[0], 0)

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
