from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional


COMMON_COVER_FILENAMES = (
    "cover.jpg",
    "folder.jpg",
    "front.jpg",
    "albumart.jpg",
    "album.jpg",
    "cover.png",
    "folder.png",
    "front.png",
    "AlbumArt.jpg",
    "Cover.jpg",
)


def find_folder_cover(file_path: str, logger=None) -> Optional[bytes]:
    """Look for common cover filenames in the same folder."""
    folder = Path(file_path).parent
    cover_path = _resolve_folder_cover_path(str(folder))
    if not cover_path:
        return None

    path = Path(cover_path)
    try:
        return path.read_bytes()
    except Exception as exc:
        if logger:
            logger.debug(f"Failed to read folder cover {path}: {exc}")
        # File may have changed since cache was populated.
        _resolve_folder_cover_path.cache_clear()
    return None


@lru_cache(maxsize=8192)
def _resolve_folder_cover_path(folder_path: str) -> Optional[str]:
    """Cache cover file discovery per folder to reduce repeated stat calls."""
    folder = Path(folder_path)
    for name in COMMON_COVER_FILENAMES:
        path = folder / name
        if path.exists():
            return str(path)
    return None


def save_cover_bytes(cover_data: bytes, key: str, covers_dir: Path) -> str:
    """Save cover art bytes to disk and return the absolute path."""
    if not cover_data:
        return ""
    covers_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(key.encode()).hexdigest()
    path = covers_dir / f"{digest}.jpg"
    if not path.exists():
        path.write_bytes(cover_data)
    return str(path)
