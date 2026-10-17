import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from artist_aliases import add_alias, fetch_aliases, merge_artists, rename_artist, resolve_alias
from chinese_variants import get_all_variants, to_simplified, to_traditional
from db_schema import MIGRATIONS, apply_migrations
from library_browse import fetch_artists
from library_discovery import fetch_artist_detail_summary, search_tracks
from library_paging import fetch_artist_page
from library_search_index import refresh_track_search_index
from scanner_service import _artist_id


class ArtistAliasMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "library.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def test_upgrade_from_schema_10_keeps_data_and_adds_alias_table(self):
        with self._connect() as conn:
            conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL)")
            for version, name, migration in MIGRATIONS:
                if version > 10:
                    break
                migration(conn)
                conn.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, name))
            conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Sammi Cheng')")
            conn.commit()

            self.assertEqual(apply_migrations(conn), 11)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(artist_aliases)")}
            artist = conn.execute("SELECT name FROM artists WHERE id = 1").fetchone()

        self.assertEqual(columns, {"id", "artist_id", "name", "created_at"})
        self.assertEqual(artist["name"], "Sammi Cheng")

    def test_alias_names_are_unique_case_insensitively_and_cascade(self):
        with self._connect() as conn:
            apply_migrations(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("INSERT INTO artists (id, name) VALUES (1, '鄭秀文'), (2, 'Other')")
            conn.execute("INSERT INTO artist_aliases (artist_id, name) VALUES (1, 'Sammi Cheng')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO artist_aliases (artist_id, name) VALUES (2, 'SAMMI CHENG')")
            conn.execute("DELETE FROM artists WHERE id = 1")
            remaining = conn.execute("SELECT COUNT(*) FROM artist_aliases").fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_alias_changes_queue_credited_tracks_for_the_search_index(self):
        with self._connect() as conn:
            apply_migrations(conn)
            conn.execute("INSERT INTO artists (id, name) VALUES (1, '鄭秀文')")
            conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, '舊歌')")
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, '心水', 'a.flac')")
            conn.execute("INSERT INTO track_artists VALUES (1, 1)")
            refresh_track_search_index(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM track_search_dirty").fetchone()[0], 0)

            conn.execute("INSERT INTO artist_aliases (artist_id, name) VALUES (1, 'Sammi Cheng')")
            queued = [row[0] for row in conn.execute("SELECT track_id FROM track_search_dirty")]
            refresh_track_search_index(conn)
            text = conn.execute("SELECT search_text FROM track_search_index WHERE track_id = 1").fetchone()[0]
        self.assertEqual(queued, [1])
        self.assertIn("sammi cheng", text)


class ArtistMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "library.db"
        with self._connect() as conn:
            apply_migrations(conn)
            conn.executescript(
                """
                INSERT INTO artists (id, name, sort_name, image_path) VALUES
                    (1, '莫文蔚', '莫文蔚', NULL),
                    (2, 'Karen Mok', 'Karen Mok', 'karen.jpg'),
                    (3, 'Somebody Else', 'Somebody Else', NULL);
                INSERT INTO albums (id, artist_id, title, year, is_favorite) VALUES
                    (1, 1, '回蔚', 2018, 0),
                    (2, 2, 'Karen', 1993, 0),
                    (3, 2, '回蔚', NULL, 1);
                INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES
                    (1, 1, 1, '如果没有你', 'a.flac'),
                    (2, 2, 2, 'Love Song', 'b.flac'),
                    (3, 3, 2, '盛夏的果實', 'c.flac'),
                    (4, 2, 2, 'Duet', 'd.flac');
                INSERT INTO track_artists VALUES (1, 1), (2, 2), (3, 2), (4, 2), (4, 3), (4, 1);
                """
            )
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def test_merge_moves_albums_tracks_credits_and_keeps_the_name_as_alias(self):
        with self._connect() as conn:
            result = merge_artists(conn, 1, [2])
            conn.commit()
            artists = [row[0] for row in conn.execute("SELECT id FROM artists ORDER BY id")]
            album_owners = {row[0]: row[1] for row in conn.execute("SELECT id, artist_id FROM albums")}
            track_owners = {row[0] for row in conn.execute("SELECT DISTINCT artist_id FROM tracks")}
            credits = sorted(tuple(row) for row in conn.execute("SELECT track_id, artist_id FROM track_artists"))
            merged_album = conn.execute("SELECT year, is_favorite, total_tracks FROM albums WHERE id = 1").fetchone()
            image = conn.execute("SELECT image_path FROM artists WHERE id = 1").fetchone()[0]

        self.assertEqual(artists, [1, 3])
        self.assertEqual(result["aliases"], ["Karen Mok"])
        self.assertEqual(result["albums_merged"], 1)
        # The same-titled album of the merged row joined the existing one.
        self.assertEqual(album_owners, {1: 1, 2: 1})
        self.assertEqual(tuple(merged_album), (2018, 1, 2))
        self.assertEqual(track_owners, {1})
        self.assertEqual(credits, [(1, 1), (2, 1), (3, 1), (4, 1), (4, 3)])
        self.assertEqual(image, "karen.jpg")

    def test_merged_artist_page_lists_all_albums_and_aliases(self):
        with self._connect() as conn:
            merge_artists(conn, 1, [2])
            conn.commit()
            detail = fetch_artist_detail_summary(conn, 1)
            albums = conn.execute(
                "SELECT DISTINCT t.album_id FROM tracks t JOIN track_artists ta ON ta.track_id = t.id WHERE ta.artist_id = 1"
            ).fetchall()
        self.assertEqual(detail["aliases"], ["Karen Mok"])
        self.assertEqual(detail["album_count"], 2)
        self.assertEqual(len(albums), 2)

    def test_merge_carries_the_sources_aliases(self):
        with self._connect() as conn:
            add_alias(conn, 2, "Mok Man-wai")
            merge_artists(conn, 1, [2])
            self.assertEqual(fetch_aliases(conn, 1), ["Karen Mok", "Mok Man-wai"])

    def test_rename_keeps_old_name_and_merges_into_an_existing_name(self):
        with self._connect() as conn:
            rename_artist(conn, 3, "某人")
            self.assertEqual(fetch_aliases(conn, 3), ["Somebody Else"])
            result = rename_artist(conn, 2, "莫文蔚")
            self.assertEqual(result["artist_id"], 1)
            self.assertEqual(fetch_aliases(conn, 1), ["Karen Mok"])
            name = conn.execute("SELECT name, sort_name FROM artists WHERE id = 3").fetchone()
        self.assertEqual(tuple(name), ("某人", "某人"))

    def test_add_alias_refuses_own_name_and_another_artists_alias(self):
        with self._connect() as conn:
            self.assertFalse(add_alias(conn, 1, "莫文蔚"))
            self.assertTrue(add_alias(conn, 1, "Karen"))
            self.assertFalse(add_alias(conn, 3, "karen"))
            self.assertTrue(add_alias(conn, 1, "KAREN"))
            self.assertEqual(resolve_alias(conn, "karen"), 1)

    def test_scanner_resolves_an_alias_instead_of_recreating_the_artist(self):
        with self._connect() as conn:
            merge_artists(conn, 1, [2])
            cache: dict[str, int] = {}
            self.assertEqual(_artist_id(conn, "Karen Mok", cache), 1)
            self.assertEqual(_artist_id(conn, "莫文蔚", cache), 1)
            count = conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
        self.assertEqual(count, 2)

    def test_case_only_spelling_folds_instead_of_recreating_the_artist(self):
        with self._connect() as conn:
            # artist_aliases.name is UNIQUE COLLATE NOCASE, so "KAREN MOK"
            # cannot be recorded as an alias of "Karen Mok" at all.
            self.assertFalse(add_alias(conn, 2, "KAREN MOK"))
            cache: dict[str, int] = {}
            self.assertEqual(_artist_id(conn, "KAREN MOK", cache), 2)
            self.assertEqual(_artist_id(conn, "Karen MOK", cache), 2)
            names = [row["name"] for row in conn.execute("SELECT name FROM artists ORDER BY id")]
        self.assertEqual(names, ["莫文蔚", "Karen Mok", "Somebody Else"])

    def test_a_genuinely_different_artist_is_still_created(self):
        with self._connect() as conn:
            new_id = _artist_id(conn, "Karen Williams", {})
            names = [row["name"] for row in conn.execute("SELECT name FROM artists ORDER BY id")]
        self.assertEqual(names, ["莫文蔚", "Karen Mok", "Somebody Else", "Karen Williams"])
        self.assertNotIn(new_id, (1, 2, 3))

    def test_artist_lists_and_track_search_match_aliases(self):
        with self._connect() as conn:
            merge_artists(conn, 1, [2])
            refresh_track_search_index(conn)
            conn.commit()
            page = fetch_artist_page(conn, search="karen", get_all_variants=get_all_variants)
            legacy = fetch_artists(conn, "Karen Mok", 50, 0, get_all_variants, to_simplified, to_traditional)
            tracks = search_tracks(conn, "karen mok", 50, get_all_variants, to_simplified, to_traditional)

        self.assertEqual([a["name"] for a in page["artists"]], ["莫文蔚"])
        self.assertEqual(page["total"], 1)
        self.assertEqual([a["name"] for a in legacy], ["莫文蔚"])
        self.assertEqual(sorted(t["id"] for t in tracks["tracks"]), [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
