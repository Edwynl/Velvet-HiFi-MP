import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

from db_schema import apply_migrations
from library_mutation import (
    add_track_to_playlist,
    create_playlist,
    delete_playlist,
    fetch_playlist_detail,
    fetch_playlists,
    remove_track_from_playlist,
    toggle_album_favorite,
    update_playlist,
)


class LibraryMutationTests(unittest.TestCase):
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
        conn.execute("PRAGMA foreign_keys=ON")
        return closing(conn)

    def _seed(self, conn: sqlite3.Connection):
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (1, 'Artist One', 'Artist One')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (2, 'Artist Two', 'Artist Two')")
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, is_favorite)
            VALUES
                (1, 1, 'Album One', 2001, 0),
                (2, 2, 'Album Two', 2002, 1)
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path)
            VALUES
                (1, 1, 1, 'Track One', 180, 'C:/music/one.flac'),
                (2, 2, 2, 'Track Two', 200, 'C:/music/two.flac')
            """
        )
        conn.commit()

    def test_toggle_album_favorite_flips_album_state(self):
        with self._connect() as conn:
            result = toggle_album_favorite(conn, 1)

        self.assertEqual(result, {"success": True, "is_favorite": 1})

        with self._connect() as conn:
            stored = conn.execute("SELECT is_favorite FROM albums WHERE id=1").fetchone()

        self.assertEqual(stored["is_favorite"], 1)

    def test_toggle_album_favorite_raises_for_missing_album(self):
        with self._connect() as conn:
            with self.assertRaises(HTTPException) as ctx:
                toggle_album_favorite(conn, 999)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_create_and_list_playlists_includes_track_counts(self):
        with self._connect() as conn:
            created = create_playlist(conn, "Morning Mix", "Start the day")
            playlists = fetch_playlists(conn)

        self.assertEqual(created["name"], "Morning Mix")
        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0]["track_count"], 0)

    def test_fetch_playlist_detail_returns_tracks_in_order(self):
        with self._connect() as conn:
            playlist = create_playlist(conn, "Ordered", "")
            add_track_to_playlist(conn, playlist["id"], 1)
            add_track_to_playlist(conn, playlist["id"], 2)
            detail = fetch_playlist_detail(conn, playlist["id"])

        self.assertEqual([track["title"] for track in detail["tracks"]], ["Track One", "Track Two"])
        self.assertEqual(detail["tracks"][0]["artist_name"], "Artist One")
        self.assertEqual(detail["tracks"][1]["album_title"], "Album Two")

    def test_add_and_remove_playlist_tracks_resequences_positions(self):
        with self._connect() as conn:
            playlist = create_playlist(conn, "Flow", "")
            added_first = add_track_to_playlist(conn, playlist["id"], 1)
            added_duplicate = add_track_to_playlist(conn, playlist["id"], 1)
            added_second = add_track_to_playlist(conn, playlist["id"], 2)
            removed = remove_track_from_playlist(conn, playlist["id"], 1)
            rows = conn.execute(
                "SELECT track_id, position FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                (playlist["id"],),
            ).fetchall()

        self.assertEqual(added_first, {"success": True})
        self.assertEqual(added_duplicate, {"success": False})
        self.assertEqual(added_second, {"success": True})
        self.assertEqual(removed, {"success": True})
        self.assertEqual([(row["track_id"], row["position"]) for row in rows], [(2, 1)])

    def test_update_and_delete_playlist_persist_changes(self):
        with self._connect() as conn:
            playlist = create_playlist(conn, "Old Name", "Old Description")
            update_result = update_playlist(conn, playlist["id"], name="New Name", description="New Description")
            updated = conn.execute("SELECT name, description FROM playlists WHERE id=?", (playlist["id"],)).fetchone()
            delete_result = delete_playlist(conn, playlist["id"])
            deleted = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist["id"],)).fetchone()

        self.assertEqual(update_result, {"success": True})
        self.assertEqual(updated["name"], "New Name")
        self.assertEqual(updated["description"], "New Description")
        self.assertEqual(delete_result, {"success": True})
        self.assertIsNone(deleted)


if __name__ == "__main__":
    unittest.main()
