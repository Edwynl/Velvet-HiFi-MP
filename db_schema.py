import json
import sqlite3

from dsp_engine import FACTORY_DSP_PRESETS, legacy_eq_bands_to_filters


LATEST_SCHEMA_VERSION = 8


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}


def create_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artists (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            sort_name   TEXT,
            mbid        TEXT,
            bio         TEXT,
            image_path  TEXT,
            created_at  REAL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS albums (
            id           INTEGER PRIMARY KEY,
            artist_id    INTEGER REFERENCES artists(id) ON DELETE CASCADE,
            title        TEXT NOT NULL,
            year         INTEGER,
            genre        TEXT,
            label        TEXT,
            mbid         TEXT,
            cover_path   TEXT,
            total_tracks INTEGER,
            created_at   REAL DEFAULT (strftime('%s', 'now')),
            is_favorite  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tracks (
            id           INTEGER PRIMARY KEY,
            album_id     INTEGER REFERENCES albums(id) ON DELETE CASCADE,
            artist_id    INTEGER REFERENCES artists(id),
            title        TEXT NOT NULL,
            composer     TEXT,
            work_title   TEXT,
            performer    TEXT,
            conductor    TEXT,
            ensemble     TEXT,
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
            date_added   REAL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS play_history (
            id              INTEGER PRIMARY KEY,
            track_id        INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            played_at       REAL DEFAULT (strftime('%s', 'now')),
            duration_played REAL
        );

        CREATE TABLE IF NOT EXISTS libraries (
            id          INTEGER PRIMARY KEY,
            path        TEXT NOT NULL UNIQUE,
            created_at  REAL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS track_artists (
            track_id    INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            artist_id   INTEGER REFERENCES artists(id) ON DELETE CASCADE,
            PRIMARY KEY (track_id, artist_id)
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT,
            created_at  REAL DEFAULT (strftime('%s', 'now')),
            updated_at  REAL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS playlist_tracks (
            id          INTEGER PRIMARY KEY,
            playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
            track_id    INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            position    INTEGER NOT NULL,
            added_at    REAL DEFAULT (strftime('%s', 'now')),
            UNIQUE(playlist_id, track_id)
        );

        CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist ON playlist_tracks(playlist_id);
        CREATE INDEX IF NOT EXISTS idx_playlist_tracks_track ON playlist_tracks(track_id);

        CREATE TABLE IF NOT EXISTS dsp_profiles (
            id               INTEGER PRIMARY KEY,
            name             TEXT NOT NULL,
            description      TEXT,
            is_default       INTEGER DEFAULT 0,
            eq_bands         TEXT,
            volume_normalize INTEGER DEFAULT 0,
            stereo_width     INTEGER DEFAULT 0,
            reverb_room_size REAL DEFAULT 0,
            reverb_wet_dry   REAL DEFAULT 0,
            created_at       REAL DEFAULT (strftime('%s', 'now')),
            updated_at       REAL DEFAULT (strftime('%s', 'now'))
        );

        INSERT OR IGNORE INTO dsp_profiles (id, name, description, is_default)
        VALUES (1, 'Flat', 'No processing - original sound', 1);
        """
    )


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at REAL DEFAULT (strftime('%s', 'now'))
        )
        """
    )


def migration_001_create_base_schema(conn: sqlite3.Connection) -> None:
    create_base_schema(conn)


def migration_002_add_album_favorites(conn: sqlite3.Connection) -> None:
    album_columns = _table_columns(conn, "albums")
    album_defaults = {
        "artist_id": "INTEGER REFERENCES artists(id) ON DELETE CASCADE",
        "year": "INTEGER",
        "genre": "TEXT",
        "label": "TEXT",
        "mbid": "TEXT",
        "cover_path": "TEXT",
        "total_tracks": "INTEGER",
        "created_at": "REAL DEFAULT (strftime('%s', 'now'))",
        "is_favorite": "INTEGER DEFAULT 0",
    }
    for column_name, column_sql in album_defaults.items():
        if column_name not in album_columns:
            conn.execute(f"ALTER TABLE albums ADD COLUMN {column_name} {column_sql}")


def migration_003_add_track_credit_fields(conn: sqlite3.Connection) -> None:
    track_columns = _table_columns(conn, "tracks")
    for column_name in ("composer", "work_title", "performer", "conductor", "ensemble"):
        if column_name not in track_columns:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {column_name} TEXT")


def migration_004_create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title_lower ON tracks(LOWER(title))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artists_name_lower ON artists(LOWER(name))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_year ON albums(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_track ON play_history(track_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_played ON play_history(played_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_play_history_date ON play_history(played_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_composer_lower ON tracks(LOWER(composer))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_work_lower ON tracks(LOWER(work_title))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_performer_lower ON tracks(LOWER(performer))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_conductor_lower ON tracks(LOWER(conductor))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_ensemble_lower ON tracks(LOWER(ensemble))")


def migration_005_add_lookup_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_file_path ON tracks(file_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_title ON albums(title)")


def _seed_factory_dsp_presets(conn: sqlite3.Connection) -> None:
    for preset in FACTORY_DSP_PRESETS:
        conn.execute(
            """
            INSERT OR IGNORE INTO dsp_profiles (
                id, name, description, is_default, eq_bands, volume_normalize,
                stereo_width, reverb_room_size, reverb_wet_dry, category,
                preset_key, is_factory, preamp_db, filter_chain_json, tags, sort_order
            ) VALUES (?, ?, ?, 0, NULL, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preset["id"],
                preset["name"],
                preset["description"],
                preset["category"],
                preset["preset_key"],
                preset["is_factory"],
                preset["preamp_db"],
                preset["filter_chain_json"],
                preset["tags"],
                preset["sort_order"],
            ),
        )


def migration_006_enhance_dsp_profiles(conn: sqlite3.Connection) -> None:
    dsp_columns = _table_columns(conn, "dsp_profiles")
    column_defaults = {
        "category": "TEXT NOT NULL DEFAULT 'custom'",
        "preset_key": "TEXT",
        "is_factory": "INTEGER DEFAULT 0",
        "preamp_db": "REAL DEFAULT 0",
        "filter_chain_json": "TEXT",
        "tags": "TEXT",
        "sort_order": "INTEGER DEFAULT 0",
    }
    for column_name, column_sql in column_defaults.items():
        if column_name not in dsp_columns:
            conn.execute(f"ALTER TABLE dsp_profiles ADD COLUMN {column_name} {column_sql}")

    rows = conn.execute("SELECT id, eq_bands, filter_chain_json FROM dsp_profiles").fetchall()
    for row in rows:
        if row["filter_chain_json"]:
            continue
        filters = legacy_eq_bands_to_filters(row["eq_bands"])
        if not filters:
            continue
        conn.execute(
            "UPDATE dsp_profiles SET filter_chain_json=? WHERE id=?",
            (json.dumps(filters), row["id"]),
        )

    conn.execute(
        """
        UPDATE dsp_profiles
        SET category='reference',
            preset_key='flat',
            is_factory=1,
            preamp_db=0,
            tags='["reference","flat"]',
            sort_order=0
        WHERE id=1
        """
    )

    _seed_factory_dsp_presets(conn)


def migration_007_seed_expanded_factory_dsp_presets(conn: sqlite3.Connection) -> None:
    _seed_factory_dsp_presets(conn)


def migration_008_add_performance_indexes(conn: sqlite3.Connection) -> None:
    # Accelerate artist-centric joins and counts.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_track_artists_artist ON track_artists(artist_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_track_artists_artist_track ON track_artists(artist_id, track_id)")

    # Accelerate album/track ordering and track listings.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tracks_album_disc_track ON tracks(album_id, disc_number, track_number)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_date_added ON tracks(date_added DESC)")

    # Accelerate discovery/favorites/genre browsing.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_created_at ON albums(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_favorite_created ON albums(is_favorite, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_genre ON albums(genre)")

    # Accelerate recent history queries and per-track play history.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_play_history_track_played ON play_history(track_id, played_at DESC)")


MIGRATIONS = (
    (1, "create_base_schema", migration_001_create_base_schema),
    (2, "align_album_columns", migration_002_add_album_favorites),
    (3, "add_track_credit_fields", migration_003_add_track_credit_fields),
    (4, "create_indexes", migration_004_create_indexes),
    (5, "add_lookup_indexes", migration_005_add_lookup_indexes),
    (6, "enhance_dsp_profiles", migration_006_enhance_dsp_profiles),
    (7, "seed_expanded_factory_dsp_presets", migration_007_seed_expanded_factory_dsp_presets),
    (8, "add_performance_indexes", migration_008_add_performance_indexes),
)


def get_applied_migration_versions(conn: sqlite3.Connection) -> set[int]:
    ensure_migrations_table(conn)
    return {
        row["version"] if isinstance(row, sqlite3.Row) else row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }


def get_schema_version(conn: sqlite3.Connection) -> int:
    ensure_migrations_table(conn)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return row[0] if row else 0


def apply_migrations(conn: sqlite3.Connection) -> int:
    ensure_migrations_table(conn)
    applied_versions = get_applied_migration_versions(conn)

    for version, name, migration in MIGRATIONS:
        if version in applied_versions:
            continue
        migration(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, name),
        )

    conn.commit()
    return get_schema_version(conn)


def init_db(conn: sqlite3.Connection | None = None) -> int:
    if conn is not None:
        return apply_migrations(conn)

    from db_pool import DatabaseConnection

    with DatabaseConnection() as managed_conn:
        return apply_migrations(managed_conn)
