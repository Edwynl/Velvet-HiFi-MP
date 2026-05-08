"""
MusicIQ Enrichment Module
─────────────────────────
① AcoustID + MusicBrainz — audio fingerprint → correct metadata + MBID
② Wikipedia — artist biography (multilingual, falls back EN→ZH→JP)
③ Last.fm — artist biography (fallback) + artist images
④ Deezer — artist images (primary source)
⑤ Discogs — master release info, vinyl editions, detailed metadata
⑥ Cover art upgrade — high-res cover from Cover Art Archive via MBID

Requirements:
  • fpcalc.exe  (Chromaprint) in PATH or same folder as server.py
    Download: https://acoustid.org/chromaprint
  • Internet access for API calls
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx

import env_loader  # noqa: F401 - load .env for direct module use

log = logging.getLogger("velvet.enrich")

DATA_DIR_ENV = os.environ.get("VELVET_DATA_DIR") or os.environ.get("DATA_DIR", "velvet_data")
DB_PATH    = Path(DATA_DIR_ENV) / "library.db"
COVERS_DIR = Path(DATA_DIR_ENV) / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)

# AcoustID API key — set ACOUSTID_KEY environment variable
ACOUSTID_KEY = os.environ.get("ACOUSTID_KEY") or os.environ.get("VELVET_ACOUSTID_KEY", "")
MB_UA = "Velvet/1.0 (velvet-personal-server)"

# Discogs API (no key needed for search, but limited)
# Get a free key at https://www.discogs.com/settings/developers for higher limits
DISCOGS_UA = "Velvet/1.0 (https://velvet.io)"

# Rate limiting for external API calls
API_SEMAPHORE = asyncio.Semaphore(8)  # Increased concurrent API calls
REQUEST_DELAY = 0.1  # Reduced delay between requests (was 0.5)

# Last.fm API (requires free API key - get one at https://www.last.fm/api/account/create)
# Get your own free key and set as environment variable LASTFM_API_KEY
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
LASTFM_KEY = os.environ.get("LASTFM_API_KEY") or os.environ.get("VELVET_LASTFM_API_KEY", "")  # Set your own key: LASTFM_API_KEY=your_key

# ─── DB helpers ───────────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn

def _query(sql, params=()):
    conn = _db()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning(f"DB query error: {e}")
        return []
    finally:
        try: conn.close()
        except: pass

def _exec(sql, params=()):
    conn = _db()
    try:
        conn.execute(sql, params)
        conn.commit()
    except Exception as e:
        log.warning(f"DB exec error: {e}")
    finally:
        try: conn.close()
        except: pass

# ─── AcoustID fingerprinting ──────────────────────────────────────────────────

def _find_fpcalc() -> Optional[str]:
    """Find fpcalc binary (Chromaprint)."""
    # Check same folder as this script first
    local = Path(__file__).parent / "fpcalc.exe"
    if local.exists():
        return str(local)
    local2 = Path(__file__).parent / "fpcalc"
    if local2.exists():
        return str(local2)
    # Check PATH
    import shutil
    return shutil.which("fpcalc")

async def fingerprint_file(file_path: str) -> Optional[tuple[str, int]]:
    """
    Run fpcalc on a file.
    Returns (fingerprint_string, duration_seconds) or None.
    """
    fpcalc = _find_fpcalc()
    if not fpcalc:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            fpcalc, "-plain", file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        lines = stdout.decode().strip().splitlines()
        duration = fp = None
        for line in lines:
            if line.startswith("DURATION="):
                duration = int(line.split("=", 1)[1])
            elif line.startswith("FINGERPRINT="):
                fp = line.split("=", 1)[1]
        if fp and duration:
            return fp, duration
    except Exception as e:
        log.debug(f"fpcalc error on {file_path}: {e}")
    return None

async def lookup_acoustid(fingerprint: str, duration: int, client: httpx.AsyncClient) -> Optional[dict]:
    """Query AcoustID API → returns best recording match."""
    try:
        resp = await client.get(
            "https://api.acoustid.org/v2/lookup",
            params={
                "client":      ACOUSTID_KEY,
                "fingerprint": fingerprint,
                "duration":    duration,
                "meta":        "recordings+releases+releasegroups+tracks+compress",
            },
            timeout=15
        )
        data = resp.json()
        if data.get("status") != "ok":
            return None
        results = data.get("results", [])
        if not results:
            return None
        # Pick highest-score result
        best = max(results, key=lambda r: r.get("score", 0))
        if best.get("score", 0) < 0.85:
            return None
        recordings = best.get("recordings", [])
        if not recordings:
            return None
        return recordings[0]   # Best recording
    except Exception as e:
        log.debug(f"AcoustID lookup error: {e}")
        return None

def _extract_mbid_meta(recording: dict) -> dict:
    """Pull useful fields from MusicBrainz recording object."""
    meta = {}
    meta["recording_mbid"] = recording.get("id")
    meta["title"] = recording.get("title")

    artists = recording.get("artists", [])
    if artists:
        meta["artist"] = artists[0].get("name")
        meta["artist_mbid"] = artists[0].get("id")

    releases = recording.get("releases", [])
    if releases:
        rel = releases[0]
        meta["album"] = rel.get("title")
        meta["release_mbid"] = rel.get("id")
        date = rel.get("date", {})
        if isinstance(date, dict):
            meta["year"] = date.get("year")
        elif isinstance(date, str) and len(date) >= 4:
            try:
                meta["year"] = int(date[:4])
            except:
                pass
        # Track number within this release
        mediums = rel.get("mediums", [])
        for medium in mediums:
            for track in medium.get("tracks", []):
                if track.get("id"):
                    meta["track_number"] = track.get("position")
                    break

    return meta

async def enrich_track_fingerprint(track_id: int) -> dict:
    """
    Fingerprint a single track, query AcoustID, update DB with correct metadata.
    Returns a status dict.
    """
    rows = _query("SELECT * FROM tracks WHERE id=?", (track_id,))
    if not rows:
        return {"status": "error", "message": "Track not found"}

    track = rows[0]
    fp_result = await fingerprint_file(track["file_path"])
    if not fp_result:
        return {"status": "error", "message": "fpcalc not found or fingerprint failed"}

    fingerprint, duration = fp_result

    async with httpx.AsyncClient(headers={"User-Agent": MB_UA}) as client:
        recording = await lookup_acoustid(fingerprint, duration, client)
        if not recording:
            return {"status": "no_match", "message": "No AcoustID match (score < 0.85)"}

        meta = _extract_mbid_meta(recording)

        # Update track title if found
        updates = []
        params  = []
        if meta.get("title"):
            updates.append("title=?"); params.append(meta["title"])
        if meta.get("track_number"):
            updates.append("track_number=?"); params.append(meta["track_number"])

        if updates:
            params.append(track_id)
            _exec(f"UPDATE tracks SET {', '.join(updates)} WHERE id=?", params)

        # Update/create artist with MBID
        if meta.get("artist"):
            existing_artist = _query("SELECT id FROM artists WHERE id=?", (track["artist_id"],))
            if existing_artist and meta.get("artist_mbid"):
                _exec("UPDATE artists SET mbid=? WHERE id=?",
                      (meta["artist_mbid"], track["artist_id"]))

        # Update album with release MBID → can be used for cover fetch
        if meta.get("release_mbid") and track.get("album_id"):
            _exec("UPDATE albums SET mbid=? WHERE id=?",
                  (meta["release_mbid"], track["album_id"]))
            # Try to fetch cover if missing
            album_rows = _query("SELECT cover_path FROM albums WHERE id=?", (track["album_id"],))
            if album_rows and not album_rows[0]["cover_path"]:
                await _fetch_cover_by_mbid(meta["release_mbid"], track["album_id"], client)

        return {
            "status":  "ok",
            "matched": meta.get("title"),
            "artist":  meta.get("artist"),
            "album":   meta.get("album"),
            "mbid":    meta.get("recording_mbid"),
        }

async def _fetch_cover_by_mbid(mbid: str, album_id: int, client: httpx.AsyncClient):
    """Fetch cover art from Cover Art Archive using release MBID."""
    try:
        await asyncio.sleep(0.5)
        resp = await client.get(
            f"https://coverartarchive.org/release/{mbid}/front-500",
            follow_redirects=True, timeout=15
        )
        if resp.status_code == 200 and resp.content:
            h = hashlib.md5(f"mb_{mbid}".encode()).hexdigest()
            path = COVERS_DIR / f"{h}.jpg"
            path.write_bytes(resp.content)
            _exec("UPDATE albums SET cover_path=? WHERE id=?", (str(path), album_id))
            log.info(f"Cover saved for album {album_id}")
    except Exception as e:
        log.debug(f"Cover fetch error: {e}")

async def batch_fingerprint(limit: int = 50, progress_callback=None) -> dict:
    """
    Fingerprint unidentified tracks in batches.
    Only processes tracks whose album has no MBID (likely not yet identified).
    Uses semaphore to limit concurrent API calls.
    """
    fpcalc = _find_fpcalc()
    if not fpcalc:
        return {"status": "error", "message": "fpcalc.exe not found. Download Chromaprint from https://acoustid.org/chromaprint"}

    # Get tracks where the album has no mbid
    tracks = _query(
        """SELECT t.id, t.file_path FROM tracks t
           JOIN albums al ON t.album_id = al.id
           WHERE al.mbid IS NULL
           ORDER BY RANDOM()
           LIMIT ?""",
        (limit,)
    )

    matched = 0; failed = 0; no_match = 0

    async with httpx.AsyncClient(headers={"User-Agent": MB_UA}) as client:
        for i, track in enumerate(tracks):
            if progress_callback:
                progress_callback(i + 1, len(tracks), track["file_path"])

            fp_result = await fingerprint_file(track["file_path"])
            if not fp_result:
                failed += 1
                continue

            fp, dur = fp_result
            # Use semaphore to limit concurrent API calls
            async with API_SEMAPHORE:
                await asyncio.sleep(0.35)   # AcoustID rate limit
                recording = await lookup_acoustid(fp, dur, client)

            if not recording:
                no_match += 1
                continue

            meta = _extract_mbid_meta(recording)
            if meta.get("release_mbid") and track.get("album_id"):
                album_id = track["album_id"]
                _exec("UPDATE albums SET mbid=? WHERE id=?", (meta["release_mbid"], album_id))
                al_row = _query("SELECT cover_path FROM albums WHERE id=?", (album_id,))
                if al_row and not al_row[0]["cover_path"]:
                    await _fetch_cover_by_mbid(meta["release_mbid"], album_id, client)

            if meta.get("title"):
                _exec("UPDATE tracks SET title=? WHERE id=?", (meta["title"], track["id"]))
                matched += 1

    return {"status": "ok", "processed": len(tracks),
            "matched": matched, "no_match": no_match, "failed": failed}

# ─── Wikipedia Artist Bio ──────────────────────────────────────────────────────

async def fetch_artist_bio(artist_name: str, lang: str = "en") -> Optional[str]:
    """
    Fetch artist biography from Wikipedia.
    Tries English first, then Simplified Chinese, then returns None.
    Strips infobox clutter, keeps first 3 meaningful paragraphs.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        for try_lang in [lang, "en", "zh"]:
            try:
                resp = await client.get(
                    f"https://{try_lang}.wikipedia.org/api/rest_v1/page/summary/{_wiki_slug(artist_name)}",
                    headers={"User-Agent": MB_UA},
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    data = resp.json()
                    extract = data.get("extract", "").strip()
                    if extract and len(extract) > 80:
                        # Remove "may refer to" disambiguation pages
                        if "may refer to" in extract.lower()[:60]:
                            continue
                        return _clean_bio(extract)
            except Exception:
                pass
    return None

def _wiki_slug(name: str) -> str:
    import urllib.parse
    return urllib.parse.quote(name, safe="")

def _clean_bio(text: str) -> str:
    """Keep first 4 sentences max, clean up Wikipedia artifacts."""
    text = re.sub(r'\([^)]*\)', '', text)     # Remove parentheticals
    text = re.sub(r'\s+', ' ', text).strip()
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return " ".join(sentences[:4]).strip()

# ─── Last.fm Artist Bio ────────────────────────────────────────────────────────

async def fetch_lastfm_bio(artist_id: int) -> dict:
    """Fetch artist biography from Last.fm as fallback."""
    if not LASTFM_KEY:
        return {"status": "not_configured", "bio": None}  # Skip if no API key

    rows = _query("SELECT id, name, bio FROM artists WHERE id=?", (artist_id,))
    if not rows:
        return {"status": "error", "message": "Artist not found"}

    artist = rows[0]
    if artist["bio"]:
        return {"status": "cached", "bio": artist["bio"]}

    artist_name = artist["name"]
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # Last.fm API - requires API key
            resp = await client.get(
                LASTFM_API,
                params={
                    "method": "artist.getinfo",
                    "artist": artist_name,
                    "api_key": LASTFM_KEY,
                    "format": "json"
                },
                headers={"User-Agent": f"{MB_UA} (Last.fm API)"}
            )
            if resp.status_code == 200:
                data = resp.json()
                artist_data = data.get("artist", {})
                bio_text = artist_data.get("bio", {}).get("summary", "")
                if bio_text and len(bio_text) > 50:
                    # Clean up Last.fm's "Read more on Last.fm" link
                    bio_text = re.sub(r'<a href="[^"]*">Read more on Last\.fm</a>', '', bio_text)
                    bio_text = re.sub(r'<[^>]+>', '', bio_text)
                    bio_text = bio_text.strip()
                    if bio_text:
                        _exec("UPDATE artists SET bio=? WHERE id=?", (bio_text, artist_id))
                        return {"status": "ok", "bio": bio_text}
        except Exception as e:
            log.debug(f"Last.fm bio fetch failed for {artist_name}: {e}")
    return {"status": "not_found", "bio": None}

# ─── MusicBrainz Artist Info ───────────────────────────────────────────────────

async def fetch_musicbrainz_artist(artist_name: str) -> dict | None:
    """Search MusicBrainz for artist info including bio and image."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            # Search for artist
            resp = await client.get(
                "https://musicbrainz.org/ws/2/artist/",
                params={
                    "query": artist_name,
                    "fmt": "json",
                    "limit": 1
                },
                headers={"User-Agent": MB_UA}
            )
            if resp.status_code == 200:
                data = resp.json()
                artists = data.get("artists", [])
                if artists:
                    mb_artist = artists[0]
                    return {
                        "mbid": mb_artist.get("id"),
                        "name": mb_artist.get("name"),
                        "sort_name": mb_artist.get("sort-name"),
                        "country": mb_artist.get("country"),
                        "type": mb_artist.get("type"),
                        "disambiguation": mb_artist.get("disambiguation")
                    }
        except Exception as e:
            log.debug(f"MusicBrainz search failed for {artist_name}: {e}")
    return None

async def enrich_artist_musicbrainz(artist_id: int) -> dict:
    """Fetch and store MusicBrainz info for an artist."""
    rows = _query("SELECT id, name, mbid FROM artists WHERE id=?", (artist_id,))
    if not rows:
        return {"status": "error", "message": "Artist not found"}

    artist = rows[0]
    if artist.get("mbid"):
        return {"status": "cached"}

    artist_info = await fetch_musicbrainz_artist(artist["name"])
    if artist_info and artist_info.get("mbid"):
        _exec("UPDATE artists SET mbid=? WHERE id=?", (artist_info["mbid"], artist_id))
        return {"status": "ok", "mbid": artist_info["mbid"]}
    return {"status": "not_found"}

async def enrich_artist_bio(artist_id: int) -> dict:
    """Fetch and store Wikipedia bio for an artist."""
    rows = _query("SELECT id, name, bio FROM artists WHERE id=?", (artist_id,))
    if not rows:
        return {"status": "error", "message": "Artist not found"}

    artist = rows[0]
    if artist["bio"]:
        return {"status": "cached", "bio": artist["bio"]}

    bio = await fetch_artist_bio(artist["name"])
    if bio:
        _exec("UPDATE artists SET bio=? WHERE id=?", (bio, artist_id))
        return {"status": "ok", "bio": bio}
    else:
        return {"status": "not_found", "bio": None}

async def batch_enrich_bios(limit: int = 500, progress_callback=None) -> dict:
    """
    Fetch bios for all artists missing them. Uses semaphore for rate limiting.
    progress_callback: function(processed, total, artist_name)
    """
    # Query artists missing bio OR image, order by id to avoid processing same artists repeatedly
    artists = _query(
        "SELECT id, name, bio, image_path FROM artists WHERE (bio IS NULL OR bio = '' OR image_path IS NULL) ORDER BY id LIMIT ?",
        (limit,)
    )
    fetched = 0; not_found = 0
    total = len(artists)

    for i, artist in enumerate(artists):
        # Use semaphore to limit concurrent API calls
        async with API_SEMAPHORE:
            # Report progress
            if progress_callback:
                progress_callback(i, total, artist["name"])

            # 1. Bio (Wikipedia first, then Last.fm as fallback)
            bio_result = await enrich_artist_bio(artist["id"])
            if bio_result["status"] != "ok":
                # Try Last.fm as fallback
                bio_result = await fetch_lastfm_bio(artist["id"])
            if bio_result.get("status") == "ok":
                fetched += 1
            elif bio_result.get("status") == "not_found":
                not_found += 1

            # 2. Photo (even if bio was cached or not found)
            if not artist.get("image_path"):
                photo_url = await search_artist_photo(artist["name"])
                if photo_url:
                    await download_artist_photo(artist["id"], photo_url)
                    fetched += 1
            await asyncio.sleep(REQUEST_DELAY)   # Reduced delay

    return {"status": "ok", "fetched": fetched, "not_found": not_found}

# ─── Artist Photo Fetching ────────────────────────────────────────────────────

async def search_artist_photo(artist_name: str) -> str | None:
    """
    Search for artist photo using multiple sources.
    Sources: Deezer > Spotify > Last.fm > DuckDuckGo
    """
    # 1. Deezer (most reliable, no API key needed)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get("https://api.deezer.com/search/artist", params={"q": artist_name})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    # Try different sizes, prefer larger ones
                    artist_data = data["data"][0]
                    return (artist_data.get("picture_xl") or
                            artist_data.get("picture_big") or
                            artist_data.get("picture_medium") or
                            artist_data.get("picture"))
        except: pass

    # 2. Last.fm (good quality images) - only if API key is configured
    # Note: Spotify requires OAuth for artist images, so not included here
    if LASTFM_KEY:
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    LASTFM_API,
                    params={
                        "method": "artist.getinfo",
                        "artist": artist_name,
                        "api_key": LASTFM_KEY,
                        "format": "json"
                    },
                    headers={"User-Agent": f"{MB_UA} (Last.fm API)"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    artist_data = data.get("artist", {})
                    images = artist_data.get("image", [])
                    # Try to get largest image
                    for img in reversed(images):
                        if img.get("#text"):
                            return img["#text"]
            except: pass

    # 3. iTunes (Artist Search) - may return artist page URL
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                "https://itunes.apple.com/search",
                params={"term": artist_name, "media": "music", "entity": "musicArtist", "limit": 1}
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results and results[0].get("artistLinkUrl"):
                    # We could scrape artistLinkUrl but it's complex.
                    pass
        except: pass

    # 3. Web Image Search Fallback (DuckDuckGo) - Mimics "Google Image"
    # This covers Tidal, KKBOX, and general high-res artist posters
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # We search for "Artist Name poster" or "Artist Name Tidal"
            for sq in [f"{artist_name} music artist poster", f"{artist_name} artist tidal profile"]:
                resp = await client.get(
                    "https://duckduckgo.com/i.js",
                    params={"q": sq, "o": "json"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    res_list = data.get("results", [])
                    if res_list:
                        # Return the first good quality image
                        return res_list[0].get("image")
        except: pass
    
    return None

async def download_artist_photo(artist_id: int, url: str) -> str | None:
    """
    Download photo and save to velvet_data/artist_images/{id}.jpg
    """
    dest_dir = COVERS_DIR.parent / "artist_images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{artist_id}.jpg"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                dest_path.write_bytes(resp.content)
                _exec("UPDATE artists SET image_path=? WHERE id=?", (str(dest_path), artist_id))
                return str(dest_path)
    except Exception as e:
        log.error(f"Failed to download artist photo: {e}")
    return None
# ─── Album Cover Search (Apple Music / KKBOX) ─────────────────────────────────

async def search_album_covers(artist: str, album: str) -> list[dict]:
    """
    Search for album covers using iTunes Search API.
    Returns a list of dicts with url, resolution, and source.
    """
    results = []
    query = f"{artist} {album}"
    
    # 1. iTunes Search API (Apple Music)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                "https://itunes.apple.com/search",
                params={"term": query, "media": "music", "entity": "album", "limit": 5},
                follow_redirects=True
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", []):
                    base_url = item.get("artworkUrl100", "")
                    if base_url:
                        url_high = base_url.replace("100x100bb.jpg", "1000x1000bb.jpg")
                        results.append({
                            "url": url_high,
                            "source": "Apple Music",
                            "album": item.get("collectionName"),
                            "artist": item.get("artistName"),
                            "res": "1000x1000"
                        })
        except Exception as e:
            log.warning(f"iTunes search failed: {e}")

    # 2. Deezer Public API
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                "https://api.deezer.com/search/album",
                params={"q": query}
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("data", []):
                    results.append({
                        "url": item.get("cover_xl") or item.get("cover_big"),
                        "source": "Deezer",
                        "album": item.get("title"),
                        "artist": item.get("artist", {}).get("name"),
                        "res": "1000x1000 (XL)"
                    })
        except Exception as e:
            log.warning(f"Deezer search failed: {e}")

    # 3. MusicBrainz + Cover Art Archive
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            mb_resp = await client.get(
                "https://musicbrainz.org/ws/2/release-group",
                params={"query": f'artist:"{artist}" AND releasegroup:"{album}"', "fmt": "json"},
                headers={"User-Agent": "MusicIQ/1.0 ( music@example.com )"}
            )
            if mb_resp.status_code == 200:
                mb_data = mb_resp.json()
                for rg in mb_data.get("release-groups", [])[:2]:
                    rg_id = rg.get("id")
                    caa_resp = await client.get(f"https://coverartarchive.org/release-group/{rg_id}")
                    if caa_resp.status_code == 200:
                        caa_data = caa_resp.json()
                        for img in caa_data.get("images", []):
                            if img.get("front"):
                                results.append({
                                    "url": img.get("image"),
                                    "source": "MusicBrainz",
                                    "album": rg.get("title"),
                                    "artist": artist,
                                    "res": "Original"
                                })
        except Exception as e:
            log.warning(f"MusicBrainz/CAA failed: {e}")

    # 4. Spotify (if available) - best for international
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            import urllib.parse
            search_query = urllib.parse.quote(f"{artist} {album}")
            resp = await client.get(
                f"https://api.spotify.com/v1/search?q={search_query}&type=album&limit=5",
                headers={"Accept": "application/json"}
            )
            # Note: Spotify requires OAuth, this will likely fail without token
        except Exception as e:
            log.debug(f"Spotify search skipped: {e}")

    # 5. Web Image Search via Bing API alternative - using thumbnail.icons.duckduckgo.com
    # This is a fallback that might work
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            import urllib.parse
            # Try to get covers from various Chinese music sites via direct URL patterns
            # These are common patterns for album covers
            query_encoded = urllib.parse.quote(f"{artist} {album} album cover")

            # Try Wikipedia as a fallback (often has album covers)
            wiki_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(album)}"
            # Don't fetch, just log - too complex to parse
        except Exception as e:
            log.debug(f"Web search failed: {e}")

    return results


async def set_album_cover_from_url(album_id: int, url: str) -> bool:
    """
    Download cover from URL and save to the album's directory as cover.jpg.
    """
    # 1. Get a track from this album to find the directory
    rows = _query("SELECT file_path FROM tracks WHERE album_id=? LIMIT 1", (album_id,))
    if not rows:
        return False
    
    track_path = Path(rows[0]["file_path"])
    album_dir = track_path.parent
    target_path = album_dir / "cover.jpg"
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                target_path.write_bytes(resp.content)
                # Update DB
                # Note: We need to import enrichment inside the function or use the local _exec
                _exec("UPDATE albums SET cover_path=? WHERE id=?", (str(target_path), album_id))
                return True
    except Exception as e:
        log.error(f"Failed to set cover from URL: {e}")
    return False

# ─── Discogs API ────────────────────────────────────────────────────────────────

async def search_discogs(artist: str, album: str, limit: int = 10) -> list[dict]:
    """
    Search Discogs database for release information.

    Returns detailed info including:
    - Master release (original version)
    - Various editions (CD, vinyl, SACD, etc.)
    - Year, country, label, catalog number
    - Average price estimates
    """
    results = []
    query = f"{artist} {album}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Search for releases
            resp = await client.get(
                "https://api.discogs.com/database/search",
                params={
                    "q": query,
                    "type": "release",
                    "per_page": limit
                },
                headers={"User-Agent": DISCOGS_UA}
            )

            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", []):
                    results.append({
                        "discogs_id": item.get("id"),
                        "title": item.get("title"),
                        "year": item.get("year"),
                        "country": item.get("country"),
                        "format": item.get("format", []),
                        "label": item.get("label", []),
                        "catno": item.get("catno"),
                        "resource_url": item.get("resource_url"),
                        "cover_image": item.get("cover_image"),
                        "thumb": item.get("thumb"),
                    })
    except Exception as e:
        log.warning(f"Discogs search failed: {e}")

    return results


async def get_discogs_release(discogs_id: int) -> Optional[dict]:
    """Get detailed release information from Discogs."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.discogs.com/releases/{discogs_id}",
                headers={"User-Agent": DISCOGS_UA}
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "discogs_id": data.get("id"),
                    "title": data.get("title"),
                    "artists": [a["name"] for a in data.get("artists", [])],
                    "year": data.get("year"),
                    "country": data.get("country"),
                    "formats": [f["name"] for f in data.get("formats", [])],
                    "labels": [l["name"] for l in data.get("labels", [])],
                    "catalog_number": data.get("identifiers", []),
                    "tracklist": [
                        {
                            "position": t.get("position"),
                            "title": t.get("title"),
                            "duration": t.get("duration")
                        }
                        for t in data.get("tracklist", [])
                    ],
                    "genres": data.get("genres", []),
                    "styles": data.get("styles", []),
                    "images": [img["uri"] for img in data.get("images", [])],
                    "lowest_price": data.get("lowest_price"),
                    "notes": data.get("notes", "")[:500],  # First 500 chars
                }
    except Exception as e:
        log.warning(f"Discogs release fetch failed: {e}")
    return None


