"""OpenCC-backed Chinese search normalization.

Only search keys are normalized. Never modify FLAC tags, database display
names, or user-visible spelling. The OpenCC dictionaries are supplied by the
pinned opencc wheel; no hand-maintained character map is used.
"""
from __future__ import annotations

from functools import lru_cache
import unicodedata

from opencc import OpenCC

_T2S = OpenCC("t2s.json")
_S2T = OpenCC("s2t.json")


def literal_fold(text: str | None) -> str:
    """Case-insensitive, Unicode-normalized representation of original text."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    # Never allow the index field separator or control characters in a key.
    return " ".join(
        "".join(" " if unicodedata.category(ch) == "Cc" else ch
                for ch in normalized).split()
    )


@lru_cache(maxsize=65536)
def to_simplified(text: str) -> str:
    """Phrase-aware Traditional -> Simplified conversion."""
    return _T2S.convert(text) if text else text


@lru_cache(maxsize=16384)
def to_traditional(text: str) -> str:
    """Best-effort Simplified -> Traditional display variant (not reversible)."""
    return _S2T.convert(text) if text else text


def search_fold(text: str | None) -> str:
    """Shared canonical key for queries and all music search indexes."""
    original = literal_fold(text)
    return literal_fold(to_simplified(original))


def get_all_variants(text: str) -> list[str]:
    """Backward-compatible query expansion. Search must not rely on S2T round-trips."""
    if not text:
        return [text]
    normalized = unicodedata.normalize("NFKC", text)
    return list(dict.fromkeys(
        (normalized, to_simplified(normalized), to_traditional(normalized))
    ))
