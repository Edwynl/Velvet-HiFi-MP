"""Recoverable album-file archival with same-volume atomic staging.

Never permanently unlink audio as part of a database mutation. Every archive
has a durable manifest under VELVET's data folder; a failed stage restores
files, and a failed SQL transaction restores the same staged files.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Iterable

from path_compat import filesystem_path


class ArchiveError(RuntimeError):
    pass


def _within_roots(source: Path, roots: list[Path]) -> bool:
    candidate = os.path.normcase(os.path.abspath(str(source)))
    for root in roots:
        safe_root = os.path.normcase(os.path.abspath(str(root)))
        try:
            if os.path.commonpath((candidate, safe_root)) == safe_root:
                return True
        except ValueError:
            continue
    return False


class AlbumFileArchive:
    def __init__(self, paths: Iterable[str], roots: list[Path], archive_dir: Path):
        self.token = uuid.uuid4().hex
        self.manifest_path = Path(archive_dir) / (self.token + ".json")
        self.entries: list[dict] = []
        self.state = "pending"
        sources = list(dict.fromkeys(str(p) for p in paths))
        if not sources:
            raise ArchiveError("This album has no registered audio files")
        for index, original in enumerate(sources):
            readable = Path(filesystem_path(original))
            if not readable.is_file():
                raise ArchiveError(f"Audio file missing; no files moved: {original}")
            if not _within_roots(readable.resolve(), [
                Path(filesystem_path(root)).resolve() for root in roots
            ]):
                raise ArchiveError("Refusing to move a file outside configured libraries")
            original_path = Path(original)
            archive_name = f".velvet-recycle-{self.token}-{index}{original_path.suffix}"
            staged = str(original_path.with_name(archive_name))
            if os.path.exists(filesystem_path(staged)):
                raise ArchiveError("Archive destination already exists")
            self.entries.append({"original": original, "staged": staged, "moved": False})

    def _write_manifest(self):
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        body = {"token": self.token, "state": self.state, "files": self.entries}
        tmp = self.manifest_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.manifest_path)

    def stage(self):
        self._write_manifest()
        try:
            for entry in self.entries:
                os.replace(filesystem_path(entry["original"]), filesystem_path(entry["staged"]))
                entry["moved"] = True
                self._write_manifest()
        except Exception as exc:
            try:
                self.restore()
            except Exception as restore_exc:
                raise ArchiveError(
                    f"Archive failed and recovery is needed; see {self.manifest_path}: {restore_exc}"
                ) from exc
            raise ArchiveError(f"Cannot archive album files; original paths restored: {exc}") from exc

    def commit(self):
        self.state = "archived"
        self._write_manifest()

    def restore(self):
        errors = []
        for entry in reversed(self.entries):
            staged, original = entry["staged"], entry["original"]
            if not os.path.isfile(filesystem_path(staged)):
                continue
            if os.path.exists(filesystem_path(original)):
                errors.append(f"Destination exists: {original}")
                continue
            try:
                os.replace(filesystem_path(staged), filesystem_path(original))
                entry["moved"] = False
            except OSError as exc:
                errors.append(f"{original}: {exc}")
        self.state = "recovery_required" if errors else "restored"
        self._write_manifest()
        if errors:
            raise ArchiveError("; ".join(errors))


def list_album_archives(archive_dir: Path) -> list[dict]:
    base = Path(archive_dir)
    if not base.is_dir():
        return []
    rows = []
    for path in sorted(base.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("state") in ("archived", "recovery_required", "pending"):
                rows.append({"token": path.stem, "state": data["state"], "files": len(data.get("files", []))})
        except (ValueError, OSError, KeyError):
            continue
    return rows


def restore_album_files(archive_dir: Path, token: str) -> dict:
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ArchiveError("Invalid archive token")
    manifest = Path(archive_dir) / (token + ".json")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArchiveError("Archive manifest not found or damaged") from exc
    archive = object.__new__(AlbumFileArchive)
    archive.token = token
    archive.manifest_path = manifest
    archive.entries = data["files"]
    archive.state = data["state"]
    # No file outside the original staged path is touched; exact token-specific
    # names prevent an attacker-controlled manifest from silently restoring
    # arbitrary paths. Do not expose write access to the data directory.
    for idx, entry in enumerate(archive.entries):
        staged_path = Path(entry["staged"])
        if staged_path.name != f".velvet-recycle-{token}-{idx}{Path(entry['original']).suffix}" or (
            staged_path.parent != Path(entry["original"]).parent
        ):
            raise ArchiveError("Invalid archived path")
    archive.restore()
    return {"restored": sum(not entry["moved"] for entry in archive.entries),
            "message": "Files restored; re-scan the library to rebuild the album index."}
