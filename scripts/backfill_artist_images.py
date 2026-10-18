#!/usr/bin/env python3
"""
Backfill artist avatars for the local VELVET library.

Source strategy
---------------
A music service's own artist image is fresher and more recognisable than a
wiki portrait, so Apple Music leads and Wikimedia is the fallback:

    A1  Apple Music og:image
        iTunes Search API (entity=musicArtist) resolves name -> artist page,
        then the page's own ``og:image`` is taken. ``og:image`` is by
        definition that artist page's hero artwork, which matters: the same
        pages embed hundreds of AMCArtistImages URLs from their "similar
        artists" carousels, and picking the first of those attaches a
        *different* musician's face - verified, the identical 147 KB portrait
        came back for four different artists. The Apple filenames carry
        ``ami-identity-...-2026-03-24`` timestamps, i.e. current press art.

    B1  Wikidata P18          - curated Commons portrait
    B2  Wikipedia lead image  - article hero
    B3  Commons category file - band photos, downgraded to `low`

Why the verification exists
---------------------------
The legacy ``enrichment.search_artist_photo()`` takes ``data[0]`` from
Deezer and the first DuckDuckGo image hit without ever checking *who* the
artist is. Auditing the 19 avatars it produced in June showed roughly half
were wrong: 张碧晨 got a white man in a leather jacket, 张靓颖 got an
illustration of Saturn, 孟佳 got an Eason Chan poster, 刘雨昕 got a music
festival banner. That helper is still wired to ``/api/enrich/bios``.
This script only stores an image after the candidate has been verified to be
the same artist, and records the source, matched name and a confidence tier
for every decision so the result can be audited afterwards.

Two traps this deliberately avoids
----------------------------------
* ``Category:<article title>`` is NOT used to derive a Commons category.
  Category:Beyond on Commons is a software product, so the band would be
  given a screenshot of unrelated software. Only an explicit ``commonswiki``
  sitelink is followed.
* A broad "non-music keyword" blacklist is NOT used. It rejected real
  artists ("German film composer" -> Hans Zimmer, "詞曲作家" -> 张靓颖)
  because those words legitimately co-occur in a musician's description.
  The gate is that a primary music role must be present.

Apple has no published rate limit, but 4 concurrent search calls earn HTTP
429 after ~15 requests; one call per second is stable.

Usage
    python scripts/backfill_artist_images.py --dry-run --limit 40
    python scripts/backfill_artist_images.py --only-id 78 --only-id 1112
    python scripts/backfill_artist_images.py --sources apple
    python scripts/backfill_artist_images.py --retry-no-image --include-low
    python scripts/backfill_artist_images.py            # full run, resumable

Resumable: settled decisions go to ``velvet_data/artist_images/_backfill.jsonl``.
Low-confidence and failed rows stay out of it on purpose so a later run with
different flags still retries them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

import httpx
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reuse the running server's normaliser so a file written here is
# indistinguishable from one written by enrichment.download_artist_photo().
from enrichment import ARTIST_IMAGE_MAX_SIZE, _normalize_image_to_jpeg_bytes  # noqa: E402

try:
    from zhconv import convert as _zh_convert
except ImportError:  # pragma: no cover - optional dependency
    _zh_convert = None

# ─── Configuration ───────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("VELVET_DATA_DIR") or os.environ.get("DATA_DIR", "velvet_data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR
DB_PATH = DATA_DIR / "library.db"
IMAGE_DIR = DATA_DIR / "artist_images"
PROGRESS_PATH = IMAGE_DIR / "_backfill.jsonl"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Wikimedia 403s every generic *and* browser-spoofing agent; only a descriptive
# UA with a contact point is served.
WIKI_UA = ("VELVET-Next/1.0 (local music library avatar backfill; "
           "https://github.com/Edwynl/velvet-next; velvet-audio@example.com)")

ITUNES_SEARCH = "https://itunes.apple.com/search"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = {"en": "https://en.wikipedia.org/w/api.php",
                 "zh": "https://zh.wikipedia.org/w/api.php"}
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"

APPLE_SEARCH_DELAY = 1.0
APPLE_SEARCH_CONCURRENCY = 1
APPLE_PAGE_CONCURRENCY = 4

CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}

# ─── Name normalisation ──────────────────────────────────────────────────────

_PUNCT = re.compile(
    r"[\s\u3000·・\-–—_.,，。、:;；!！?？'\"“”‘’()（）\[\]【】《》〈〉/\\|~～@#$%^&*+=\u200b]+"
)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

PSEUDO_ARTIST = re.compile(
    r"^(various(\s+artists)?|unknown(\s+artist)?|compilation|ost|"
    r"original\s+soundtrack|群星|群星演唱|未知|不明|無名|佚名|未命名|无|"
    r"多重\w*|various\s+artists\s*feat\.?.*)$",
    re.IGNORECASE,
)


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def normalize_name(name: str) -> str:
    """Fold case, punctuation, accents and Chinese script into one key.

    ``Sharléne`` must match Apple's ``Sharlene`` and 郑秀文 must match 鄭秀文,
    otherwise verified artists get discarded as mismatches.
    """
    s = unicodedata.normalize("NFKD", name or "").casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _PUNCT.sub("", s)
    if _zh_convert is not None:
        s = _zh_convert(s, "zh-cn")
    return s


def _token_order(name: str) -> str:
    return " ".join(sorted(_PUNCT.sub(" ", name or "").casefold().split()))


_ALIAS_NOISE = re.compile(r"[\(\[\{（【][^)）\]}】]*[\)\]\}）】]|[/／][^/／]*$")


def strip_alias_noise(name: str) -> str:
    """Drop a trailing alias annotation.

    File tags carry "陳綺貞 (Cheer Chen)" and "BIGBANG (빅뱅)" while Apple and
    Wikidata file the same artists as "陳綺貞" and "BIGBANG". Without this the
    verified artist is thrown away as a mismatch.
    """
    cleaned = _ALIAS_NOISE.sub("", name or "").strip()
    return cleaned or (name or "").strip()


def name_match_level(target: str, label: str) -> str:
    """How confidently two artist names denote the same person.

    ``exact``  - identical after normalisation, or the same latin words in a
                 different order (Wikidata files 梶浦由記 as "Yuki Kajiura"
                 while tags say "Kajiura Yuki").
    ``alias``  - equal once a trailing "(...)" alias is removed.
    ``partial``- one name contains the other ("软硬" / "軟硬天師").

    CJK never gets the word-order treatment: reordering ideographs is not a
    spelling variant.
    """
    tgt, lbl = normalize_name(target), normalize_name(label)
    if not tgt or not lbl:
        return "none"
    if tgt == lbl:
        return "exact"
    if not (has_cjk(target) or has_cjk(label)) and _token_order(target) == _token_order(label):
        return "exact"
    if normalize_name(strip_alias_noise(target)) == normalize_name(strip_alias_noise(label)):
        return "alias"
    short, long = sorted((tgt, lbl), key=len)
    if len(short) >= 2 and short in long:
        return "partial"
    return "none"


def name_matches(target: str, label: str) -> bool:
    """True for exact/alias matches; partial containment needs --include-low."""
    return name_match_level(target, label) in ("exact", "alias")


def looks_like_pseudo_artist(name: str) -> bool:
    cleaned = (name or "").strip()
    if PSEUDO_ARTIST.match(cleaned):
        return True
    if not cleaned or len(_PUNCT.sub("", cleaned)) <= 1:
        return True
    if re.fullmatch(r"[\d\s\-_]+", cleaned):
        return True
    return False


# ─── Wikidata entity classification ──────────────────────────────────────────

_DISAMBIGUATION = re.compile(r"消歧义|disambiguation|désambiguï|disambig", re.IGNORECASE)

# Roles that identify the entity as a performing musician.
PRIMARY_MUSIC = re.compile(
    r"singer|songwriter|vocalist|rapper|\brapping\b|musician|musical\s+group|"
    r"\bband\b|rock\s+band|pop\s+group|record\s+producer|\bidol\b|lyricist|"
    r"composer|orchestra|choir|"
    r"歌手|唱作人|演唱|樂手|樂團|乐团|乐队|组合|組合|組合|偶像|音樂家|音乐家|"
    r"作曲家|摇滚|搖滾|說唱|说唱|乐团|演奏團|合唱團|音乐剧|音樂劇",
    re.IGNORECASE,
)

# Roles that alone are NOT evidence - "美國華裔玩具鋼琴家" has one.
SECONDARY_ROLE = re.compile(
    r"\bpianist\b|keyboardist|violinist|guitarist|drummer|bassist|"
    r"\bremixer\b|\barranger\b|percussionist|"
    r"鋼琴家|钢琴家|鋼琴手|吉他|鼓手|贝斯|貝斯|制作人|製作人|作曲家",
    re.IGNORECASE,
)

# Entity types that can never be the artist. Deliberately narrow.
NON_MUSIC = re.compile(
    r"\bvillage\b|\btown in\b|\bcompany\b|\bbrand\b|\brailway station\b|"
    r"\bfictional\b|\bfictional character\b|\bdynasty\b|"
    r"角色|虛構|虚构|王朝|村鎮|村镇|公司|品牌|車站|车站",
    re.IGNORECASE,
)

COMMONS_JUNK = re.compile(
    r"crowd|audience|concert\s+audience|auditorium|fans|backstage|"
    r"poster|autograph|signature|stamp|coin|banknote|album\s+cover|"
    r"singles|video\s+screenshot|screenshot|logo\s+variant|"
    r"觀眾|观众|人群|簽名|签名|海报|海報|自動販賣|現場|现场",
    re.IGNORECASE,
)


class Verdict:
    __slots__ = ("accept", "confidence", "reason")

    def __init__(self, accept: bool, confidence: str, reason: str):
        self.accept = accept
        self.confidence = confidence
        self.reason = reason


def classify(target_name: str, label: str, description: str,
             occupation_labels: Iterable[str] = (),
             allow_unknown_role: bool = False) -> Verdict:
    if _DISAMBIGUATION.search(description or "") or _DISAMBIGUATION.search(label or ""):
        return Verdict(False, "reject", "disambiguation page")
    if not label or not name_matches(target_name, label):
        return Verdict(False, "reject", "name mismatch")

    desc = description or ""
    if NON_MUSIC.search(desc):
        return Verdict(False, "reject", "non-music domain: %s" % desc[:48])

    occ = " | ".join(o for o in occupation_labels if o)
    if PRIMARY_MUSIC.search(desc):
        return Verdict(True, "high", "primary music role in description")
    if occ and PRIMARY_MUSIC.search(occ):
        return Verdict(True, "medium", "primary music role in occupation: %s" % occ[:48])
    if SECONDARY_ROLE.search(desc) or (occ and SECONDARY_ROLE.search(occ)):
        return Verdict(False, "reject", "instrumental/secondary role only: %s" % (desc or occ)[:48])

    # Requiring PRIMARY_MUSIC is what keeps "Lisa" from resolving to a
    # Romanian village with a perfectly valid photo.
    if allow_unknown_role:
        # Second pass only: 盧慧敏 is filed as "香港女演員" on Wikidata even
        # though she sings. Exact name, unproven role -> offered as `low` so
        # it stays out of the default set until reviewed.
        return Verdict(True, "low", "exact name match, role not stated as music: %s" % desc[:48])
    return Verdict(False, "reject", "no primary music role: %s" % desc[:48])


# ─── Apple Music ─────────────────────────────────────────────────────────────

_MZ_RESIZE = re.compile(r"/(\d+)x(\d+)[a-z]{2}(?:-60)?\.(jpg|png|webp)$")
_OG_IMAGE = re.compile(r'<meta property="og:image" content="([^"]+)"')


def _upscale(url: str, edge: int = 800) -> str:
    m = _MZ_RESIZE.search(url)
    return url[:m.start()] + "/%dx%dbb.jpg" % (edge, edge) if m else url


def looks_flat(colour_spread: float, edge_energy: float) -> bool:
    """Detect Apple's "no artwork" placeholder and plain wordmark tiles.

    Measured over the confirmed-bad vs confirmed-good samples: Apple's blank
    artist tile sits near spread 21 / edges 1.8, while real portraits keep
    edges above ~9. A naive threshold on spread alone flagged 田馥甄 and 丁噹,
    which are perfectly good headshots.
    """
    return edge_energy < 3.0 and colour_spread < 45.0


def image_stats(jpeg_bytes: bytes) -> tuple[float, float]:
    """(colour spread, edge energy) of a JPEG, used for placeholder detection."""
    try:
        import io as _io

        with Image.open(_io.BytesIO(jpeg_bytes)) as probe:
            arr = np.asarray(
                probe.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
            ).astype("float32")
        spread = float(arr.std(axis=(0, 1)).mean())
        edges = float((np.abs(np.diff(arr.mean(axis=2), axis=1)).mean()
                       + np.abs(np.diff(arr.mean(axis=2), axis=0)).mean()) / 2)
        return spread, edges
    except Exception:
        return 999.0, 999.0


def _is_wikimedia_host(url: str) -> bool:
    return any(h in url for h in ("wikimedia.org", "wikipedia.org", "wikidata.org"))


async def fetch_image(http: httpx.AsyncClient, wiki: Optional["Wiki"], url: str) -> bytes:
    """Download with the User-Agent that host actually accepts.

    Wikimedia 403s browser-spoofing agents while mzstatic wants one; sending
    the wrong UA silently failed every Wikimedia download until this split.
    """
    if _is_wikimedia_host(url) and wiki is not None:
        return await wiki.fetch_bytes(url)
    resp = await http.get(url)
    resp.raise_for_status()
    return resp.content


class Apple:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._sem = asyncio.Semaphore(APPLE_SEARCH_CONCURRENCY)
        self._last = 0.0
        self.calls = 0

    async def _search(self, name: str) -> list[dict]:
        async with self._sem:
            wait = APPLE_SEARCH_DELAY - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            delay = 2.0
            for attempt in range(4):
                self.calls += 1
                resp = await self._client.get(
                    ITUNES_SEARCH,
                    params={"term": name, "entity": "musicArtist", "limit": 5})
                if resp.status_code == 429:
                    if attempt == 3:
                        return []
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if resp.status_code != 200:
                    return []
                return resp.json().get("results", [])
        return []

    async def artist_image(self, name: str, min_level: str = "alias",
                           exact_name: Optional[str] = None) -> Optional[dict]:
        """Resolve an artist page and its og:image.

        ``exact_name`` forces a specific Apple entry, used for the
        hand-reviewed cases where the library tag and the service name differ
        but are known to be the same act ("軟硬" -> "軟硬天師").
        """
        results = await self._search(name)
        if not results:
            return None

        levels = {"exact": 3, "alias": 2, "partial": 1}
        floor = levels.get(min_level, 2)
        matched = None
        matched_level = "none"
        for item in results:
            apple_name = item.get("artistName") or ""
            if exact_name is not None:
                level = "exact" if name_matches(exact_name, apple_name) else "none"
            else:
                level = name_match_level(name, apple_name)
            if levels.get(level, 0) >= floor and levels.get(level, 0) > levels.get(matched_level, 0):
                matched = item
                matched_level = level
                if level == "exact":
                    break
        if matched is None:
            return None

        link = matched.get("artistLinkUrl")
        if not link:
            return None
        page = await self._client.get(link)
        if page.status_code != 200:
            return None
        found = _OG_IMAGE.search(page.text)
        if not found:
            return None
        return {
            "url": _upscale(found.group(1).split("?")[0]),
            "apple_name": matched.get("artistName"),
            "artist_id": matched.get("artistId"),
            "match_level": matched_level,
        }


# ─── Wikimedia ───────────────────────────────────────────────────────────────

class Wiki:
    def __init__(self, concurrency: int = 3, delay: float = 0.15):
        self._client = httpx.AsyncClient(
            timeout=30.0, follow_redirects=True,
            headers={"User-Agent": WIKI_UA, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=concurrency + 2),
        )
        self._sem = asyncio.Semaphore(concurrency)
        self._delay = delay
        self._last = 0.0
        self._occ_cache: dict[str, str] = {}
        self.calls = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_bytes(self, url: str) -> bytes:
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.content

    async def _get(self, url: str, params: dict) -> Any:
        async with self._sem:
            wait = self._delay - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            delay = 1.0
            for attempt in range(3):
                try:
                    self.calls += 1
                    resp = await self._client.get(url, params=params)
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if resp.status_code in (429, 503, 502, 504):
                    if attempt == 2:
                        resp.raise_for_status()
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                resp.raise_for_status()
                return resp.json()
        raise RuntimeError("unreachable")

    async def search_entities(self, name: str, lang: str) -> list[dict]:
        data = await self._get(WIKIDATA_API, {
            "action": "wbsearchentities", "search": name, "language": lang,
            "uselang": lang, "format": "json", "limit": 6, "type": "item",
        })
        return data.get("search", [])

    async def entity(self, qid: str) -> dict:
        data = await self._get(WIKIDATA_API, {
            "action": "wbgetentities", "ids": qid, "format": "json",
            "props": "claims|sitelinks",
        })
        return (data.get("entities") or {}).get(qid, {})

    async def labels_for(self, qids: list[str]) -> dict[str, str]:
        missing = [q for q in qids if q not in self._occ_cache]
        if missing:
            try:
                data = await self._get(WIKIDATA_API, {
                    "action": "wbgetentities", "ids": "|".join(missing),
                    "format": "json", "props": "labels",
                    "languages": "en|zh|zh-hans|zh-hant|zh-cn",
                })
                for q, ent in (data.get("entities") or {}).items():
                    labels = (ent or {}).get("labels") or {}
                    self._occ_cache[q] = next(
                        (v["value"] for v in labels.values() if v.get("value")), q)
            except Exception:
                for q in missing:
                    self._occ_cache.setdefault(q, "")
        return {q: self._occ_cache.get(q, "") for q in qids}

    async def page_image(self, lang: str, title: str, width: int = 400) -> Optional[str]:
        api = WIKIPEDIA_API.get(lang)
        if not api:
            return None
        try:
            data = await self._get(api, {
                "action": "query", "titles": title, "prop": "pageimages",
                "piprop": "thumbnail", "pithumbsize": width, "format": "json",
            })
        except Exception:
            return None
        for page in (data.get("query") or {}).get("pages", {}).values():
            thumb = page.get("thumbnail")
            if thumb and thumb.get("source"):
                return thumb["source"]
        return None

    async def commons_category_image(self, category_title: str,
                                     width: int = 400) -> Optional[str]:
        """Only called for an explicit commonswiki sitelink, never derived."""
        try:
            members = await self._get(COMMONS_API, {
                "action": "query", "list": "categorymembers",
                "cmtitle": category_title, "cmtype": "file",
                "cmlimit": "40", "format": "json",
            })
        except Exception:
            return None
        files = [m["title"] for m in members.get("query", {}).get("categorymembers", [])
                 if not COMMONS_JUNK.search(m.get("title", ""))]
        if not files:
            return None
        try:
            info = await self._get(COMMONS_API, {
                "action": "query", "titles": "|".join(files[:8]), "prop": "imageinfo",
                "iiprop": "url", "iiurlwidth": str(width), "format": "json",
            })
        except Exception:
            return None
        best = None
        for page in (info.get("query") or {}).get("pages", {}).values():
            ii = (page.get("imageinfo") or [{}])[0]
            url = ii.get("thumburl") or ii.get("url")
            if not url:
                continue
            title = page.get("title", "")
            score = 1 if re.search(r"(band|group|with|\b\d{4}\b)", title, re.IGNORECASE) else 0
            if re.search(r"(logo|map|coat|flag)", title, re.IGNORECASE):
                score -= 1
            if best is None or score > best[0]:
                best = (score, url)
        return best[1] if best else None


def sitelink_key(sitelang: str) -> str:
    return {"en": "enwiki", "zh": "zhwiki", "zh-hant": "zh_hantwiki"}[sitelang]


def _filepath_url(filename: str, width: int = 400) -> str:
    return FILEPATH + quote(filename.replace(" ", "_"), safe="/:()',!") + f"?width={width}"


async def _pick_wiki_image(wiki: Wiki, qid: str, label: str, desc: str, claims: dict,
                           sitelinks: dict, verdict: Verdict, use_commons: bool,
                           rejected: list[str]) -> dict:
    base = {
        "status": "ok", "confidence": verdict.confidence, "reason": verdict.reason,
        "wikidata_id": qid, "matched_label": label, "matched_description": desc,
        "english_label": (sitelinks.get("enwiki") or {}).get("title"),
        "rejected_candidates": rejected,
    }

    for claim in claims.get("P18", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, str) and value:
            return {**base, "image_url": _filepath_url(value),
                    "source": "wikidata-p18", "reference": value}

    for sitelang in ("en", "zh", "zh-hant"):
        sitelink = sitelinks.get(sitelink_key(sitelang))
        if not sitelink:
            continue
        wiki_lang = "zh" if sitelang.startswith("zh") else "en"
        url = await wiki.page_image(wiki_lang, sitelink["title"])
        if url:
            return {**base, "image_url": url,
                    "source": "wikipedia-pageimage:" + sitelang,
                    "reference": sitelink["title"]}

    if use_commons:
        commons = sitelinks.get("commonswiki")
        if commons:
            url = await wiki.commons_category_image(commons["title"])
            if url:
                return {**base, "image_url": url, "source": "commons-category",
                        "confidence": "low",
                        "reason": verdict.reason + "; uncurated commons category file",
                        "reference": commons["title"]}

    return {
        "status": "no-image", "reason": "verified entity has no picture",
        "source": None, "wikidata_id": qid, "matched_label": label,
        "matched_description": desc, "confidence": verdict.confidence,
        "english_label": (sitelinks.get("enwiki") or {}).get("title"),
        "rejected_candidates": rejected,
    }


async def resolve_wiki(wiki: Wiki, name: str, use_commons: bool) -> dict:
    lang = "zh" if has_cjk(name) else "en"
    try:
        hits = await wiki.search_entities(name, lang)
    except Exception as exc:
        return {"status": "error", "reason": "wikidata search failed: %s" % type(exc).__name__}
    if not hits:
        return {"status": "no-image", "reason": "no Wikidata entity", "source": None}

    rejected: list[str] = []
    fallback: Optional[dict] = None

    for hit in hits:
        label = hit.get("label") or ""
        desc = hit.get("description") or ""

        pre = classify(name, label, desc, ())
        if pre.reason in ("disambiguation page", "name mismatch"):
            rejected.append("%s(%s):%s" % (label, desc[:24], pre.reason))
            continue

        qid = hit.get("id")
        try:
            ent = await wiki.entity(qid)
        except Exception:
            rejected.append("%s: entity fetch failed" % label)
            continue

        claims = ent.get("claims") or {}
        sitelinks = ent.get("sitelinks") or {}
        occ_labels: list[str] = []
        if not PRIMARY_MUSIC.search(desc):
            occ_qids = []
            for claim in claims.get("P106", []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                if isinstance(value, dict) and value.get("id"):
                    occ_qids.append(value["id"])
            if occ_qids:
                occ_labels = list((await wiki.labels_for(occ_qids)).values())

        verdict = classify(name, label, desc, occ_labels)
        if not verdict.accept:
            rejected.append("%s(%s):%s" % (label, desc[:24], verdict.reason))
            if verdict.reason.startswith("no primary music role") and fallback is None:
                fallback = {"qid": qid, "label": label, "desc": desc, "claims": claims,
                            "sitelinks": sitelinks, "occ_labels": occ_labels}
            continue

        return await _pick_wiki_image(wiki, qid, label, desc, claims, sitelinks,
                                      verdict, use_commons, rejected)

    if fallback is not None:
        lenient = classify(name, fallback["label"], fallback["desc"],
                           fallback["occ_labels"], allow_unknown_role=True)
        if lenient.accept:
            return await _pick_wiki_image(
                wiki, fallback["qid"], fallback["label"], fallback["desc"],
                fallback["claims"], fallback["sitelinks"], lenient, use_commons, rejected)

    return {"status": "no-image",
            "reason": "no verified candidate (%s)" % ("; ".join(rejected) or "no hits"),
            "source": None, "rejected_candidates": rejected}


async def resolve(apple: Apple, wiki: Optional[Wiki], artist: dict,
                  sources: set[str], use_commons: bool,
                  exact_name: Optional[str] = None) -> dict:
    name = artist["name"]
    if not exact_name and looks_like_pseudo_artist(name):
        return {"status": "skipped", "reason": "pseudo/various artist", "source": None}

    attempts: list[str] = []

    if "apple" in sources:
        try:
            found = await apple.artist_image(name, exact_name=exact_name)
        except Exception as exc:
            found = None
            attempts.append("apple error %s" % type(exc).__name__)
        if found:
            level = found.get("match_level", "exact")
            return {"status": "ok", "image_url": found["url"],
                    "source": "apple-og-image",
                    # A containment match ("软硬" / "軟硬天師") may be a different
                    # act that merely shares a prefix, so it is review-only.
                    "confidence": "high" if level == "exact" else
                                  "medium" if level == "alias" else "low",
                    "reason": "Apple Music artist page og:image (%s name match)" % level,
                    "matched_label": found["apple_name"],
                    "reference": "https://music.apple.com/artist/%s" % found["artist_id"]}
        attempts.append("apple: no verified match")

    if "wiki" in sources and wiki is not None:
        res = await resolve_wiki(wiki, name, use_commons)
        if res.get("status") == "ok":
            return res

        # Apple files many Chinese artists under a romanised name only
        # ("杨坤" -> "Yang Kun"), which strict matching rejects. Wikidata has
        # already verified who this is, so its English label is safe to look
        # up on Apple and match exactly.
        english_label = res.get("english_label")
        if apple is not None and english_label and not has_cjk(english_label):
            try:
                bridged = await apple.artist_image(english_label, min_level="exact")
            except Exception:
                bridged = None
            if bridged:
                return {"status": "ok", "image_url": bridged["url"],
                        "source": "apple-og-image", "confidence": "high",
                        "reason": "Apple Music og:image via Wikidata-verified English name",
                        "matched_label": bridged["apple_name"],
                        "matched_description": res.get("matched_description"),
                        "reference": "https://music.apple.com/artist/%s" % bridged["artist_id"]}
            attempts.append("apple via english label %r: no match" % english_label)

        attempts.append("wiki: %s" % res.get("reason"))

    return {"status": "no-image", "reason": "; ".join(attempts) or "no sources enabled",
            "source": None}


# ─── Persistence ─────────────────────────────────────────────────────────────

def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def load_artists(only_ids: Optional[list[int]], include_filled: bool,
                 order_by: str, id_file: str = "") -> list[dict]:
    sql = "SELECT id, name FROM artists"
    if not include_filled:
        sql += " WHERE image_path IS NULL OR image_path = ''"
    sql += " ORDER BY name" if order_by == "name" else " ORDER BY id"
    conn = connect_db()
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    artists = [{"id": r["id"], "name": r["name"]} for r in rows]

    wanted: set[int] = set(only_ids or [])
    if id_file:
        for line in Path(id_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                wanted.add(int(line))
    if wanted:
        artists = [a for a in artists if a["id"] in wanted]
    return artists


def load_progress() -> dict[int, dict]:
    done: dict[int, dict] = {}
    if not PROGRESS_PATH.exists():
        return done
    with PROGRESS_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") in ("ok", "skipped", "no-image"):
                done[row["id"]] = row
    return done


class Writer:
    def __init__(self, conn: sqlite3.Connection, flush_every: int = 40):
        self.conn = conn
        self.flush_every = flush_every
        self.pending: list[tuple[str, int]] = []

    def queue(self, path: str, artist_id: int) -> None:
        self.pending.append((path, artist_id))
        if len(self.pending) >= self.flush_every:
            self.flush()

    def flush(self) -> None:
        if not self.pending or self.conn is None:
            return
        self.conn.executemany("UPDATE artists SET image_path=? WHERE id=?", self.pending)
        self.conn.commit()
        self.pending.clear()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Backfill VELVET artist avatars (verified).")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--only-id", type=int, action="append", default=[])
    ap.add_argument("--id-file", default="",
                    help="newline-separated artist ids to process (one per line); "
                         "used to run a priority batch, e.g. the popular ones first")
    ap.add_argument("--apple-name-map", default="",
                    help='JSON of {"<artist_id>": "<Apple artist name>"} forcing a '
                         'specific match for hand-reviewed name variants')
    ap.add_argument("--sources", default="apple,wiki",
                    help="comma separated: apple,wiki (default: apple,wiki)")
    ap.add_argument("--min-confidence", choices=["low", "medium", "high"], default="medium")
    ap.add_argument("--include-low", action="store_true",
                    help="alias for --min-confidence low")
    ap.add_argument("--no-commons", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--include-filled", action="store_true")
    ap.add_argument("--order-by", choices=["id", "name"], default="id")
    ap.add_argument("--wiki-concurrency", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ignore-progress", action="store_true",
                    help="re-resolve settled artists (use after changing the logic)")
    ap.add_argument("--retry-no-image", action="store_true",
                    help="re-process only artists previously settled with no image")
    ap.add_argument("--report", default="")
    return ap.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    min_conf = "low" if args.include_low else args.min_confidence
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    unknown = sources - {"apple", "wiki"}
    if unknown:
        raise SystemExit("unknown source(s): %s" % ", ".join(sorted(unknown)))

    apple_overrides: dict[int, str] = {}
    if args.apple_name_map:
        raw = json.loads(Path(args.apple_name_map).read_text(encoding="utf-8"))
        apple_overrides = {int(k): v for k, v in raw.items()}

    artists = load_artists(args.only_id or None,
                           args.include_filled or args.overwrite, args.order_by,
                           id_file=args.id_file)
    total_all = len(artists)

    if args.ignore_progress:
        done = {}
    elif args.retry_no_image:
        done = {aid: row for aid, row in load_progress().items()
                if row.get("status") != "no-image"}
    else:
        done = load_progress()
    artists = [a for a in artists if a["id"] not in done]
    if args.offset:
        artists = artists[args.offset:]
    if args.limit:
        artists = artists[:args.limit]

    print("artists in library: %d   to process now: %d   sources: %s   min_confidence: %s%s"
          % (total_all, len(artists), "+".join(sorted(sources)), min_conf,
             "   [DRY RUN]" if args.dry_run else ""))

    apple = None
    wiki = None
    async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": BROWSER_UA},
            limits=httpx.Limits(max_connections=APPLE_PAGE_CONCURRENCY + 2)) as http:
        if "apple" in sources:
            apple = Apple(http)
        if "wiki" in sources:
            wiki = Wiki(concurrency=max(1, args.wiki_concurrency), delay=args.delay)

        conn = None if args.dry_run else connect_db()
        writer = Writer(conn) if conn else None
        progress_fh = None if args.dry_run else PROGRESS_PATH.open("a", encoding="utf-8")

        counts = {"ok": 0, "stored": 0, "skipped": 0, "no-image": 0,
                  "low-confidence": 0, "download-failed": 0, "error": 0}
        by_source: dict[str, int] = {}
        rows: list[dict] = []
        started = time.time()
        page_sem = asyncio.Semaphore(APPLE_PAGE_CONCURRENCY)

        def record(row: dict, checkpoint: bool = True) -> None:
            rows.append(row)
            if progress_fh and checkpoint:
                progress_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                progress_fh.flush()

        async def handle(artist: dict) -> dict:
            res = await resolve(apple, wiki, artist, sources,
                                use_commons=not args.no_commons,
                                exact_name=apple_overrides.get(artist["id"]))
            row = {
                "id": artist["id"], "name": artist["name"],
                "status": res["status"], "source": res.get("source"),
                "confidence": res.get("confidence"), "reason": res.get("reason"),
                "matched_label": res.get("matched_label"),
                "matched_description": res.get("matched_description"),
                "image_url": res.get("image_url"), "reference": res.get("reference"),
                "rejected_candidates": res.get("rejected_candidates", [])[:6],
            }

            if res["status"] != "ok":
                counts["skipped" if res["status"] == "skipped" else
                       ("no-image" if res["status"] == "no-image" else "error")] += 1
                record(row)
                return row

            conf = res.get("confidence") or "low"
            if CONFIDENCE_ORDER.get(conf, 0) < CONFIDENCE_ORDER[min_conf]:
                row["status"] = "low-confidence"
                counts["low-confidence"] += 1
                record(row, checkpoint=False)
                return row

            async with page_sem:
                try:
                    payload = await fetch_image(http, wiki, res["image_url"])
                except Exception as exc:
                    row["status"] = "download-failed"
                    row["reason"] = "download failed: %s" % type(exc).__name__
                    counts["download-failed"] += 1
                    record(row, checkpoint=False)
                    return row

            jpeg = _normalize_image_to_jpeg_bytes(payload, max_edge=ARTIST_IMAGE_MAX_SIZE)
            if not jpeg:
                row["status"] = "download-failed"
                row["reason"] = "not a decodable image"
                counts["download-failed"] += 1
                record(row, checkpoint=False)
                return row

            # Apple's "no artwork" tile is a flat colour block. Storing it is
            # worse than the letter placeholder, so fall back to Wikimedia.
            if res.get("source") == "apple-og-image":
                spread, edges = image_stats(jpeg)
                if looks_flat(spread, edges):
                    row["colour_spread"] = round(spread, 1)
                    row["edge_energy"] = round(edges, 1)
                    counts["flat-placeholder"] = counts.get("flat-placeholder", 0) + 1
                    if apple is not None and "wiki" in sources and wiki is not None:
                        try:
                            fallback = await resolve_wiki(wiki, artist["name"],
                                                         use_commons=not args.no_commons)
                        except Exception:
                            fallback = {}
                        if fallback.get("status") == "ok":
                            try:
                                fb_payload = await fetch_image(http, wiki, fallback["image_url"])
                                fb_jpeg = _normalize_image_to_jpeg_bytes(
                                    fb_payload, max_edge=ARTIST_IMAGE_MAX_SIZE)
                            except Exception:
                                fb_jpeg = None
                            if fb_jpeg:
                                row.update({
                                    "status": "ok", "source": fallback.get("source"),
                                    "confidence": fallback.get("confidence"),
                                    "reason": "Apple placeholder -> " + str(fallback.get("reason")),
                                    "reference": fallback.get("reference"),
                                    "matched_label": fallback.get("matched_label"),
                                    "matched_description": fallback.get("matched_description"),
                                    "image_url": fallback.get("image_url"),
                                })
                                payload, jpeg = fb_payload, fb_jpeg
                                by_source[row["source"]] = by_source.get(row["source"], 0) + 1
                            else:
                                row["status"] = "flat-placeholder"
                        else:
                            row["status"] = "flat-placeholder"
                    else:
                        row["status"] = "flat-placeholder"
                    if row["status"] == "flat-placeholder":
                        row["reason"] = ("Apple returned a flat placeholder "
                                         "(spread %.1f, edges %.1f)" % (spread, edges))
                        record(row, checkpoint=False)
                        return row

            if not args.dry_run:
                dest = IMAGE_DIR / ("%d.jpg" % artist["id"])
                tmp = dest.with_suffix(".jpg.tmp")
                tmp.write_bytes(jpeg)
                tmp.replace(dest)
                writer.queue(str(dest.resolve()), artist["id"])
                row["stored_path"] = str(dest.resolve())
                row["bytes"] = len(jpeg)
            counts["ok"] += 1
            counts["stored"] += 1
            by_source[res["source"]] = by_source.get(res["source"], 0) + 1
            record(row)
            return row

        try:
            for i, artist in enumerate(artists, start=1):
                await handle(artist)
                if i % 25 == 0 or i == len(artists):
                    if progress_fh:
                        progress_fh.flush()
                    if writer:
                        writer.flush()
                    el = time.time() - started
                    rate = i / el if el else 0
                    eta = (len(artists) - i) / rate if rate else 0
                    print("  %4d/%d  ok=%d noimg=%d low=%d skip=%d err=%d  %.2f/s eta=%.0fmin"
                          % (i, len(artists), counts["ok"], counts["no-image"],
                             counts["low-confidence"], counts["skipped"],
                             counts["error"] + counts["download-failed"], rate, eta / 60),
                          flush=True)
            # Flush outside the loop: a mid-run exception must not lose images
            # that are already on disk, only their DB pointers.
            if writer:
                writer.flush()
        finally:
            if writer:
                writer.flush()
            if progress_fh:
                progress_fh.close()
            if conn:
                conn.close()
            if wiki:
                await wiki.aclose()

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": args.dry_run,
        "sources": sorted(sources),
        "min_confidence": min_conf,
        "candidates_considered": len(artists),
        "counts": counts,
        "by_source": by_source,
        "itunes_requests": apple.calls if apple else 0,
        "wikimedia_requests": wiki.calls if wiki else 0,
        "elapsed_seconds": round(time.time() - started, 1),
    }

    report_path = args.report or str(
        DATA_DIR / ("artist_images_backfill_%s.json"
                    % datetime.now().strftime("%Y%m%d-%H%M%S")))
    if not args.dry_run or args.report:
        Path(report_path).write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.dry_run or args.report:
        print("report: %s" % report_path)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())