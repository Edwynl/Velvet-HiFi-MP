"""Folder-aware album grouping.

Tags alone split albums apart: a compilation tagged without an album artist
becomes one album per singer, case variants of a title become two albums,
"CD1/CD2" become separate albums, and untagged files all land in one shared
"Unknown Album". The planner looks at every audio file of an album folder
together and returns per-file metadata overrides that keep the folder whole.
Track artists are never replaced when the tags name one.
"""
from __future__ import annotations

import collections
import functools
import ntpath
import re
from typing import Callable, Optional

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
VARIOUS_ARTISTS = "Various Artists"

_DISC_DIR = re.compile(r"^(?:cd|disc|disk)\s*[-_.]?\s*(\d{1,2})\b", re.I)
_DISC_IN_NAME = re.compile(r"(?:^|[^A-Za-z])CD\s*(\d{1,2})(?:\D|$)", re.I)
_MULTI_DISC_PARENT = re.compile(r"\d\s*CD", re.I)
_GENERIC_DIRS = {
    "music", "albums", "album", "singles", "downloads", "tidal", "roon music", "roon2",
    "cd wav cue", "flac", "wav", "mp3", "ape", "dsd", "dsf", "hi-res", "hires", "lossless",
    "专辑", "单曲", "无损",
}
_AD_MARK = re.compile(
    r"[\[【(（][^\]】)）]*?(?:\.(?:com|cn|net|org|cc)|分享|下载)[^\]】)）]*[\]】)）]", re.I,
)
_AD_WORDS = r"(?:分享|下载|资源|收藏|整理|无损控)"
_BARE_AD = re.compile(
    rf"^\s*{_AD_WORDS}*\s*(?:https?://)?(?:www\.)?[a-z0-9-]+\.(?:com|cn|net|org|cc)\s*{_AD_WORDS}*\s*$", re.I,
)
_INLINE_AD = re.compile(rf"\s*(?:https?://)?(?:www\.)?[a-z0-9-]+\.(?:com|cn|net|org|cc){_AD_WORDS}+", re.I)


def strip_ad_marks(value: Optional[str]) -> Optional[str]:
    """Drop download-site watermarks such as "古巨基[60yp.com分享]" or "wusunk.com"."""
    if not value:
        return value
    if _BARE_AD.match(value):
        return None
    cleaned = _INLINE_AD.sub("", _AD_MARK.sub("", value)).strip()
    if cleaned != value.strip() and re.fullmatch(rf"{_AD_WORDS}*", cleaned):
        return None  # only watermark words were left, e.g. "无损控资源[wusunk.com]"
    return cleaned or None


def album_folder_of(file_path: str) -> tuple[str, Optional[int]]:
    """Return the folder that holds the whole album and the disc implied by a CD subfolder."""
    folder = ntpath.dirname(file_path)
    name = ntpath.basename(folder)
    parent = ntpath.dirname(folder)
    match = _DISC_DIR.match(name)
    if match:
        return parent, int(match.group(1))
    match = _DISC_IN_NAME.search(name)
    if match and _MULTI_DISC_PARENT.search(ntpath.basename(parent)):
        return parent, int(match.group(1))
    return folder, None


def split_disc_suffix(album: str) -> tuple[str, Optional[int]]:
    """'1989(Deluxe Edition 2CD).CD2' -> ('1989(Deluxe Edition)', 2)."""
    disc = None
    match = (
        re.match(r"^(.*?)[\s._-]*[(\[（]\s*(?:disc|disk|cd)\s*(\d{1,2})\s*[)\]）]\s*$", album, re.I)
        or re.match(r"^(.*?)[\s._-]*\b(?:disc|disk|cd)\s*(\d{1,2})\s*$", album, re.I)
    )
    base = album
    if match and match.group(1).strip():
        base, disc = match.group(1), int(match.group(2))
    base = re.sub(r"\s*[(（]\s*\d\s*CD\s*[)）]", "", base, flags=re.I)
    base = re.sub(r"\s+\d\s*CD(?=\s*[)）])", "", base, flags=re.I)
    return base.strip() or album, disc


