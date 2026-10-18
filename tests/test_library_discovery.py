import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from chinese_variants import get_all_variants, to_simplified, to_traditional
from db_schema import apply_migrations
from library_discovery import (
    fetch_album_radio,
    fetch_artist_detail_summary,
    fetch_artist_radio,
    fetch_discover,
    search_tracks,
)
from library_search_index import refresh_track_search_index
from library_search_match import match_track_ids


class LibraryDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "library.db"
        with self._connect() as conn:
            apply_migrations(conn)
            self._seed(conn)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def _seed(self, conn: sqlite3.Connection):
        conn.execute("INSERT INTO artists (id, name, sort_name, bio) VALUES (1, 'Artist One', 'Artist One', 'Bio text')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (2, 'Artist Two', 'Artist Two')")
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, is_favorite)
            VALUES (1, 1, 'Album One', 2001, 'Classical', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, is_favorite)
            VALUES (2, 2, 'Album Two', 2003, 'Jazz', 0)
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (
                id, album_id, artist_id, title, composer, work_title, performer, conductor,
                ensemble, track_number, duration, sample_rate, bit_depth, file_path
            ) VALUES
                (1, 1, 1, 'Opening Work', 'Composer Alpha', 'Work A', 'Soloist One', 'Conductor One', 'Ensemble One', 1, 300, 96000, 24, 'C:/music/one.flac'),
                (2, 1, 1, 'Second Work', 'Composer Alpha', 'Work B', 'Soloist One', 'Conductor One', 'Ensemble One', 2, 280, 96000, 24, 'C:/music/two.flac'),
                (3, 2, 2, 'Night Jazz', 'Composer Beta', 'Work C', 'Soloist Two', '', '', 1, 260, 44100, 16, 'C:/music/three.flac')
            """
        )
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 1)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (2, 1)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (3, 2)")
        conn.execute("INSERT INTO play_history (track_id, duration_played) VALUES (1, 300)")
        conn.execute("INSERT INTO play_history (track_id, duration_played) VALUES (1, 300)")
        conn.execute("INSERT INTO play_history (track_id, duration_played) VALUES (3, 260)")
        conn.commit()

    def test_fetch_artist_detail_summary_returns_counts(self):
        with self._connect() as conn:
            result = fetch_artist_detail_summary(conn, 1)

        self.assertEqual(result["name"], "Artist One")
        self.assertEqual(result["album_count"], 1)
        self.assertEqual(result["track_count"], 2)
        self.assertEqual(result["play_count"], 2)

    def test_search_tracks_matches_credit_fields(self):
        with self._connect() as conn:
            result = search_tracks(
                conn,
                q="conductor one",
                limit=10,
                get_all_variants=lambda text: [text],
                to_simplified=lambda text: text,
                to_traditional=lambda text: text,
            )

        self.assertEqual(len(result["tracks"]), 2)
        self.assertEqual(result["tracks"][0]["artist_name"], "Artist One")

    def test_variant_search_reaches_far_end_of_library(self):
        """Previous fallback sampled only first remaining*3 songs."""
        with self._connect() as db:
            for i in range(10, 700):
                db.execute(
                    "INSERT INTO tracks(album_id,artist_id,title,file_path) VALUES(1,1,?,?)",
                    (f"Other song {i}", f"C:/music/{i}.flac"),
                )
            db.execute(
                "INSERT INTO tracks(album_id,artist_id,title,file_path) "
                "VALUES(1,1,'快樂的音樂','C:/music/last.flac')"
            )
            db.commit()
            result = search_tracks(
                db, q="快乐", limit=10,
                get_all_variants=lambda q: [q],
                to_simplified=lambda x: x.replace("樂", "乐"),
                to_traditional=lambda x: x.replace("乐", "樂"),
            )
        self.assertTrue(any("快樂" in track["title"] for track in result["tracks"]))

    def test_fetch_discover_returns_sections(self):
        with self._connect() as conn:
            result = fetch_discover(conn, 5)

        self.assertEqual(result["favorites"][0]["title"], "Album One")
        self.assertEqual(result["trending_artists"][0]["name"], "Artist One")
        self.assertGreaterEqual(len(result["recent_tracks"]), 1)

    def test_fetch_radio_builds_seeded_queue(self):
        with self._connect() as conn:
            album_radio = fetch_album_radio(conn, 1, 10)
            artist_radio = fetch_artist_radio(conn, 1, 10)

        self.assertEqual(album_radio["mode"], "album_radio")
        self.assertEqual(album_radio["seed"]["title"], "Album One")
        self.assertGreaterEqual(len(album_radio["tracks"]), 2)
        self.assertEqual(artist_radio["mode"], "artist_radio")
        self.assertEqual(artist_radio["seed"]["name"], "Artist One")


def _real_search(db, q, limit=80):
    result = search_tracks(db, q, limit, get_all_variants, to_simplified, to_traditional)
    return [track["id"] for track in result["tracks"]]


class TrackSearchIndexTests(unittest.TestCase):
    """The index must only narrow candidates: indexed answers equal exact ones."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists (id, name) VALUES (1, 'Pianist'), (2, '周杰倫'), (3, 'Guest')")
        self.db.execute(
            "INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Filler'), (2, 2, '鋼琴精選')"
        )
        self.db.executemany(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (?, 1, 1, ?, ?)",
            [(i, f"Other song {i}", f"C:/music/{i}.flac") for i in range(1, 1500)],
        )
        self.db.executemany(
            "INSERT INTO tracks (id, album_id, artist_id, title, composer, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1500, 1, 1, "Track_01", None, "C:/music/a.flac"),
                (1501, 1, 1, "鋼琴奏鳴曲", None, "C:/music/b.flac"),  # traditional title
                (1502, 1, 1, "Waltz", "钢琴家", "C:/music/c.flac"),   # simplified credit
                (1503, 2, 2, "月亮代表我的心", None, "C:/music/d.flac"),
                (1504, 1, 1, "琴", None, "C:/music/e.flac"),          # field inside query
            ],
        )
        self.db.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1503, 2), (1502, 3)")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _exact(self, q, limit=80):
        self.db.execute("DELETE FROM search_index_meta")  # unsigned index: exact path
        try:
            return _real_search(self.db, q, limit)
        finally:
            self.db.rollback()

    def _build(self):
        refresh_track_search_index(self.db)
        self.db.commit()

    def test_conversion_map_supports_folding(self):
        # OpenCC is phrase-aware and S2T is not reversible: compare canonical
        # *search keys*, not individual character round trips.
        from library_search_index import search_fold
        for traditional, simplified in (
            ("周杰倫", "周杰伦"), ("快樂", "快乐"),
            ("頭髮", "头发"), ("發展", "发展"),
            ("陳綺貞", "陈绮贞"), ("後來", "后来"),
        ):
            with self.subTest(traditional=traditional):
                self.assertEqual(search_fold(traditional), search_fold(simplified))

    def test_variants_match_near_end_of_table_with_index(self):
        self._build()
        for q in ("钢琴", "鋼琴"):
            with self.subTest(q=q):
                ids = _real_search(self.db, q)
                self.assertIn(1501, ids)  # traditional title, last rows of the table
                self.assertIn(1502, ids)  # simplified composer credit
                self.assertIn(1503, ids)  # traditional album title
                self.assertIn(1504, ids)  # whole field '琴' inside the query
                self.assertEqual(ids, self._exact(q))

    def test_traditional_artist_found_from_simplified_query(self):
        self._build()
        self.assertEqual(_real_search(self.db, "周杰伦"), [1503])
        self.assertEqual(_real_search(self.db, "周杰倫"), [1503])

    def test_indexed_answers_equal_exact_answers(self):
        self._build()
        for q in ("钢琴", "鋼琴", "琴", "song 14", "SONG 149", "guest", "pianist", "track_01",
                  "%", "_", "o%g", "月亮", "zz不存在的琴", "Other song 1499"):
            for limit in (1, 5, 80):
                with self.subTest(q=q, limit=limit):
                    self.assertEqual(_real_search(self.db, q, limit), self._exact(q, limit))

    def test_unrefreshed_changes_stay_correct(self):
        self._build()
        self.db.execute("UPDATE tracks SET title='鋼琴小品' WHERE id=10")
        self.db.execute("UPDATE albums SET title='鋼琴與我' WHERE id=1")  # dirties every filler track
        self.db.execute("UPDATE artists SET name='鋼琴手' WHERE id=3")
        self.db.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
            "VALUES (1600, 2, 2, '新鋼琴曲', 'C:/music/f.flac')"
        )
        self.db.commit()
        dirty = self.db.execute("SELECT COUNT(*) FROM track_search_dirty").fetchone()[0]
        self.assertGreater(dirty, 1500)
        for q in ("钢琴", "鋼琴手", "钢琴与我"):
            with self.subTest(q=q):
                before_page = _real_search(self.db, q)
                self.assertEqual(before_page, self._exact(q))
                # The complete match set, not a ranked top-N page: ranking reads
                # the stored title_key, so a just-renamed track is only promoted
                # once its index row is rebuilt. Comparing two ranked pages would
                # only measure that reordering.
                before_ids = match_track_ids(self.db, q, reverse=True)
                self._build()
                self.assertEqual(match_track_ids(self.db, q, reverse=True), before_ids)
                self.db.execute("UPDATE albums SET title=title || '' WHERE id=1")
        # Track 10 was renamed to 鋼琴小品. Once rebuilt, its folded title is a
        # prefix of 钢琴, so ranking must promote it above the "Other song N"
        # fillers instead of leaving it buried in title order.
        self._build()
        ranked = _real_search(self.db, "钢琴", 5)
        self.assertIn(10, ranked)
        self.assertNotIn(101, ranked)
        self.assertIn(1600, _real_search(self.db, "钢琴曲"))

    def test_deleted_track_leaves_no_index_rows(self):
        self._build()
        self.db.execute("DELETE FROM tracks WHERE id=1501")
        self.db.commit()
        self.assertNotIn(1501, _real_search(self.db, "钢琴"))
        for table in ("track_search_index", "track_search_terms", "track_search_dirty"):
            row = self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE track_id=1501").fetchone()
            self.assertEqual(row[0], 0, table)

    def test_changed_signature_rebuilds_every_track(self):
        self._build()
        self.db.execute("UPDATE search_index_meta SET value='old' WHERE key='signature'")
        self.db.commit()
        self.assertEqual(_real_search(self.db, "钢琴"), self._exact("钢琴"))
        self.assertEqual(refresh_track_search_index(self.db), 1504)

    def test_refresh_is_a_no_op_before_schema_10(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY)")
        self.assertEqual(refresh_track_search_index(db), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
