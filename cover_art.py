from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from path_compat import filesystem_path


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
    folder = Path(filesystem_path(file_path)).parent
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
    # "Album\CD1\track.flac" keeps its artwork in "Album".
    if DISC_FOLDER.match(folder.name):
        return _resolve_folder_cover_path(str(folder.parent))
    for subfolder in ARTWORK_SUBFOLDERS:
        found = _any_image(folder / subfolder)
        if found:
            return found
    return _any_image(folder)


DISC_FOLDER = re.compile(r"^(?:cd|disc|disk)\s*[-_.]?\s*\d{1,2}\b", re.I)
ARTWORK_SUBFOLDERS = ("Artwork", "Covers", "Cover", "Scans")
_FRONT_HINT = re.compile(r"cover|front|folder|封面", re.I)
_NOT_FRONT_HINT = re.compile(r"back|inlay|tray|booklet|disc|cd\d|inside|背", re.I)


def _any_image(folder: Path) -> Optional[str]:
    """Pick the likeliest front cover among arbitrarily named images."""
    try:
        images = [
            entry for entry in folder.iterdir()
            if entry.suffix.lower() in (".jpg", ".jpeg", ".png")
            and not entry.name.startswith("._") and entry.is_file()
        ]
    except OSError:
        return None
    if not images:
        return None
    images.sort(key=lambda p: (
        0 if _FRONT_HINT.search(p.stem) else 2 if _NOT_FRONT_HINT.search(p.stem) else 1,
        p.name.lower(),
    ))
    return str(images[0])


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