def clean_folder_album_name(name: str) -> Optional[str]:
    """Best-effort album title from a download/rip folder name."""
    original = name.strip()
    s = re.sub(r"^\[(?:A|AE|E)\]\s*", "", original)  # Tidal explicit/atmos markers
    s = re.sub(r"\s*\[\d{6,}\]", "", s)  # Tidal album ID
    s = re.sub(r"\s*[\[(（]\s*(?:19|20)\d{2}\s*[\])）]", "", s)  # [2024] / (2020)
    s = re.sub(
        r"\s*[\[(（][^\])）]*?(?:flac|wav|ape|dsd|khz|bit|\dB-|分轨|整轨|cue|\dCD)[^\])）]*[\])）]",
        "", s, flags=re.I,
    )
    s = re.sub(r"[\[(（](?:单曲|精选)[\])）]", "", s)
    match = re.search(r"《\s*([^》]+?)\s*》", s)
    if match:
        s = match.group(1)
    s = re.sub(r"^(?:19|20)\d{2}(?:[-.]\d{1,2}[-.]\d{1,2})?\s*[-.]?\s*", "", s) or s
    parts = re.split(r"\s+-\s+", s)
    if len(parts) >= 2:
        s = parts[1]
    s = s.strip(" -_.")
    return s or original or None


def _folder_year(name: str) -> Optional[int]:
    match = (
        re.search(r"[\[(（]((?:19|20)\d{2})[\])）]", name)
        or re.match(r"^((?:19|20)\d{2})(?:\D|$)", name.strip())
    )
    return int(match.group(1)) if match else None


def _artist_from_folder_name(name: str) -> Optional[str]:
    name = re.sub(r"^\[(?:19|20)\d{2}\]\s*", "", name.strip())
    for pattern in (r"^(.+?)\.(?:19|20)\d{2}\s*-", r"^(.+?)\s*《", r"^(.+?)\s+-\s+.+"):
        match = re.match(pattern, name)
        if match and not re.fullmatch(r"[\d\s.\-\[\]]+", match.group(1)):
            return match.group(1).strip()
    return None


def _ancestor_names(album_folder: str) -> list[str]:
    """Nearest-first folder names above the album, skipping drive/root and format folders."""
    parts = [p for p in re.split(r"[\\/]", album_folder) if p]
    names = []
    for name in reversed(parts[2:-1]):
        lower = name.lower()
        if lower in _GENERIC_DIRS or lower.endswith("downloads") or "格式" in name:
            continue
        names.append(name)
    return names


def _parse_filename(file_path: str) -> tuple[Optional[int], list[str]]:
    """'02 - Artist - Title.m4a' -> (2, ['Artist', 'Title'])."""
    stem = ntpath.splitext(ntpath.basename(file_path))[0].strip()
    match = re.match(r"^[A-Za-z]?(\d{1,3})(?:\s*[-._)]\s*|\s+)(.+)$", stem)
    number = None
    if match:
        number, stem = int(match.group(1)), match.group(2)
    return number, [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]


def _title_key(title: str) -> str:
    """Compare titles ignoring case, spaces and punctuation."""
    return re.sub(r"[\W_]+", "", title.casefold()) or title.casefold()


def _known(value: Optional[str], unknown: str) -> Optional[str]:
    return value if value and value != unknown else None


def _strip_trailing_paren(name: str) -> str:
    return re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", name).strip() or name


def _choose_owner(names: list[str], album_folder: str,
                  sibling_artist: Callable[[str], Optional[str]]) -> str:
    """Album artist for a folder whose tracks credit different artists."""
    from scanner_service import split_artists  # scanner_service imports this module

    counts = collections.Counter(names)
    candidates = set()
    for name in counts:
        candidates.update({name, split_artists(name)[0], _strip_trailing_paren(name)})
    folded = [n.casefold() for n in names]

    def score(candidate: str) -> tuple[int, int]:
        key = candidate.casefold()
        return sum(1 for n in folded if key in n), counts.get(candidate, 0)

    best = max(sorted(candidates), key=score)
    if score(best)[0] * 2 > len(names):
        return best
    sibling = sibling_artist(album_folder)
    if sibling and any(sibling.casefold() in n for n in folded):
        return sibling
    for ancestor in _ancestor_names(album_folder):
        for candidate in candidates:
            if candidate.casefold() == ancestor.casefold():
                return candidate
    return VARIOUS_ARTISTS


