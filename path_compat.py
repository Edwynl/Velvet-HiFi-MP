from __future__ import annotations

import os


def as_windows_extended_path(path: str | os.PathLike[str]) -> str:
    """Return a Windows extended-length path without changing its stored form."""
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        return value

    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def filesystem_path(path: str | os.PathLike[str]) -> str:
    """Return a path suitable for OS file access on the current platform."""
    value = os.fspath(path)
    return as_windows_extended_path(value) if os.name == "nt" else value
