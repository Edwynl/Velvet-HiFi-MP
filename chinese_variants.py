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

# Folding stops at a fixed point; the cap only guards against a future
# dictionary with a cycle. Two passes cover every case seen in the library.
_MAX_FOLD_PASSES = 4


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
    """Shared canonical key for queries and all music search indexes.

    OpenCC rewrites whole phrases, so a single pass is not a fixed point: the
    Taiwan singer 陳昇 folds to 陈昇, and 陈昇 folds again to 陈升. Stored keys
    and query keys are compared with `instr`, so a key that changes on a second
    pass can never match itself. Fold until stable instead.
    """
    folded = literal_fold(to_simplified(literal_fold(text)))
    for _ in range(_MAX_FOLD_PASSES):
        again = literal_fold(to_simplified(folded))
        if again == folded:
            break
        folded = again
    return folded


def get_all_variants(text: str) -> list[str]:
    """Backward-compatible query expansion. Search must not rely on S2T round-trips."""
    if not text:
        return [text]
    normalized = unicodedata.normalize("NFKC", text)
    return list(dict.fromkeys(
        (normalized, to_simplified(normalized), to_traditional(normalized))
    ))