def plan_album_folder(
    entries: list[tuple[str, dict]],
    *,
    sibling_artist: Callable[[str], Optional[str]],
) -> dict[str, dict]:
    """Return {file_path: metadata overrides} for every file of one album folder.

    ``sibling_artist(album_folder)`` names the artist that owns most other
    albums next to this folder (e.g. the artist folder), or None.
    """
    plans: dict[str, dict] = {path: {} for path, _ in entries}
    if not entries:
        return plans
    album_folder = album_folder_of(entries[0][0])[0]
    folder_name = ntpath.basename(album_folder)

    # 1. Album titles: merge case variants and "CD1/CD2" suffixes.
    keys: dict[str, str] = {}
    display: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for path, meta in entries:
        plan = plans[path]
        _, folder_disc = album_folder_of(path)
        album = _known(meta.get("album"), UNKNOWN_ALBUM)
        title_disc = None
        if album:
            base, title_disc = split_disc_suffix(album)
            keys[path] = _title_key(base)
            display[keys[path]][base] += 1
        disc = folder_disc or title_disc
        if disc and disc != 1 and (meta.get("disc_number") or 1) == 1:
            plan["disc_number"] = disc
    # "Remember" next to "2nd Album : Remember" in one folder is the same release.
    by_size = sorted(display, key=lambda k: -sum(display[k].values()))
    for index, minor in enumerate(by_size):
        for major in by_size[:index]:
            if major in display and min(len(major), len(minor)) >= 4 and (minor in major or major in minor):
                display[major].update(display.pop(minor))
                keys.update({path: major for path, key in keys.items() if key == minor})
                break
    canonical = {key: counter.most_common(1)[0][0] for key, counter in display.items()}
    tagged_years = collections.Counter(m.get("year") for _, m in entries if m.get("year"))
    if canonical:
        top_key = collections.Counter(keys.values()).most_common(1)[0][0]
        fallback_album = canonical[top_key]
    else:
        fallback_album = clean_folder_album_name(folder_name)
    for path, meta in entries:
        if path in keys:
            if canonical[keys[path]] != meta.get("album"):
                plans[path]["album"] = canonical[keys[path]]
        elif fallback_album:
            plans[path]["album"] = fallback_album
            keys[path] = _title_key(fallback_album)
            if not meta.get("year"):
                year = (tagged_years.most_common(1)[0][0] if tagged_years else None) or _folder_year(folder_name)
                if year:
                    plans[path]["year"] = year

    # 2. Untagged artists/titles from file and folder names.
    @functools.cache
    def folder_artist() -> Optional[str]:
        known = collections.Counter(
            _known(m.get("album_artist"), UNKNOWN_ARTIST) or _known(m.get("artist"), UNKNOWN_ARTIST)
            for _, m in entries
        )
        known.pop(None, None)
        return (
            (known.most_common(1)[0][0] if known else None)
            or _artist_from_folder_name(folder_name)
            or sibling_artist(album_folder)
            or next(iter(_ancestor_names(album_folder)), None)
        )

    for path, meta in entries:
        plan = plans[path]
        number, parts = _parse_filename(path)
        untitled = meta.get("title") == ntpath.splitext(ntpath.basename(path))[0]
        if meta.get("track_number") is None and number is not None:
            plan["track_number"] = number
        if _known(meta.get("artist"), UNKNOWN_ARTIST) or meta.get("album_artist"):
            untitled = untitled and not _known(meta.get("album"), UNKNOWN_ALBUM)
            if untitled and len(parts) == 2 and parts[0].casefold() == meta["artist"].casefold():
                plan["title"] = parts[1]
            elif untitled and number is not None:
                plan["title"] = " - ".join(parts)
            continue
        artist, title = None, " - ".join(parts)
        if len(parts) >= 2:
            guess = folder_artist()
            if number is not None or guess is None or parts[0].casefold() == guess.casefold():
                artist, title = parts[0], " - ".join(parts[1:])
        artist = artist or folder_artist()
        if artist:
            plan["artist"] = artist
        if untitled and title:
            plan["title"] = title

    # 3. One album artist per (folder, album title).
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for path, _ in entries:
        if path in keys:
            groups[keys[path]].append(path)
    meta_by_path = dict(entries)
    for paths in groups.values():
        names = []
        for path in paths:
            meta = meta_by_path[path]
            name = (
                _known(meta.get("album_artist"), UNKNOWN_ARTIST)
                or plans[path].get("artist")
                or _known(meta.get("artist"), UNKNOWN_ARTIST)
            )
            if name:
                names.append(name)
        if len(set(names)) <= 1:
            continue
        owner = _choose_owner(names, album_folder, sibling_artist)
        for path in paths:
            plans[path]["album_owner"] = owner
    return plans
