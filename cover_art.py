from __future__ import annotations

import hashlib
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
    for name in COMMON_COVER_FILENAMES:
        path = folder / name
        if path.exists():
            try:
                return path.read_bytes()
            except Exception as exc:
                if logger:
                    logger.debug(f"Failed to read folder cover {path}: {exc}")
    return None


def save_cover_bytes(cover_data: bytes, key: str, covers_dir: Path) -> str:
    """Save cover art bytes to disk and return the absolute path."""
    digest = hashlib.md5(key.encode()).hexdigest()
    path = covers_dir / f"{digest}.jpg"
    if not path.exists():
        path.write_bytes(cover_data)
    return str(path)
