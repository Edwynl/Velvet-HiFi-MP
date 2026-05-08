import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_schema import LATEST_SCHEMA_VERSION, apply_migrations, get_schema_version


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "library.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def test_apply_migrations_upgrades_legacy_tables(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE albums (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL
                );

                CREATE TABLE tracks (
                    id           INTEGER PRIMARY KEY,
                    album_id     INTEGER,
                    artist_id    INTEGER,
                    title        TEXT NOT NULL,
                    track_number INTEGER,
                    disc_number  INTEGER DEFAULT 1,
                    duration     REAL,
                    file_path    TEXT UNIQUE NOT NULL,
                    format       TEXT,
                    sample_rate  INTEGER,
                    bit_depth    INTEGER,
                    channels     INTEGER,
                    bitrate      INTEGER,
                    file_size    INTEGER,
                    date_added   REAL
                );
                """
            )
            conn.commit()

            applied_version = apply_migrations(conn)

            album_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(albums)").fetchall()
            }
            track_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
            }

        self.assertEqual(applied_version, LATEST_SCHEMA_VERSION)
        self.assertIn("is_favorite", album_columns)
        self.assertTrue(
            {"composer", "work_title", "performer", "conductor", "ensemble"}.issubset(track_columns)
        )

    def test_apply_migrations_is_idempotent(self):
        with self._connect() as conn:
            first_version = apply_migrations(conn)
            second_version = apply_migrations(conn)
            migration_rows = conn.execute("SELECT COUNT(*) AS count FROM schema_migrations").fetchone()
        with self._connect() as verification_conn:
            current_version = get_schema_version(verification_conn)

        self.assertEqual(first_version, LATEST_SCHEMA_VERSION)
        self.assertEqual(second_version, LATEST_SCHEMA_VERSION)
        self.assertEqual(current_version, LATEST_SCHEMA_VERSION)
        self.assertEqual(migration_rows["count"], LATEST_SCHEMA_VERSION)

    def test_apply_migrations_enhances_dsp_profiles_and_seeds_factory_presets(self):
        with self._connect() as conn:
            apply_migrations(conn)
            dsp_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(dsp_profiles)").fetchall()
            }
            profiles = conn.execute(
                "SELECT preset_key, category, is_factory, sort_order FROM dsp_profiles ORDER BY id"
            ).fetchall()

        self.assertTrue(
            {"category", "preset_key", "is_factory", "preamp_db", "filter_chain_json", "tags", "sort_order"}.issubset(dsp_columns)
        )
        self.assertEqual(profiles[0]["preset_key"], "flat")
        self.assertEqual(profiles[0]["category"], "reference")
        self.assertEqual(profiles[0]["is_factory"], 1)
        self.assertEqual(
            {row["preset_key"] for row in profiles[1:]},
            {
                "black_background",
                "reference_neutral",
                "vocal_forward_warm",
                "vocal_forward_cool",
                "bass_boost",
                "sub_bass_boost",
                "pop",
                "classical",
            },
        )


if __name__ == "__main__":
    unittest.main()
