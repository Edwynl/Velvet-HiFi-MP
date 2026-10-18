import json
import sqlite3

from dsp_engine import FACTORY_DSP_PRESETS, legacy_eq_bands_to_filters


LATEST_SCHEMA_VERSION = 14


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



def migration_009_track_file_state_and_query_indexes(conn: sqlite3.Connection) -> None:
    """Track incremental file revisions without altering track IDs or history.

    Existing libraries are deliberately NOT backfilled with guesses: the next
    scan takes a baseline stat snapshot for existing tracks. Following scans
    can then detect a changed file without deleting its playlist/history rows.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS track_file_state (
            track_id       INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
            size_bytes     INTEGER NOT NULL,
            mtime_ns       INTEGER NOT NULL,
            sha256         TEXT,
            hash_size      INTEGER,
            hash_mtime_ns  INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_duplicate_size ON tracks(file_size, id)")
    # History browse orders by played_at and then joins track_id.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_recent_track ON play_history(played_at DESC, track_id)"
    )
    # High resolution album browsing accesses the album/rate/depth together.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tracks_album_quality ON tracks(album_id, sample_rate DESC, bit_depth DESC)"
    )


def migration_010_track_search_index(conn: sqlite3.Connection) -> None:
    """Precomputed search keys (see library_search_index.py).

    The keys need Python (simplified-Chinese folding), so the triggers only
    queue changed tracks in track_search_dirty; any writer stays correct
    without knowing about the index. The table starts empty and unsigned:
    search uses the exact path until refresh_track_search_index() builds it.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS track_search_index (
            track_id    INTEGER PRIMARY KEY,
            search_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS track_search_terms (
            term     TEXT NOT NULL,
            track_id INTEGER NOT NULL,
            PRIMARY KEY (term, track_id)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS idx_track_search_terms_track ON track_search_terms(track_id);
        CREATE TABLE IF NOT EXISTS track_search_dirty (
            track_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS search_index_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TRIGGER IF NOT EXISTS trg_search_track_insert AFTER INSERT ON tracks
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id) VALUES (NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_track_update
        AFTER UPDATE OF id, title, artist_id, album_id, composer, work_title,
                        performer, conductor, ensemble ON tracks
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id) VALUES (OLD.id), (NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_track_delete AFTER DELETE ON tracks
        BEGIN
            DELETE FROM track_search_index WHERE track_id = OLD.id;
            DELETE FROM track_search_terms WHERE track_id = OLD.id;
            DELETE FROM track_search_dirty WHERE track_id = OLD.id;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_search_track_artists_insert AFTER INSERT ON track_artists
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id) VALUES (NEW.track_id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_track_artists_update AFTER UPDATE ON track_artists
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id) VALUES (OLD.track_id), (NEW.track_id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_track_artists_delete AFTER DELETE ON track_artists
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id) VALUES (OLD.track_id);
        END;

        CREATE TRIGGER IF NOT EXISTS trg_search_artist_insert AFTER INSERT ON artists
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE artist_id = NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_artist_update AFTER UPDATE OF id, name ON artists
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE artist_id IN (OLD.id, NEW.id);
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT track_id FROM track_artists WHERE artist_id IN (OLD.id, NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_artist_delete AFTER DELETE ON artists
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE artist_id = OLD.id;
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT track_id FROM track_artists WHERE artist_id = OLD.id;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_search_album_insert AFTER INSERT ON albums
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE album_id = NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_album_update AFTER UPDATE OF id, title ON albums
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE album_id IN (OLD.id, NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_album_delete AFTER DELETE ON albums
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE album_id = OLD.id;
        END;
        """
    )


def migration_011_artist_aliases(conn: sqlite3.Connection) -> None:
    """Other names of one artist (English, pinyin, other script).

    A merged artist keeps one row whose name is the display name; the names
    of the rows merged into it live here, so the artist page can list them,
    search finds them, and a rescan that reads an old name from a file tag
    resolves to the same artist instead of recreating the duplicate.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artist_aliases (
            id         INTEGER PRIMARY KEY,
            artist_id  INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_artist_aliases_artist ON artist_aliases(artist_id);

        CREATE TRIGGER IF NOT EXISTS trg_search_alias_insert AFTER INSERT ON artist_aliases
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT track_id FROM track_artists WHERE artist_id = NEW.artist_id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_alias_update AFTER UPDATE ON artist_aliases
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT track_id FROM track_artists WHERE artist_id IN (OLD.artist_id, NEW.artist_id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_search_alias_delete AFTER DELETE ON artist_aliases
        BEGIN
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT track_id FROM track_artists WHERE artist_id = OLD.artist_id;
        END;
        """
    )



def migration_012_opencc_catalog_search(conn: sqlite3.Connection) -> None:
    """Dirty-queued OpenCC keys for Artists and Albums, including artist aliases.

    SQLite triggers only enqueue IDs; OpenCC conversion always happens in
    Python during refresh. Old names and track IDs remain untouched.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_search_index (
            kind         TEXT NOT NULL CHECK(kind IN ('artist','album')),
            entity_id    INTEGER NOT NULL,
            search_text  TEXT NOT NULL,
            PRIMARY KEY(kind,entity_id)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS catalog_search_dirty (
            kind         TEXT NOT NULL CHECK(kind IN ('artist','album')),
            entity_id    INTEGER NOT NULL,
            PRIMARY KEY(kind,entity_id)
        ) WITHOUT ROWID;
        INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id)
            SELECT 'artist',id FROM artists;
        INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id)
            SELECT 'album',id FROM albums;

        CREATE TRIGGER IF NOT EXISTS trg_v2_artist_insert AFTER INSERT ON artists
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_artist_update
        AFTER UPDATE OF id,name ON artists
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',OLD.id);
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',NEW.id);
            INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id)
                SELECT 'album',id FROM albums WHERE artist_id IN (OLD.id,NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_artist_delete AFTER DELETE ON artists
        BEGIN
            DELETE FROM catalog_search_index WHERE kind='artist' AND entity_id=OLD.id;
            DELETE FROM catalog_search_dirty WHERE kind='artist' AND entity_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_album_insert AFTER INSERT ON albums
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('album',NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_album_update
        AFTER UPDATE OF id,title,artist_id ON albums
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('album',OLD.id);
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('album',NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_album_delete AFTER DELETE ON albums
        BEGIN
            DELETE FROM catalog_search_index WHERE kind='album' AND entity_id=OLD.id;
            DELETE FROM catalog_search_dirty WHERE kind='album' AND entity_id=OLD.id;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_v2_alias_insert AFTER INSERT ON artist_aliases
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',NEW.artist_id);
            INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id)
                SELECT 'album',id FROM albums WHERE artist_id=NEW.artist_id;
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE artist_id=NEW.artist_id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_alias_update AFTER UPDATE ON artist_aliases
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',OLD.artist_id);
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',NEW.artist_id);
            INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id)
                SELECT 'album',id FROM albums WHERE artist_id IN (OLD.artist_id,NEW.artist_id);
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE artist_id IN (OLD.artist_id,NEW.artist_id);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_v2_alias_delete AFTER DELETE ON artist_aliases
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('artist',OLD.artist_id);
            INSERT OR IGNORE INTO catalog_search_dirty(kind,entity_id)
                SELECT 'album',id FROM albums WHERE artist_id=OLD.artist_id;
            INSERT OR IGNORE INTO track_search_dirty(track_id)
                SELECT id FROM tracks WHERE artist_id=OLD.artist_id;
        END;
        """
    )


def migration_013_track_title_key(conn: sqlite3.Connection) -> None:
    """Add the folded track title and album genre to the search index.

    Without a normalized title column, ORDER BY can only compare the raw
    `LOWER(title)`, which cannot see that a Simplified query matches a
    Traditional title exactly. A query for 爱你 therefore ranked 愛 (matched
    only by the reverse-substring clause) above 愛你 (the exact match).

    genre_key keeps album *search* results unchanged while letting the track
    browse match a Simplified genre query against a Traditional stored label.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(track_search_index)")}
    if "title_key" not in columns:
        conn.execute("ALTER TABLE track_search_index ADD COLUMN title_key TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_track_search_title_key "
        "ON track_search_index(title_key)"
    )

    # Genre is stored separately from search_text so that making genre matching
    # script-aware does not start letting album *search* match on genre, which
    # would change artist/album search results.
    catalog_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(catalog_search_index)")
    }
    if catalog_columns and "genre_key" not in catalog_columns:
        conn.execute("ALTER TABLE catalog_search_index ADD COLUMN genre_key TEXT")

    # genre_key only stays current if a genre edit re-queues the album.
    # Migration 12's trigger watched id/title/artist_id but not genre.
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS trg_v2_album_update;
        CREATE TRIGGER trg_v2_album_update
        AFTER UPDATE OF id,title,artist_id,genre ON albums
        BEGIN
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('album',OLD.id);
            INSERT OR IGNORE INTO catalog_search_dirty VALUES('album',NEW.id);
        END;
        """
    )



def migration_014_artist_alias_enrichment(conn: sqlite3.Connection) -> None:
    """Durable, per-artist MusicBrainz alias-enrichment checkpoints.

    No audio metadata or artist names are rewritten. Unfinished requests have
    no checkpoint, so the next user-initiated batch resumes automatically.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artist_alias_enrichment_state (
            artist_id       INTEGER PRIMARY KEY
                            REFERENCES artists(id) ON DELETE CASCADE,
            status          TEXT NOT NULL,
            aliases_added   INTEGER NOT NULL DEFAULT 0,
            attempted_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
            error           TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_artist_alias_enrichment_status
            ON artist_alias_enrichment_state(status, attempted_at);
        CREATE TRIGGER IF NOT EXISTS trg_alias_enrichment_artist_rename
        AFTER UPDATE OF name, mbid ON artists
        BEGIN
            DELETE FROM artist_alias_enrichment_state WHERE artist_id=NEW.id;
        END;
        """
    )


MIGRATIONS = (
    (1, "create_base_schema", migration_001_create_base_schema),
    (2, "align_album_columns", migration_002_add_album_favorites),
    (3, "add_track_credit_fields", migration_003_add_track_credit_fields),
    (4, "create_indexes", migration_004_create_indexes),
    (5, "add_lookup_indexes", migration_005_add_lookup_indexes),
    (6, "enhance_dsp_profiles", migration_006_enhance_dsp_profiles),
    (7, "seed_expanded_factory_dsp_presets", migration_007_seed_expanded_factory_dsp_presets),
    (8, "add_performance_indexes", migration_008_add_performance_indexes),
    (9, "track_file_state_and_query_indexes", migration_009_track_file_state_and_query_indexes),
    (10, "track_search_index", migration_010_track_search_index),
    (11, "artist_aliases", migration_011_artist_aliases),
    (12, "opencc_catalog_search", migration_012_opencc_catalog_search),
    (13, "track_title_key", migration_013_track_title_key),
    (14, "artist_alias_enrichment", migration_014_artist_alias_enrichment),
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
