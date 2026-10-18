"""Unified Velvet search and opt-in MusicBrainz aliases.

No network access. Fake MusicBrainz records have the same shape as its API.
"""
import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from artist_alias_sync import sync_artist_aliases
from artist_aliases import add_alias
from chinese_variants import get_all_variants, to_simplified, to_traditional
from db_schema import apply_migrations
from library_discovery import search_library
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
             "total_artists": 0, "total_albums": 0},
        )

    def test_search_result_page_sizes_are_bounded(self):
        self.assertEqual(len(self.search("徐", artist_limit=1, album_limit=1)["artists"]), 1)

    def test_musicbrainz_sync_imports_lala_hsu_without_hardcoded_mapping(self):
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            status = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(status["status"], "ok")
        self.assertIn("LaLa Hsu".casefold(), {a.casefold() for a in status["aliases"]})
        self.assertEqual(len(fake.requests), 2)
        self.db.commit()
        self.assertEqual(self.search("LaLa Hsu")["total_artists"], 1)
        self.assertEqual(self.search("LaLa Hsu")["total_albums"], 1)
        self.assertIn(1, match_track_ids(self.db, "LaLa Hsu"))
        self.assertEqual(
            self.db.execute("SELECT mbid FROM artists WHERE id=1").fetchone()[0], MBID
        )

    def test_musicbrainz_ambiguous_identity_does_not_write(self):
        other = "12345678-1234-1234-1234-123456789abc"
        fake = MockMusicBrainzClient(artists=[
            {"id": MBID, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"},
            {"id": other, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"},
        ])
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            status = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(status["status"], "ambiguous")
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(match_catalog_ids(self.db, "artist", "LaLa Hsu"), set())

    def test_musicbrainz_existing_mbid_needs_only_lookup(self):
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (MBID,))
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            status = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(fake.requests), 1)

    def test_musicbrainz_does_not_take_another_artist_primary_name(self):
        self.db.execute("INSERT INTO artists(id,name) VALUES(2,'LaLa Hsu')")
        fake = MockMusicBrainzClient()
        with patch("artist_alias_sync.asyncio.sleep", _instant_sleep):
            status = asyncio.run(sync_artist_aliases(self.db, 1, client=fake))
        self.assertEqual(status["status"], "ok")
        self.assertTrue(any(n.casefold() == "lala hsu" for n in status["conflicts"]))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM artist_aliases WHERE artist_id=1 AND name=? COLLATE NOCASE",
            ("LaLa Hsu",),
        ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