async def get_discogs_master(master_id: int) -> Optional[dict]:
    """Get master release (definitive version) from Discogs."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.discogs.com/masters/{master_id}",
                headers={"User-Agent": DISCOGS_UA}
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "master_id": data.get("id"),
                    "title": data.get("title"),
                    "year": data.get("year"),
                    "main_release": data.get("main_release"),
                    "artists": [a["name"] for a in data.get("artists", [])],
                    "genres": data.get("genres", []),
                    "styles": data.get("styles", []),
                    "cover_image": data.get("cover_image"),
                    "tracklist": [
                        {
                            "position": t.get("position"),
                            "title": t.get("title"),
                            "duration": t.get("duration")
                        }
                        for t in data.get("tracklist", [])[:20]  # Limit to 20
                    ],
                }
    except Exception as e:
        log.warning(f"Discogs master fetch failed: {e}")
    return None


async def search_discogs_master(artist: str, album: str) -> Optional[dict]:
    """Search for master release (the definitive version)."""
    query = f"{artist} {album}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.discogs.com/database/search",
                params={
                    "q": query,
                    "type": "master"
                },
                headers={"User-Agent": DISCOGS_UA}
            )

            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    # Return first master release
                    return await get_discogs_master(results[0]["id"])
    except Exception as e:
        log.warning(f"Discogs master search failed: {e}")
    return None
