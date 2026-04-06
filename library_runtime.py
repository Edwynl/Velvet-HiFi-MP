from __future__ import annotations

import sqlite3
from pathlib import Path


def record_track_play(db: sqlite3.Connection, track_id: int, duration_played: float | None) -> dict:
    db.execute(
        "INSERT INTO play_history (track_id, duration_played) VALUES (?, ?)",
        (track_id, duration_played),
    )
    db.commit()
    return {"ok": True}


def fetch_libraries(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute("SELECT * FROM libraries ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def add_library_path(db: sqlite3.Connection, path: str) -> None:
    db.execute("INSERT OR IGNORE INTO libraries (path) VALUES (?)", (path,))
    db.commit()


def remove_library_path(db: sqlite3.Connection, lib_id: int) -> None:
    db.execute("DELETE FROM libraries WHERE id=?", (lib_id,))
    db.commit()


def sync_music_dir_library(db: sqlite3.Connection, music_dir: str) -> None:
    if not music_dir:
        return
    add_library_path(db, music_dir)


def build_config_payload(
    db: sqlite3.Connection,
    music_dir: str,
    port: int,
    data_dir: Path | str,
) -> dict:
    libraries = fetch_libraries(db)
    if not libraries and music_dir:
        add_library_path(db, music_dir)
        libraries = fetch_libraries(db)

    return {
        "libraries": libraries,
        "music_dir": music_dir,
        "port": port,
        "data_dir": str(data_dir),
    }
