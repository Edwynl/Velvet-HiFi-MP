#!/usr/bin/env python3
"""
VELVET - Personal Music Server
A Roon-like music management system for Windows
"""

import asyncio
import collections
import concurrent.futures
import gzip
from functools import lru_cache
import io
import ipaddress
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional, AsyncGenerator
from dataclasses import dataclass, field

import env_loader  # noqa: F401 - load .env before modules read os.environ

from cachetools import TTLCache
from collections import deque

import httpx
from mutagen import File as MutagenFile
from mutagen.id3 import ID3
from PIL import Image
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Query, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import upnp_server
import remote_access
import security
from cover_art import find_folder_cover as find_folder_cover_bytes, save_cover_bytes
from dsp_engine import (
    MAX_PREAMP_DB,
    MIN_PREAMP_DB,
    REPLAYGAIN_MODES,
    DspValidationError,
    build_dsp_filter_chain,
    dsp_effective_status,
    format_autoeq_parametric_eq,
    parse_autoeq_parametric_eq,
    profile_filters,
    read_replaygain_tags,
    resolve_dsp_plan,
    resolve_replaygain,
    validate_dsp_filters,
    validate_dsp_headroom,
    validate_dsp_number,
)
from audio_streaming import (
    StreamLease, StreamSlots, TranscodeGuard, TranscodeStreamingResponse, iter_ffmpeg_stdout,
    ffmpeg_supports_soxr,
)
from db_pool import (
    DatabaseConnection,
    _return_db,
    get_db,
    get_pool_status,
    start_pool_recycle,
)
from db_schema import init_db
from library_search_index import refresh_track_search_index
from library_browse import fetch_albums, fetch_artists, fetch_tracks
from library_paging import album_catalog_filters, fetch_album_page, fetch_artist_page
from home_recommendations import fetch_home_shelves, parse_home_ids
from artist_aliases import add_alias, fetch_aliases
from artist_alias_sync import AliasSyncUnavailable, sync_artist_aliases, resolve_search_alias
from artist_alias_batch import ArtistAliasEnrichmentJob, DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE
from library_safety import AlbumFileArchive, ArchiveError, list_album_archives, restore_album_files
from library_discovery import (
    fetch_album_radio,
    fetch_artist_detail_summary,
    fetch_artist_radio,
    fetch_discover,
    fetch_genres,
    fetch_recently_added,
    search_tracks,
    search_library,
)
from library_detail import (
    fetch_album_credits,
    fetch_album_detail,
    fetch_composer_detail,
    fetch_composers,
    fetch_credit_entities,
    fetch_credit_entity_detail,
    fetch_work_detail,
    fetch_works,
)
from library_jobs import JobState, ScanJobState, create_bio_job_state, create_fingerprint_job_state
from library_duplicates import find_exact_duplicates
from maintenance_service import (
    enrich_missing_album_covers,
    refresh_album_from_files,
    refresh_artist_from_files,
    rescan_local_album_covers,
)
from library_mutation import (
    add_track_to_playlist as add_track_to_playlist_record,
    create_playlist as create_playlist_record,
    delete_playlist as delete_playlist_record,
    fetch_playlist_detail,
    fetch_playlists,
    remove_track_from_playlist as remove_track_from_playlist_record,
    toggle_album_favorite as toggle_album_favorite_record,
    update_playlist as update_playlist_record,
)
from library_runtime import (
    add_library_path,
    build_config_payload,
    record_track_play,
    remove_library_path,
    sync_music_dir_library,
)
from path_compat import filesystem_path
from scanner_service import refresh_folder_records, scan_music_dirs
from album_grouping import strip_ad_marks
from library_stats import (
    fetch_genre_albums,
    fetch_history,
    fetch_library_stats,
    fetch_listening_stats,
    fetch_most_played,
)

from chinese_variants import get_all_variants, to_simplified, to_traditional

# Request bounds for search. A very long query makes the reverse direction fall
# back to a full instr() scan per term batch, and an unbounded limit turns one
# request into an arbitrarily large JSON materialization. These are validated
# by FastAPI, so an abusive request gets a 422 instead of running the query.
MAX_SEARCH_QUERY_LENGTH = 128
MAX_SEARCH_LIMIT = 200
MAX_TRACKS_LIMIT = 500

# ─── Import Settings ──────────────────────────────────────────────────────────

from settings import get_settings, VelvetSettings, ScanConstants

# Load settings (with directory creation)
settings = get_settings()

# ─── Configuration (from Settings) ────────────────────────────────────────────

MUSIC_DIR = settings.music_dir
DATA_DIR  = settings.data_dir
DB_PATH   = settings.db_path
COVERS_DIR = settings.covers_dir
CACHE_DIR  = settings.cache_dir
PORT = settings.port
LOG_DIR = settings.log_dir

DATA_DIR.mkdir(exist_ok=True)
COVERS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ─── Thumbnail Cache ─────────────────────────────────────────────────────────
THUMBS_DIR = DATA_DIR / "thumbs"
THUMBS_DIR.mkdir(exist_ok=True)

# Thumbnail sizes for different contexts
# Smaller sizes for better mobile performance
THUMB_SIZES = {
    'small': (150, 150),   # Mobile grid - optimized for 4-column layout
    'medium': (250, 250),  # Tablet / Desktop grid
    'large': (400, 400),   # Album detail view / hero images
}
THUMB_QUALITY = 85  # JPEG quality (balanced for file size)
ARTIST_IMAGE_MAX_SIZE = (300, 300)
ARTIST_IMAGE_QUALITY = 82
ARTIST_IMAGE_CACHE_DIR = CACHE_DIR / "artist_images"
ARTIST_IMAGE_CACHE_DIR.mkdir(exist_ok=True)
ALBUM_COVER_MAX_EDGE = 1000
ALBUM_COVER_QUALITY = 90

# ─── Constants (Configurable via Settings) ─────────────────────────────────────

@dataclass
class ServerConstants:
    """Server constants - can be overridden via settings."""

    # Audio file extensions
    AUDIO_EXTENSIONS: set = field(default_factory=lambda: ScanConstants.AUDIO_EXTENSIONS.copy())

    # Upsampling rates
    UPSAMPLE_RATES: dict = field(default_factory=lambda: ScanConstants.UPSAMPLE_RATES.copy())

    # Upsampling quality presets (SoXR algorithms)
    UPSAMPLE_QUALITY: dict = field(default_factory=lambda: ScanConstants.UPSAMPLE_QUALITY.copy())

    # DSD rates for DoP output
    DSD_RATES: dict = field(default_factory=lambda: ScanConstants.DSD_RATES.copy())

    # Batch size for database commits during scanning
    SCAN_BATCH_SIZE: int = field(default_factory=lambda: settings.scan_batch_size)

    # Default pagination limits
    DEFAULT_ARTIST_LIMIT: int = field(default_factory=lambda: settings.default_artist_limit)
    DEFAULT_ALBUM_LIMIT: int = field(default_factory=lambda: settings.default_album_limit)
    DEFAULT_TRACK_LIMIT: int = field(default_factory=lambda: settings.default_track_limit)

    # Streaming settings
    STREAM_TIMEOUT_MINUTES: int = field(default_factory=lambda: settings.stream_timeout_minutes)
    STREAM_CHUNK_SIZE: int = field(default_factory=lambda: settings.stream_chunk_size)

    # MIME types
    MIME_MAP: dict = field(default_factory=lambda: {
        '.flac': 'audio/flac', '.mp3': 'audio/mpeg',
        '.m4a':  'audio/mp4',  '.aac': 'audio/aac',
        '.ogg':  'audio/ogg',  '.opus': 'audio/opus',
        '.wav':  'audio/wav',  '.aiff': 'audio/aiff',
        '.aif':  'audio/aiff', '.wv':  'audio/x-wavpack',
        '.dsf':  'audio/x-dsf', '.dff': 'audio/x-dff',
        '.wma':  'audio/x-ms-wma'
    })


# Create global constants instance
CONST = ServerConstants()

# Backward compatibility: module-level constants that reference CONST
AUDIO_EXTENSIONS = CONST.AUDIO_EXTENSIONS
UPSAMPLE_RATES = CONST.UPSAMPLE_RATES
UPSAMPLE_QUALITY = CONST.UPSAMPLE_QUALITY
DSD_RATES = CONST.DSD_RATES
SCAN_BATCH_SIZE = CONST.SCAN_BATCH_SIZE
DEFAULT_ARTIST_LIMIT = CONST.DEFAULT_ARTIST_LIMIT
DEFAULT_ALBUM_LIMIT = CONST.DEFAULT_ALBUM_LIMIT
DEFAULT_TRACK_LIMIT = CONST.DEFAULT_TRACK_LIMIT
MIME_MAP = CONST.MIME_MAP

# ─── Logging Setup with Structured Logging ────────────────────────────────────

class StructuredLogFormatter(logging.Formatter):
    """JSON-formatted log output for structured logging."""

    def __init__(self, include_request_id: bool = True):
        super().__init__()
        self.include_request_id = include_request_id

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add request ID if available in extra
        if self.include_request_id and hasattr(record, 'request_id'):
            log_data["request_id"] = record.request_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in ('name', 'msg', 'args', 'created', 'filename', 'funcName',
                           'levelname', 'levelno', 'lineno', 'module', 'msecs',
                           'pathname', 'process', 'processName', 'relativeCreated',
                           'thread', 'threadName', 'exc_info', 'exc_text', 'stack_info',
                           'request_id'):
                if not key.startswith('_'):
                    try:
                        json.dumps(value)  # Ensure serializable
                        log_data[key] = value
                    except (TypeError, ValueError):
                        log_data[key] = str(value)

        if settings.structured_logging:
            return json.dumps(log_data)
        else:
            # Human-readable format with optional request ID
            base_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            if hasattr(record, 'request_id'):
                base_format = "%(asctime)s - %(name)s - [%(request_id)s] - %(levelname)s - %(message)s"
            formatter = logging.Formatter(base_format, datefmt='%Y-%m-%d %H:%M:%S')
            return formatter.format(record)


class RequestIDAdapter(logging.LoggerAdapter):
    """Logger adapter that adds request ID to log messages."""

    def process(self, msg, kwargs):
        request_id = kwargs.pop('request_id', None)
        if request_id:
            extra = kwargs.get('extra', {})
            extra['request_id'] = request_id
            kwargs['extra'] = extra
        return msg, kwargs


# Initialize logger
log = logging.getLogger("velvet")
log.setLevel(getattr(logging, settings.log_level.upper(), logging.WARNING))

# Remove existing handlers
log.handlers.clear()

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(StructuredLogFormatter(include_request_id=True))
log.addHandler(console_handler)

# Create file handler
LOG_DIR.mkdir(exist_ok=True)
file_handler = logging.FileHandler(LOG_DIR / "server.log", mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(StructuredLogFormatter(include_request_id=True))
log.addHandler(file_handler)

# Wrap logger with request ID support
log = RequestIDAdapter(log, {})


def get_request_logger(request_id: Optional[str] = None) -> logging.LoggerAdapter:
    """Get a logger instance with optional request ID."""
    return RequestIDAdapter(
        logging.getLogger("velvet"),
        {'request_id': request_id} if request_id else {}
    )


def _log_baseline_metric(metric: str, started_at: float, **fields) -> None:
    """Emit lightweight baseline performance metrics to structured logs."""
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    payload = [f"metric={metric}", f"elapsed_ms={elapsed_ms:.2f}"]
    for key, value in fields.items():
        payload.append(f"{key}={value}")
    log.warning("[BASELINE] %s", " ".join(payload))


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """Prime network renderer discovery in the background after app startup."""
    try:
        upnp_server.refresh_renderers_async()
    except Exception as exc:
        log.debug(f"Renderer cache warm-up skipped: {exc}")
    yield


app = FastAPI(
    title="VELVET - Personal Hi-Fi Music Server",
    version="3.0",
    description="A Roon-inspired personal music manager for Windows.\n\n"
                "**Features:**\n- 🎵 Library scanning (FLAC, MP3, DSD256)\n- 🖼️ Cover art + Metadata\n- 🔍 Full-text search\n- 🌐 Multi-room streaming\n- 📻 UPnP/DLNA for HiFi devices",
    lifespan=app_lifespan,
)

# CORS origins configuration
# Default origins + allow CORS_ORIGINS env var (comma-separated URLs)
# Example: set CORS_ORIGINS=https://abc123.lt.me,https://xyz789.cf.com
_ALLOWED_ORIGINS_DEFAULT = [
    "http://localhost:8765",
    "http://127.0.0.1:8765",
]

def _get_cors_origins():
    """Honor VELVET_CORS_ORIGINS settings and the legacy CORS_ORIGINS list."""
    configured = [*_ALLOWED_ORIGINS_DEFAULT, *settings.cors_origins]
    extra = os.environ.get("CORS_ORIGINS", "")
    configured.extend(o.strip() for o in extra.split(",") if o.strip())
    return list(dict.fromkeys(configured))

ALLOWED_ORIGINS = _get_cors_origins()
log.info(f"[CORS] Allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_credentials=settings.cors_credentials,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Password"])


# Global scan state
_scan_job = ScanJobState(MUSIC_DIR)
_duplicate_job = JobState({"running": False, "progress": 0, "total": 0, "current_file": "", "groups": [], "error": None, "finished_at": None})
_alias_enrichment_job = ArtistAliasEnrichmentJob(get_db)
# One library job at a time: scans, folder refreshes and duplicate hashing all
# commit per file, and a commit from one job between another job's per-file
# read and write fails that file with "database is locked" (BUSY_SNAPSHOT).
_library_task_lock = threading.Lock()
_album_mutation_active = False

# In-memory cover cache with TTL support
_cover_cache: TTLCache = TTLCache(maxsize=10000, ttl=86400)  # album_id -> path
_cover_cache_lock = threading.Lock()

def _prime_cover_cache(rows) -> None:
    """Prime in-memory cover cache from query rows without extra file-system checks."""
    with _cover_cache_lock:
        for row in rows:
            album_id = row["id"]
            if album_id not in _cover_cache:
                _cover_cache[album_id] = row["cover_path"] or None

init_db()


def _refresh_search_index_at_startup() -> None:
    """Index tracks changed while the server was down (the first run: all)."""
    started_at = time.perf_counter()
    with DatabaseConnection() as db:
        refreshed = refresh_track_search_index(db)
        db.commit()
    if refreshed:
        log.info(f"[SEARCH] Indexed {refreshed} tracks in {time.perf_counter() - started_at:.1f}s")


_refresh_search_index_at_startup()

# ─── Metadata Extraction ───────────────────────────────────────────────────────

def _get_tag(tags, *keys) -> Optional[str]:
    for key in keys:
        val = tags.get(key)
        if val:
            if isinstance(val, (list, tuple)):
                val = val[0]
            s = str(val).strip()
            if s:
                return s
    return None

def _parse_tracknum(s: str) -> Optional[int]:
    if not s:
        return None
    try:
        return int(str(s).split('/')[0].strip())
    except:
        return None


def _get_raw_tag(tags, *keys) -> Optional[str]:
    if not tags:
        return None

    for key in keys:
        try:
            val = tags.get(key)
        except Exception:
            val = None

        if not val:
            continue

        if hasattr(val, "text"):
            val = val.text
        elif hasattr(val, "value"):
            val = val.value

        if isinstance(val, (list, tuple)):
            val = val[0] if val else None

        if isinstance(val, bytes):
            try:
                val = val.decode("utf-8", errors="ignore")
            except Exception:
                val = None

        if val is None:
            continue

        s = str(val).strip()
        if s:
            return s
    return None


def extract_metadata(file_path: str) -> dict:
    path = Path(file_path)
    readable_path = filesystem_path(file_path)
    meta = {
        'title': path.stem, 'artist': 'Unknown Artist',
        'album': 'Unknown Album', 'album_artist': None,
        'year': None, 'genre': None, 'label': None,
        'composer': None, 'work_title': None,
        'performer': None, 'conductor': None, 'ensemble': None,
        'track_number': None, 'disc_number': 1,
        'duration': None, 'sample_rate': None, 'bit_depth': None,
        'channels': None, 'bitrate': None,
        'format': path.suffix.lstrip('.').upper(),
        'cover_data': None
    }

    try:
        # Easy=True normalises tag names across formats
        audio_easy = MutagenFile(readable_path, easy=True)
        audio_raw  = MutagenFile(readable_path)

        if audio_easy:
            # Audio technical info
            if hasattr(audio_easy, 'info'):
                info = audio_easy.info
                meta['duration']    = getattr(info, 'length', None)
                meta['sample_rate'] = getattr(info, 'sample_rate', None)
                meta['channels']    = getattr(info, 'channels', None)
                meta['bitrate']     = getattr(info, 'bitrate', None)
                meta['bit_depth']   = getattr(info, 'bits_per_sample', None)

            # DSD formats
            if path.suffix.lower() in ('.dsf', '.dff'):
                meta['format'] = 'DSD'
                if meta['sample_rate']:
                    dsd_map = {2822400: 'DSD64', 5644800: 'DSD128',
                               11289600: 'DSD256', 22579200: 'DSD512'}
                    meta['format'] = dsd_map.get(meta['sample_rate'], 'DSD')

            # Tags
            if audio_easy.tags:
                t = _easy_view_of_id3(audio_easy.tags)
                meta['title']       = _get_tag(t, 'title')        or meta['title']
                meta['artist']      = _get_tag(t, 'artist')       or meta['artist']
                meta['album']       = _get_tag(t, 'album')        or meta['album']
                meta['album_artist']= _get_tag(t, 'albumartist', 'album artist')
                meta['genre']       = _get_tag(t, 'genre')
                meta['label']       = _get_tag(t, 'organization', 'label', 'publisher')
                meta['composer']    = _get_tag(t, 'composer', 'composer_sort', 'composersort')
                meta['work_title']  = _get_tag(t, 'work', 'grouping', 'contentgroup')
                meta['performer']   = _get_tag(t, 'performer', 'artists')
                meta['conductor']   = _get_tag(t, 'conductor')
                meta['ensemble']    = _get_tag(t, 'ensemble', 'orchestra')
                meta['track_number']= _parse_tracknum(_get_tag(t, 'tracknumber'))
                meta['disc_number'] = _parse_tracknum(_get_tag(t, 'discnumber')) or 1

                year_str = _get_tag(t, 'date', 'year')
                if year_str:
                    try:
                        meta['year'] = int(re.match(r'\d{4}', year_str).group())
                    except:
                        pass

        # Extract embedded cover art
        if audio_raw:
            if not meta['composer']:
                meta['composer'] = _get_raw_tag(
                    audio_raw.tags,
                    'composer', 'COMPOSER', '©wrt', 'TCOM',
                    '----:com.apple.iTunes:COMPOSER'
                )
            if not meta['work_title']:
                meta['work_title'] = _get_raw_tag(
                    audio_raw.tags,
                    'work', 'WORK', 'grouping', 'GROUPING', '©grp', 'TIT1',
                    '----:com.apple.iTunes:WORK'
                )
            if not meta['performer']:
                meta['performer'] = _get_raw_tag(
                    audio_raw.tags,
                    'performer', 'PERFORMER', 'artists', 'ARTISTS',
                    '----:com.apple.iTunes:PERFORMER'
                )
            if not meta['conductor']:
                meta['conductor'] = _get_raw_tag(
                    audio_raw.tags,
                    'conductor', 'CONDUCTOR',
                    '----:com.apple.iTunes:CONDUCTOR'
                )
            if not meta['ensemble']:
                meta['ensemble'] = _get_raw_tag(
                    audio_raw.tags,
                    'ensemble', 'ENSEMBLE', 'orchestra', 'ORCHESTRA',
                    'band', 'BAND', '----:com.apple.iTunes:ENSEMBLE'
                )
            if not meta['ensemble'] and meta['conductor'] and meta['album_artist']:
                meta['ensemble'] = meta['album_artist']
            if not meta['performer'] and (meta['conductor'] or meta['ensemble']) and meta['artist']:
                meta['performer'] = meta['artist']
            meta['cover_data'] = _extract_cover(audio_raw)

    except Exception as e:
        log.warning(f"Failed to extract metadata from {file_path}: {e}")

    for key in ('title', 'artist', 'album', 'album_artist', 'genre'):
        meta[key] = strip_ad_marks(meta[key])
    meta['title'] = meta['title'] or path.stem
    meta['artist'] = meta['artist'] or 'Unknown Artist'
    meta['album'] = meta['album'] or 'Unknown Album'
    return meta

# Easy-tag names for raw ID3 frames; WAV/AIFF return raw ID3 even with easy=True.
_ID3_EASY_FRAMES = {
    'title': 'TIT2', 'artist': 'TPE1', 'album': 'TALB', 'albumartist': 'TPE2',
    'genre': 'TCON', 'organization': 'TPUB', 'composer': 'TCOM', 'grouping': 'TIT1',
    'tracknumber': 'TRCK', 'discnumber': 'TPOS', 'date': 'TDRC', 'year': 'TYER',
}

def _easy_view_of_id3(tags):
    """Return tags addressable by easy names (raw ID3 frames are mapped)."""
    if not isinstance(tags, ID3):
        return tags
    view = {}
    for easy_key, frame_id in _ID3_EASY_FRAMES.items():
        frame = tags.get(frame_id)
        if frame is not None and getattr(frame, 'text', None):
            view[easy_key] = [str(value) for value in frame.text]
    return view

def _extract_cover(audio) -> Optional[bytes]:
    """Extract embedded cover art from any mutagen-supported format."""
    try:
        # FLAC
        if hasattr(audio, 'pictures') and audio.pictures:
            return audio.pictures[0].data
    except Exception as e:
        log.debug(f"Failed to extract FLAC cover: {e}")

    try:
        # ID3 (MP3)
        if audio.tags:
            for k, v in audio.tags.items():
                if k.startswith('APIC'):
                    return v.data
    except Exception as e:
        log.debug(f"Failed to extract ID3 cover: {e}")

    try:
        # MP4/M4A
        if audio.tags and 'covr' in audio.tags:
            covers = audio.tags['covr']
            if covers:
                return bytes(covers[0])
    except Exception as e:
        log.debug(f"Failed to extract MP4 cover: {e}")

    try:
        # Ogg/Opus
        if audio.tags and 'metadata_block_picture' in audio.tags:
            import base64
            from mutagen.flac import Picture
            data = audio.tags['metadata_block_picture'][0]
            pic = Picture(base64.b64decode(data))
            return pic.data
    except Exception as e:
        log.debug(f"Failed to extract Ogg cover: {e}")

    return None

def save_cover(cover_data: bytes, key: str) -> str:
    """Save cover art bytes to disk, return path."""
    return save_cover_bytes(cover_data, key, COVERS_DIR)


def find_folder_cover(file_path: str) -> Optional[bytes]:
    """Look for common cover filenames in the same folder."""
    return find_folder_cover_bytes(file_path, logger=log)

def scan_libraries(music_dirs: list[str], *, already_started: bool = False):
    if not already_started:
        _scan_job.begin(", ".join(music_dirs))
    started_at = time.perf_counter()

    log.warning(f"[SCAN] Starting scan of {music_dirs}")

    # Debug: Check existing records before scan
    db = get_db()
    try:
        pre_scan_tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        pre_scan_albums = db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        pre_scan_artists = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
        _scan_job.update(
            albums_before=pre_scan_albums,
            artists_before=pre_scan_artists,
            library_tracks=pre_scan_tracks,
            library_albums=pre_scan_albums,
            library_artists=pre_scan_artists,
        )
        log.warning(
            f"[SCAN] DB state before scan: {pre_scan_tracks} tracks, {pre_scan_albums} albums, {pre_scan_artists} artists"
        )

        def record_scan_file_error(message: str) -> None:
            log.warning(message)
            _scan_job.update(error=message)
            _scan_job.increment("files_failed")

        scan_result = scan_music_dirs(
            db,
            music_dirs,
            audio_extensions=AUDIO_EXTENSIONS,
            scan_batch_size=SCAN_BATCH_SIZE,
            extract_metadata=extract_metadata,
            find_folder_cover=find_folder_cover,
            save_cover=save_cover,
            set_total=lambda total: _scan_job.set("total", total),
            on_progress=lambda progress, current_file: _scan_job.update(progress=progress, current_file=current_file),
            on_track_added=lambda: _scan_job.increment("tracks_added"),
            on_track_skipped=lambda: _scan_job.increment("tracks_skipped"),
            on_track_updated=lambda: _scan_job.increment("tracks_updated"),
            on_error=record_scan_file_error,
        )
        state = _scan_job.snapshot()
        scan_metrics = scan_result.get("scan_metrics", {})
        _log_baseline_metric(
            "scan_libraries",
            started_at,
            files=scan_result.get("discovered_files", 0),
            tracks_added=state.get("tracks_added", 0),
            tracks_updated=state.get("tracks_updated", 0),
            tracks_skipped=state.get("tracks_skipped", 0),
            batch_size=SCAN_BATCH_SIZE,
            commit_count=scan_metrics.get("commit_count", 0),
            commit_avg_ms=scan_metrics.get("commit_avg_ms", 0.0),
            files_per_sec=scan_metrics.get("files_per_second", 0.0),
            sec_per_1000=scan_metrics.get("seconds_per_1000_files", 0.0),
        )

    except Exception as e:
        import traceback
        log.error(f"Scan error: {e}\n{traceback.format_exc()}")
        _scan_job.set("error", str(e))
    finally:
        db.close()
        _scan_job.finish()

# ─── MusicBrainz / Cover Art Enrichment ────────────────────────────────────────

async def enrich_album_covers():
    """Background task: fetch missing covers from MusicBrainz Cover Art Archive."""
    await enrich_missing_album_covers(
        get_db=get_db,
        http_client_factory=lambda: httpx.AsyncClient(
            headers={"User-Agent": "MusicIQ/1.0 (personal-music-server)"},
            timeout=15,
        ),
        save_cover=save_cover,
        logger=log,
    )

# ─── DLNA / Casting ───────────────────────────────────────────────────────────

@app.get("/api/devices")
def get_cast_devices():
    """Discover local DLNA renderers (like Marantz M1)."""
    return upnp_server.discover_renderers()


@app.get("/api/zones")
def get_zones(refresh: bool = Query(False, description="Force a fresh network renderer scan.")):
    """Aggregate browser, native audio, and network playback outputs."""
    cast_devices = upnp_server.refresh_renderers() if refresh else upnp_server.get_cached_renderers()
    if not refresh and not cast_devices:
        upnp_server.refresh_renderers_async()

    native_devices = []
    native_available = False
    native_error = None

    try:
        import local_playback

        native_available = local_playback.SOUNDDEVICE_AVAILABLE
        native_error = local_playback.SOUNDDEVICE_ERROR
        for idx, dev in enumerate(local_playback.get_output_devices()):
            max_output_channels = int(dev.get("max_output_channels") or 0)
            if max_output_channels <= 0:
                continue

            device_id = dev.get("index", idx)
            native_devices.append({
                "device_id": device_id,
                "name": dev.get("name", f"Output {device_id}"),
                "channels": max_output_channels,
                "sample_rate": dev.get("default_samplerate"),
                "hostapi": dev.get("hostapi"),
            })
    except Exception as e:
        native_error = str(e)

    return {
        "browser": {
            "id": "browser",
            "name": "Local Web Player",
            "type": "browser",
            "available": True,
        },
        "native": {
            "available": native_available,
            "error": native_error,
            "devices": native_devices,
        },
        "network": {
            "devices": cast_devices,
        },
    }

@app.get("/api/dlna/browse-access")
def get_dlna_browse_access():
    """Whether LAN DLNA control points may currently browse the library."""
    expires_at = security.get_security_manager().dlna_browse_expires_at()
    return {"active": bool(expires_at), "expires_at": int(expires_at) or None}


@app.post("/api/dlna/browse-access")
def grant_dlna_browse_access(body: dict = None):
    """Open a bounded window in which LAN control points (HEOS, StreamMagic,
    BubbleUPnP) may browse. They cannot send credentials, so access is limited
    to private-network clients and track/cover URLs carry scoped tickets."""
    minutes = (body or {}).get("minutes", security.DLNA_BROWSE_DEFAULT_MINUTES)
    if (not isinstance(minutes, int) or isinstance(minutes, bool)
            or not 1 <= minutes <= security.DLNA_BROWSE_MAX_MINUTES):
        raise HTTPException(422, f"minutes must be 1-{security.DLNA_BROWSE_MAX_MINUTES}")
    expires_at = security.get_security_manager().grant_dlna_browse(minutes)
    return {"active": True, "expires_at": int(expires_at)}


@app.delete("/api/dlna/browse-access")
def revoke_dlna_browse_access():
    security.get_security_manager().revoke_dlna_browse()
    return {"active": False, "expires_at": None}


@app.post("/api/cast/{device_udn}")
async def cast_to_device(device_udn: str, request: Request):
    """Tell a DLNA device to play a specific track.

    Renderers cannot send API headers, so the URLs handed to them carry
    tickets scoped to this track/cover that expire after the track length.

    With an active DSP preset the renderer gets the same server-side DSP
    transcode as the browser (24-bit FLAC at the source rate, EQ-1 headroom
    guard at that rate); otherwise the direct source file. `replaygain`
    (off|track|album) adds the file's tagged gain the same way: a non-zero
    gain also selects the transcode. `start_seconds` resumes the track there:
    the transcode starts at that offset, a direct stream is seeked by the
    renderer after Play. `recast` marks a settings change for the track the
    renderer already has: a paused renderer (or one the user just paused)
    gets the new stream loaded without Play.

    Casts and controls for one renderer run one at a time in arrival order;
    a cast superseded by a newer cast or Stop while it waited is skipped
    ({"superseded": true}), so a stale request never replaces newer playback.
    A client-chosen `request_id` lets the client fetch the outcome later
    (GET /api/cast/result/{udn}/{request_id}) if its request timed out.
    """
    data = await request.json()
    track_id = data.get("track_id")
    if not track_id:
        raise HTTPException(400, "Missing track_id")
    requested_dsp = data.get("dsp_profile_id") or None
    if requested_dsp is not None and (isinstance(requested_dsp, bool) or not isinstance(requested_dsp, int)):
        raise HTTPException(422, "dsp_profile_id must be an integer")
    start = data.get("start_seconds") or 0
    if (isinstance(start, bool) or not isinstance(start, (int, float))
            or not 0 <= start <= TRANSCODE_MAX_SECONDS):
        raise HTTPException(422, f"start_seconds must be 0-{TRANSCODE_MAX_SECONDS}")
    start = round(float(start), 3)
    replaygain_mode = data.get("replaygain", "off")
    if not isinstance(replaygain_mode, str) or replaygain_mode not in REPLAYGAIN_MODES:
        raise HTTPException(422, f"replaygain must be one of {list(REPLAYGAIN_MODES)}")
    recast = data.get("recast", False)
    if not isinstance(recast, bool):
        raise HTTPException(422, "recast must be a boolean")
    request_id = data.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 64):
        raise HTTPException(422, "request_id must be a string of 1-64 characters")

    generation = _supersede_casts(device_udn)
    _register_cast_request(device_udn, request_id)
    # The resolved stream, set below. A newer cast or Stop cancels it, which
    # wakes this cast's job at once if it is already waiting for it, so a
    # superseded lookup never holds the renderer's queue.
    prepared = concurrent.futures.Future()
    with _cast_state_lock:
        _cast_preparing.setdefault(device_udn, {})[generation] = prepared

    def superseded():
        with _cast_state_lock:
            return _cast_generations.get(device_udn) != generation

    def job():
        try:
            if superseded():
                return {"success": False, "superseded": True}  # a newer cast or Stop arrived
            try:
                stream = prepared.result(timeout=CAST_PREPARE_TIMEOUT_SECONDS)
            except concurrent.futures.CancelledError:
                if superseded():
                    return {"success": False, "superseded": True}
                return {"success": False, "cancelled": True}
            except concurrent.futures.TimeoutError:
                return {"success": False, "error": "Timed out resolving the stream"}
            if superseded():
                return {"success": False, "superseded": True}
            return _cast_now(device_udn, stream, start, recast)
        finally:
            with _cast_state_lock:
                pending = _cast_preparing.get(device_udn, {})
                if pending.get(generation) is prepared:
                    del pending[generation]

    # Queued on arrival, before the stream is resolved: a Play/Pause sent
    # after this cast always runs after it, so a slow lookup cannot let an
    # older new-track cast undo a later Pause. Its outcome is recorded for
    # /api/cast/result on every path, including cancellation.
    future = _renderer_dispatcher(device_udn).submit(
        job, on_done=lambda outcome: _record_cast_result(device_udn, request_id, outcome))
    try:
        stream = await asyncio.to_thread(_resolve_cast_stream, track_id, requested_dsp, start, replaygain_mode)
    except HTTPException as exc:
        _settle(prepared, exception=exc)
        _record_cast_result(device_udn, request_id, {"success": False, "error": exc.detail})
        raise
    except Exception as exc:
        _settle(prepared, exception=exc)
        _record_cast_result(device_udn, request_id, {"success": False, "error": "Cast failed"})
        raise
    except BaseException:  # cancelled while resolving
        prepared.cancel()
        _record_cast_result(device_udn, request_id, {"success": False, "cancelled": True})
        raise
    _settle(prepared, result=stream)
    return await asyncio.wrap_future(future)


def _settle(future: concurrent.futures.Future, result=None, exception=None) -> None:
    """Complete a cast's `prepared` future unless a newer cast or Stop cancelled it."""
    try:
        if exception is not None:
            future.set_exception(exception)
        else:
            future.set_result(result)
    except concurrent.futures.InvalidStateError:
        pass  # superseded: the job has already returned


def _resolve_cast_stream(track_id, requested_dsp, start, replaygain_mode) -> dict:
    """The URL, DIDL and processing status of the stream a renderer gets."""
    db = get_db()
    track = db.execute(
        "SELECT t.*, ar.name as artist_name, al.title as album_title "
        "FROM tracks t JOIN artists ar ON t.artist_id=ar.id "
        "JOIN albums al ON t.album_id=al.id WHERE t.id=?", (track_id,)
    ).fetchone()
    dsp_profile = None
    if requested_dsp:
        dsp_profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (requested_dsp,)).fetchone()
    db.close()

    if not track:
        raise HTTPException(404, "Track not found")
    if requested_dsp and not dsp_profile:
        # Fail closed, as /api/stream does: never play unprocessed audio for a requested preset.
        raise HTTPException(404, "DSP profile not found")

    track = dict(track)
    dsp_status = dsp_effective_status(requested_dsp, dict(dsp_profile) if dsp_profile else None,
                                      track["sample_rate"])
    tags = {}
    if replaygain_mode != "off":
        tags = read_replaygain_tags(filesystem_path(track["file_path"]))
    # The same resolution /api/stream applies to the URL handed to the renderer.
    replaygain = resolve_replaygain(tags, replaygain_mode)
    dsp_applied = dsp_status["status"] == "applied"
    replaygain_applied = replaygain["applied_db"] != 0
    transcoded = dsp_applied or replaygain_applied
    stream_query = ""
    if transcoded:
        stream_query = ((f"&dsp={requested_dsp}" if dsp_applied else "")
                        + (f"&replaygain={replaygain_mode}" if replaygain_applied else "")
                        + (f"&start={start:g}" if start else ""))
    dsp_status["stream"] = "dsp_transcode" if transcoded else "direct"
    stream_url, _ = upnp_server.media_urls(track, stream_query=stream_query)
    # Use upnp_server's DIDL-Lite builder for rich metadata (important for some hi-fi players)
    meta = upnp_server._didl_wrap(upnp_server.track_xml(
        track, "0", stream_query=stream_query, start_seconds=start if transcoded else 0))
    return {"url": stream_url, "meta": meta, "transcoded": transcoded,
            "dsp": dsp_status, "replaygain": replaygain}


def _cast_now(device_udn, stream, start, recast):
    """Cast a resolved stream (on the renderer's dispatcher thread)."""
    transcoded = stream["transcoded"]
    with _cast_state_lock:
        # The user's last Play/Pause is kept until a cast has honoured it.
        user_paused = _cast_user_paused.get(device_udn)
    paused = recast and (user_paused if user_paused is not None
                         else upnp_server.get_transport_info(device_udn) == "PAUSED_PLAYBACK")
    transport = upnp_server.cast_track(device_udn, stream["url"], stream["meta"], play=not paused) or "failed"
    if transport in ("playing", "loaded"):
        with _cast_state_lock:
            _cast_user_paused.pop(device_udn, None)
    position_restored = None
    if transport != "failed" and start and not transcoded:
        # A direct stream resumes by seeking; positions then stay absolute.
        position_restored = upnp_server.seek(device_udn, start)
        if not position_restored:
            log.warning("[CAST] Renderer did not seek to %.1fs; it plays from the start", start)
    # Renderer positions are relative to the stream: the UI adds start_offset.
    return {
        # "loaded": the new stream is set but Play was not sent (the renderer
        # was paused), so playback has not started; "play_failed": the stream
        # is set but the renderer refused Play.
        "success": transport in ("playing", "loaded"),
        "transport": transport,
        "position_restored": position_restored,
        "dsp": stream["dsp"],
        "replaygain": stream["replaygain"],
        "start_offset": start if transcoded else 0,
    }


class _RendererDispatcher:
    """Runs one renderer's SOAP jobs one at a time, in submission order.

    A worker thread exists only while jobs are queued, so waiting requests
    hold no thread-pool slot; callers await the returned future. A job
    cancelled while queued never runs. A running job cannot be cancelled
    and keeps the renderer until it returns, so jobs never overlap.
    """

    def __init__(self, name: str):
        self._name = name
        self._lock = threading.Lock()
        self._jobs: collections.deque = collections.deque()
        self._worker: threading.Thread | None = None
        self.submitted = 0  # jobs ever submitted (tests wait on it)

    def submit(self, fn, on_done=None) -> concurrent.futures.Future:
        future = concurrent.futures.Future()
        with self._lock:
            self._jobs.append((future, fn, on_done))
            self.submitted += 1
            if self._worker is None:
                self._worker = threading.Thread(target=self._drain, daemon=True,
                                                name=f"cast-{self._name}")
                self._worker.start()
        return future

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._jobs:
                    self._worker = None
                    return
                future, fn, on_done = self._jobs.popleft()
            if not future.set_running_or_notify_cancel():
                outcome = {"success": False, "cancelled": True}
            else:
                try:
                    outcome = fn()
                    future.set_result(outcome)
                except BaseException as exc:  # reported to the caller; the worker carries on
                    future.set_exception(exc)
                    detail = exc.detail if isinstance(exc, HTTPException) else "Cast failed"
                    outcome = {"success": False, "error": detail}
            if on_done:
                on_done(outcome)


_cast_state_lock = threading.Lock()
_cast_dispatchers: dict[str, _RendererDispatcher] = {}
_cast_generations: dict[str, int] = {}  # bumped by each cast and Stop
_cast_preparing: dict[str, dict[int, concurrent.futures.Future]] = {}  # generation -> unresolved stream
_cast_user_paused: dict[str, bool] = {}  # last Play/Pause not yet honoured by a cast
_cast_results: dict[str, dict[str, dict | None]] = {}  # request_id -> response (None: pending)
_CAST_RESULTS_KEPT = 16
CAST_PREPARE_TIMEOUT_SECONDS = 60


def _renderer_dispatcher(device_udn: str) -> _RendererDispatcher:
    with _cast_state_lock:
        if device_udn not in _cast_dispatchers:
            _cast_dispatchers[device_udn] = _RendererDispatcher(device_udn)
        return _cast_dispatchers[device_udn]


def _supersede_casts(device_udn: str) -> int:
    """A new cast or Stop: every cast queued before it is skipped.

    Casts still resolving their stream are woken (their `prepared` future
    is cancelled) so they leave the queue at once; one already sending SOAP
    keeps the renderer until it finishes.
    """
    with _cast_state_lock:
        generation = _cast_generations[device_udn] = _cast_generations.get(device_udn, 0) + 1
        for prepared in _cast_preparing.pop(device_udn, {}).values():
            prepared.cancel()
        return generation


def _register_cast_request(device_udn: str, request_id: str | None) -> None:
    if not request_id:
        return
    with _cast_state_lock:
        results = _cast_results.setdefault(device_udn, {})
        results[request_id] = None
        while len(results) > _CAST_RESULTS_KEPT:
            results.pop(next(iter(results)))


def _record_cast_result(device_udn: str, request_id: str | None, response: dict) -> None:
    """Publish a cast's terminal outcome; the first one stays (a superseded
    cast whose lookup fails later is still reported as superseded)."""
    if not request_id:
        return
    with _cast_state_lock:
        results = _cast_results.get(device_udn, {})
        if request_id in results and results[request_id] is None:
            results[request_id] = response


@app.get("/api/cast/result/{device_udn}/{request_id}")
def get_cast_result(device_udn: str, request_id: str):
    """The outcome of an earlier cast request, for a client whose request timed out."""
    with _cast_state_lock:
        results = _cast_results.get(device_udn, {})
        if request_id not in results:
            raise HTTPException(404, "Unknown cast request")
        response = results[request_id]
    return {"done": False} if response is None else {"done": True, **response}


@app.post("/api/cast/control/{device_udn}/{action}")
async def cast_control(device_udn: str, action: str):
    """Pause, Play, or Stop the cast device.

    Controls are never dropped: they run in arrival order with the
    renderer's casts, and a Play/Pause decides whether later re-casts play
    until one has honoured it. Stop also cancels casts still waiting, so
    leaving a renderer cannot be undone by a late one. An accepted control
    runs even if its HTTP request is cancelled while it waits (a client
    leaving the page must still stop the renderer).
    """
    if action == "Stop":
        _supersede_casts(device_udn)

    def control():
        success = upnp_server.cast_control(device_udn, action)
        with _cast_state_lock:
            if action == "Stop":
                _cast_user_paused.pop(device_udn, None)
            elif success and action in ("Play", "Pause"):
                _cast_user_paused[device_udn] = action == "Pause"
        return success

    future = _renderer_dispatcher(device_udn).submit(control)
    success = await asyncio.shield(asyncio.wrap_future(future))
    return {"success": bool(success)}

@app.get("/api/cast/status/{device_udn}")
async def get_cast_status(device_udn: str):
    """Get current playback state and position from cast device."""
    state = upnp_server.get_transport_info(device_udn)
    pos, dur = upnp_server.get_position_info(device_udn)
    return {
        "state": state,
        "position": pos,
        "duration": dur,
        "is_playing": state == "PLAYING"
    }

# ─── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    db = get_db()
    try:
        return fetch_library_stats(db, MUSIC_DIR)
    finally:
        db.close()

@app.get("/api/artists")
def get_artists(search: str = "", limit: int = 500, offset: int = 0):
    db = get_db()
    try:
        return fetch_artists(
            db,
            search=search,
            limit=limit,
            offset=offset,
            get_all_variants=get_all_variants,
            to_simplified=to_simplified,
            to_traditional=to_traditional,
        )
    finally:
        db.close()

@app.get("/api/albums/{album_id}/search-covers")
async def search_covers(album_id: int):
    """Search for album covers from multiple sources including Chinese platforms."""
    db = get_db()
    album = db.execute("SELECT ar.name as artist, al.title as album FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE al.id=?", (album_id,)).fetchone()
    db.close()
    if not album:
        raise HTTPException(404, "Album not found")

    import enrichment
    results = await enrichment.search_album_covers(album["artist"], album["album"])
    return {"results": results, "album": album["album"], "artist": album["artist"]}


@app.post("/api/albums/{album_id}/fetch-cover")
async def fetch_cover_auto(album_id: int):
    """Automatically find and save the best cover for an album."""
    db = get_db()
    album = db.execute(
        "SELECT ar.name as artist, al.title as album FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE al.id=?",
        (album_id,)
    ).fetchone()
    db.close()

    if not album:
        raise HTTPException(404, "Album not found")

    import enrichment
    # Search all sources
    results = await enrichment.search_album_covers(album["artist"], album["album"])

    if not results:
        return {"success": False, "message": "No covers found"}

    # Try to save the first (best) result
    best_cover = results[0]
    success = await enrichment.set_album_cover_from_url(album_id, best_cover["url"])

    if success:
        invalidate_cover_cache(album_id)  # Clear cache so new cover is served
        return {"success": True, "source": best_cover.get("source", "Unknown")}
    else:
        return {"success": False, "message": "Failed to download cover"}


@app.get("/api/albums/{album_id}/discogs")
async def search_discogs(album_id: int):
    """Search Discogs for album release information."""
    db = get_db()
    album = db.execute(
        "SELECT ar.name as artist, al.title as album FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE al.id=?",
        (album_id,)
    ).fetchone()
    db.close()
    if not album:
        raise HTTPException(404, "Album not found")

    import enrichment
    results = await enrichment.search_discogs(album["artist"], album["album"])
    return {"results": results, "artist": album["artist"], "album": album["album"]}


@app.get("/api/discogs/release/{discogs_id}")
async def get_discogs_release(discogs_id: int):
    """Get detailed release info from Discogs."""
    import enrichment
    result = await enrichment.get_discogs_release(discogs_id)
    if not result:
        raise HTTPException(404, "Release not found")
    return result


@app.get("/api/discogs/master")
async def get_discogs_master(artist: str = "", album: str = ""):
    """Search for master release (definitive version) on Discogs."""
    if not artist or not album:
        raise HTTPException(400, "artist and album parameters required")

    import enrichment
    result = await enrichment.search_discogs_master(artist, album)
    if not result:
        raise HTTPException(404, "Master release not found")
    return result


@app.post("/api/albums/{album_id}/set-cover")
async def set_album_cover(album_id: int, request: Request):
    data = await request.json()
    url = data.get("url")
    if not url:
        raise HTTPException(400, "URL required")

    import enrichment
    success = await enrichment.set_album_cover_from_url(album_id, url)
    if success:
        invalidate_cover_cache(album_id)  # Clear cache so new cover is served
        return {"status": "ok"}
    else:
        raise HTTPException(500, "Failed to download/save cover")

@app.get("/api/artists/page")
def get_artist_page(
    limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0),
    search: str = "", sort: str = "name",
):
    """Serve one artist page instead of all 5000 artist records to a phone."""
    started = time.perf_counter()
    db = get_db()
    try:
        result = fetch_artist_page(
            db, search=search, limit=limit, offset=offset, sort=sort,
            get_all_variants=get_all_variants,
        )
        _log_baseline_metric("artist_page", started, limit=limit, offset=offset,
                             result_count=len(result["artists"]), total=result["total"])
        return result
    finally:
        db.close()


@app.get("/api/artists/{artist_id}")
def get_artist(artist_id: int):
    """Get artist details with statistics."""
    db = get_db()
    artist = db.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()
    if not artist:
        db.close()
        raise HTTPException(404, "Artist not found")

    # Get additional stats
    stats = db.execute("""
        SELECT
            COUNT(DISTINCT t.album_id) as album_count,
            COUNT(DISTINCT t.id) as track_count,
            SUM(t.duration) as total_duration
        FROM tracks t
        JOIN track_artists ta ON t.id = ta.track_id
        WHERE ta.artist_id = ?
    """, (artist_id,)).fetchone()

    # Get genre if available
    genre = db.execute("""
        SELECT genre, COUNT(*) as count
        FROM albums
        WHERE artist_id = ? AND genre IS NOT NULL
        GROUP BY genre
        ORDER BY count DESC
        LIMIT 1
    """, (artist_id,)).fetchone()
    aliases = fetch_aliases(db, artist_id)

    db.close()

    result = dict(artist)
    result["aliases"] = aliases
    result["album_count"] = stats["album_count"] or 0
    result["track_count"] = stats["track_count"] or 0
    result["total_duration_hours"] = round((stats["total_duration"] or 0) / 3600, 1)
    result["primary_genre"] = genre["genre"] if genre else None
    return result

@app.post("/api/artists/{artist_id}/aliases")
async def create_artist_alias(artist_id: int, request: Request):
    """Add one explicitly confirmed alternate artist name.

    No automatic assumptions based on romanization: an English stage name
    needs a verified alias link, not just Traditional/Simplified conversion.
    """
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "Invalid JSON body")
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
        raise HTTPException(422, "Alias must be 1-128 characters")
    name = name.strip()
    db = get_db()
    try:
        artist = db.execute("SELECT name FROM artists WHERE id=?", (artist_id,)).fetchone()
        if artist is None:
            raise HTTPException(404, "Artist not found")
        if db.execute(
            "SELECT 1 FROM artists WHERE id != ? AND name=? COLLATE NOCASE",
            (artist_id, name),
        ).fetchone():
            raise HTTPException(409, "This name already belongs to another artist")
        try:
            added = add_alias(db, artist_id, name)
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            raise HTTPException(409, "This alias already belongs to another artist")
        return {"status": "ok", "added": bool(added),
                "aliases": fetch_aliases(db, artist_id)}
    finally:
        db.close()


@app.get("/api/library/artist-aliases/enrichment")
def artist_alias_enrichment_status():
    """Progress plus persisted overall coverage; safe to poll from any view."""
    db = get_db()
    try:
        return _alias_enrichment_job.overview(db)
    finally:
        db.close()


@app.post("/api/library/artist-aliases/enrichment/start", status_code=202)
def start_artist_alias_enrichment(body: dict):
    """Start a bounded, opt-in MusicBrainz lookup batch; never run at startup."""
    mode = body.get("mode", "pending")
    limit = body.get("limit", DEFAULT_BATCH_SIZE)
    all_remaining = body.get("all_remaining", False)
    if type(all_remaining) is not bool:
        raise HTTPException(422, "all_remaining must be a boolean")
    if mode not in ("pending", "retry"):
        raise HTTPException(422, "mode must be pending or retry")
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH_SIZE:
        raise HTTPException(422, "limit must be an integer between 1 and 50")
    db = get_db()
    try:
        with _library_task_lock:
            if _album_mutation_active or _scan_job.is_running() or _duplicate_job.is_running():
                raise HTTPException(409, "Wait for library maintenance or scanning to finish")
            try:
                result = _alias_enrichment_job.start(
                    db, mode=mode, limit=limit, all_remaining=all_remaining,
                )
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        return result
    finally:
        db.close()


@app.post("/api/library/artist-aliases/enrichment/stop")
def stop_artist_alias_enrichment():
    """Cooperatively stop after the in-flight artist; progress is persistent."""
    return _alias_enrichment_job.stop()


@app.post("/api/artists/{artist_id}/aliases/sync")
def sync_musicbrainz_artist_aliases(artist_id: int):
    """Opt-in MusicBrainz alias import on a dedicated FastAPI worker thread.

    Network awaits never hold a pooled event-loop-thread DB connection while
    unrelated async requests execute. Search itself never contacts MusicBrainz.
    """
    if _alias_enrichment_job.state.is_running():
        raise HTTPException(409, "Wait for bulk alias enrichment or stop it before a manual MusicBrainz sync")
    db = get_db()
    try:
        try:
            result = asyncio.run(sync_artist_aliases(db, artist_id))
        except ValueError:
            raise HTTPException(404, "Artist not found")
        except AliasSyncUnavailable as exc:
            raise HTTPException(502, str(exc))
        if result["status"] != "ok":
            # No partial writes on unverified or ambiguous identities.
            db.rollback()
            return result
        db.commit()
        return result
    finally:
        db.close()


@app.get("/api/artists/{artist_id}/albums")
def get_artist_albums(artist_id: int):
    db = get_db()
    # Optimized JOIN-based query to avoid N+1 subqueries
    rows = db.execute(
        """SELECT al.*,
                  COUNT(DISTINCT ta.track_id) as track_count,
                  SUM(t.duration) as total_duration,
                  MAX(CASE WHEN t.sample_rate > 44100 OR t.bit_depth > 16 THEN 1 ELSE 0 END) as is_hires
           FROM albums al
           JOIN tracks t ON t.album_id = al.id
           JOIN track_artists ta ON t.id = ta.track_id
           WHERE ta.artist_id = ?
           GROUP BY al.id
           ORDER BY al.year DESC, al.title""",
        (artist_id,)
    ).fetchall()

    _prime_cover_cache(rows)

    db.close()
    return [dict(r) for r in rows]

@app.get("/api/albums")
def get_albums(
    search: str = "", limit: int = 200, offset: int = 0,
    sort: str = "title", year: Optional[int] = None
):
    db = get_db()
    try:
        rows = fetch_albums(
            db,
            search=search,
            limit=limit,
            offset=offset,
            sort=sort,
            year=year,
            get_all_variants=get_all_variants,
            to_simplified=to_simplified,
            to_traditional=to_traditional,
        )

        _prime_cover_cache(rows)

        return rows
    finally:
        db.close()

@app.get("/api/albums/page")
def get_album_page(
    limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0),
    search: str = "", sort: str = "title", genre: str = "",
    decade: str = "", favorites_only: bool = False,
):
    """Server-side album paging: never send the entire library to phones."""
    started = time.perf_counter()
    db = get_db()
    try:
        try:
            result = fetch_album_page(
                db, search=search, limit=limit, offset=offset, sort=sort,
                genre=genre, decade=decade, favorites_only=favorites_only,
                get_all_variants=get_all_variants,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if offset == 0:
            result.update(album_catalog_filters(db))
        _prime_cover_cache(result["albums"])
        _log_baseline_metric("album_page", started, limit=limit, offset=offset,
                             result_count=len(result["albums"]), total=result["total"])
        return result
    finally:
        db.close()


@app.get("/api/albums/{album_id}")
def get_album(album_id: int, background_tasks: BackgroundTasks):
    db = get_db()
    try:
        result = fetch_album_detail(db, album_id)
    finally:
        db.close()

    if not result["cover_path"]:
        background_tasks.add_task(enrich_album_covers)

    return result


@app.get("/api/albums/{album_id}/credits")
def get_album_credits(album_id: int):
    db = get_db()
    try:
        return fetch_album_credits(db, album_id)
    finally:
        db.close()

@app.get("/api/artists/{artist_id}/image")
async def get_artist_image(artist_id: int):
    db = get_db()
    row = db.execute("SELECT image_path, name FROM artists WHERE id=?", (artist_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Artist not found")

    if row["image_path"] and os.path.exists(row["image_path"]):
        source_path = Path(row["image_path"])
        serving_path = _get_or_create_artist_image_thumb(artist_id, source_path)
        return FileResponse(
            str(serving_path),
            media_type=_guess_image_media_type(serving_path),
            headers={"Cache-Control": "max-age=86400"},
        )

    # No image yet: do not let the browser sit on this for a day. Avatars are
    # backfilled in bulk, and a cached 204 would keep showing the placeholder
    # long after the picture landed. Real images above stay cached for a day.
    return Response(status_code=204, headers={"Cache-Control": "no-store"})

@app.get("/api/artists/{artist_id}/details")
async def get_artist_details(artist_id: int):
    """Get complete artist details including bio from Wikipedia."""
    db = get_db()
    try:
        result = fetch_artist_detail_summary(db, artist_id)
    finally:
        db.close()

    # If no bio in DB, try to fetch from Wikipedia
    if not result.get("bio"):
        try:
            import enrichment
            bio_result = await enrichment.enrich_artist_bio(artist_id)
            if bio_result.get("status") == "ok":
                result["bio"] = bio_result.get("bio")
                result["bio_fetched"] = True
            else:
                result["bio_fetched"] = False
        except Exception:
            result["bio_fetched"] = False
    else:
        result["bio_fetched"] = True

    return result

@app.post("/api/search/resolve-artist-alias")
def resolve_unrecognized_artist_alias(
    q: str = Query(..., min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH),
):
    """Opt-in lookup only, never called as part of typing into search."""
    if _alias_enrichment_job.state.is_running():
        raise HTTPException(409, "Wait for bulk alias enrichment or stop it before a manual MusicBrainz lookup")
    db = get_db()
    try:
        try:
            result = asyncio.run(resolve_search_alias(db, q))
        except AliasSyncUnavailable as exc:
            db.rollback()
            raise HTTPException(502, str(exc))
        if result.get("status") == "ok":
            db.commit()
        else:
            db.rollback()
        return result
    finally:
        db.close()


@app.get("/api/search")
def search(
    q: str = Query("", max_length=MAX_SEARCH_QUERY_LENGTH),
    limit: int = Query(80, ge=1, le=MAX_SEARCH_LIMIT),
):
    started_at = time.perf_counter()
    db = get_db()
    try:
        result = search_library(
            db,
            q=q,
            limit=limit,
            get_all_variants=get_all_variants,
            to_simplified=to_simplified,
            to_traditional=to_traditional,
        )
        _log_baseline_metric(
            "search_tracks",
            started_at,
            q_len=len((q or "").strip()),
            limit=limit,
            result_count=len(result.get("tracks", [])),
        )
        return result
    finally:
        db.close()

@app.get("/api/genres")
def get_genres():
    db = get_db()
    try:
        return fetch_genres(db)
    finally:
        db.close()


@app.get("/api/composers")
def get_composers(limit: int = 500, offset: int = 0, search: str = ""):
    db = get_db()
    try:
        return fetch_composers(db, limit=limit, offset=offset, search=search)
    finally:
        db.close()


@app.get("/api/composers/{composer:path}")
def get_composer(composer: str, limit: int = 120):
    db = get_db()
    try:
        return fetch_composer_detail(db, composer=composer, limit=limit)
    finally:
        db.close()


@app.get("/api/works")
def get_works(limit: int = 500, offset: int = 0, search: str = "", composer: str = ""):
    db = get_db()
    try:
        return fetch_works(db, limit=limit, offset=offset, search=search, composer=composer)
    finally:
        db.close()


@app.get("/api/works/detail")
def get_work_detail(composer: str, title: str, limit: int = 160, role: str = "", name: str = "", version: str = ""):
    db = get_db()
    try:
        return fetch_work_detail(
            db,
            composer=composer,
            title=title,
            limit=limit,
            role=role,
            name=name,
            version=version,
        )
    finally:
        db.close()


@app.get("/api/credits")
def get_credit_entities(role: str, limit: int = 200, offset: int = 0, search: str = ""):
    db = get_db()
    try:
        return fetch_credit_entities(db, role=role, limit=limit, offset=offset, search=search)
    finally:
        db.close()


@app.get("/api/credits/detail")
def get_credit_entity_detail(role: str, name: str, limit: int = 160):
    db = get_db()
    try:
        return fetch_credit_entity_detail(db, role=role, name=name, limit=limit)
    finally:
        db.close()

@app.get("/api/genres/{genre}/albums")
def get_genre_albums(genre: str, limit: int = 500, offset: int = 0, sort: str = "year"):
    db = get_db()
    try:
        return fetch_genre_albums(db, genre=genre, limit=limit, offset=offset, sort=sort)
    finally:
        db.close()

@app.get("/api/recently-added")
def recently_added(limit: int = 30):
    db = get_db()
    try:
        rows = fetch_recently_added(db, limit)
    finally:
        db.close()

    _prime_cover_cache(rows)

    return rows

@app.get("/api/discover")
def get_discover(limit: int = 12):
    db = get_db()
    try:
        return fetch_discover(db, limit)
    finally:
        db.close()


@app.get("/api/home")
def get_home_shelves(album_ids: str = Query("", max_length=512),
                     artist_ids: str = Query("", max_length=512)):
    db = get_db()
    try:
        return fetch_home_shelves(db, parse_home_ids(album_ids, 12), parse_home_ids(artist_ids))
    finally:
        db.close()


@app.get("/api/radio/album/{album_id}")
def get_album_radio(album_id: int, limit: int = 80):
    db = get_db()
    try:
        return fetch_album_radio(db, album_id, limit)
    finally:
        db.close()


@app.get("/api/radio/artist/{artist_id}")
def get_artist_radio(artist_id: int, limit: int = 80):
    db = get_db()
    try:
        return fetch_artist_radio(db, artist_id, limit)
    finally:
        db.close()

# ─── Cover Art ────────────────────────────────────────────────────────────────

def invalidate_cover_cache(album_id: int):
    """Clear cover cache for an album when cover is updated."""
    with _cover_cache_lock:
        if album_id in _cover_cache:
            del _cover_cache[album_id]

    # Also clear thumbnail cache for this album
    for size_name in THUMB_SIZES:
        thumb_path = _get_thumb_path(album_id, size_name)
        if thumb_path.exists():
            try:
                thumb_path.unlink()
            except Exception:
                pass

def _get_thumb_path(album_id: int, size_name: str) -> Path:
    """Get the thumbnail file path for an album."""
    return THUMBS_DIR / f"{album_id}_{size_name}.jpg"


def _get_artist_thumb_path(artist_id: int) -> Path:
    """Get cached artist image thumbnail path."""
    return ARTIST_IMAGE_CACHE_DIR / f"{artist_id}_300.jpg"


def _guess_image_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _get_or_create_artist_image_thumb(artist_id: int, source_path: Path) -> Path:
    """
    Return a 300x300-capped artist image path.
    Rebuilds cache file only when source image changes.
    """
    thumb_path = _get_artist_thumb_path(artist_id)
    try:
        if thumb_path.exists() and thumb_path.stat().st_mtime >= source_path.stat().st_mtime:
            return thumb_path
    except Exception:
        pass

    try:
        with Image.open(source_path) as img:
            if img.mode in ('RGBA', 'LA', 'P', 'CMYK'):
                if img.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'RGBA':
                        background.paste(img, mask=img.split()[-1])
                    else:
                        background.paste(img)
                    img = background
                else:
                    img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            img.thumbnail(ARTIST_IMAGE_MAX_SIZE, Image.Resampling.LANCZOS)
            img.save(thumb_path, 'JPEG', quality=ARTIST_IMAGE_QUALITY, optimize=True, progressive=True)
        return thumb_path
    except Exception as e:
        log.warning(f"Failed to generate artist image thumb for {source_path}: {e}")
        return source_path

def _generate_thumbnail(source_path: Path, thumb_path: Path, size: tuple[int, int]) -> bool:
    """
    Generate a thumbnail from the source image.
    Returns True if successful, False otherwise.
    """
    try:
        with Image.open(source_path) as img:
            # Convert to RGB if necessary (for PNG with transparency, etc.)
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img

            # Calculate aspect-ratio-preserving size
            img.thumbnail(size, Image.Resampling.LANCZOS)

            # Save thumbnail
            img.save(thumb_path, 'JPEG', quality=THUMB_QUALITY, optimize=True)
        return True
    except Exception as e:
        logging.warning(f"Failed to generate thumbnail for {source_path}: {e}")
        return False

def _get_or_create_thumbnail(album_id: int, size_name: str) -> Optional[Path]:
    """
    Get existing thumbnail or create a new one.
    Thread-safe thumbnail generation.

    If thumbnail doesn't exist, returns None so the caller can fall back to full cover.
    Thumbnail generation is queued asynchronously to avoid blocking requests.
    """
    if size_name not in THUMB_SIZES:
        return None

    thumb_path = _get_thumb_path(album_id, size_name)

    # If thumbnail exists, return it immediately
    if thumb_path.exists():
        return thumb_path

    # Queue thumbnail generation for later (non-blocking)
    # This will be picked up by the background worker
    _queue_thumbnail_generation(album_id, size_name)
    return None

@app.post("/api/library/reset")
def reset_library():
    """Wipe all music metadata safely and thoroughly."""
    db = get_db()
    try:
        # 1. Get all table names to avoid "No such table" errors
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        all_tables = [row[0] for row in cursor.fetchall()]
        
        # 2. Define tables that MUST be cleared (if they exist)
        # Order matters for constraints, but we disable FKs anyway
        music_tables = [
            "playlist_tracks", "playlists", "track_artists", 
            "tracks", "albums", "artists", "play_history"
        ]
        
        db.execute("PRAGMA foreign_keys = OFF")
        
        for table in music_tables:
            if table in all_tables:
                db.execute(f"DELETE FROM {table}")
                
        # 3. Delete all physical covers and thumbnails to prevent ID/Hash mismatch
        for folder in [THUMBS_DIR, COVERS_DIR]:
            if folder.exists():
                for f in folder.glob("*.jpg"):
                    try: f.unlink()
                    except: pass
                # Also check for .png if any
                for f in folder.glob("*.png"):
                    try: f.unlink()
                    except: pass
                
        # 4. Reset scan history
        if "scan_history" in all_tables:
            db.execute("UPDATE scan_history SET status='idle', tracks_added=0, tracks_skipped=0, finished_at=NULL")
        
        db.commit()
        db.execute("VACUUM")
        db.execute("PRAGMA foreign_keys = ON")
        
        # 4. Clear in-memory caches
        with _cover_cache_lock:
            _cover_cache.clear()
            
        return {"success": True, "message": "Library fully wiped."}
    except Exception as e:
        try: db.execute("PRAGMA foreign_keys = ON")
        except: pass
        return {"success": False, "message": str(e)}
    finally:
        db.close()

# Queue for pending thumbnail generations (bounded to prevent unbounded memory growth)
_THUMB_QUEUE_MAX = 10000
_THUMB_BATCH_SIZE = 24
_THUMB_WORKERS = max(2, min((os.cpu_count() or 4), 6))
_thumb_gen_queue: deque[tuple[int, str]] = deque(maxlen=_THUMB_QUEUE_MAX)
_thumb_gen_queue_lock = threading.Lock()
_thumb_in_progress: set[tuple[int, str]] = set()
_thumb_in_progress_lock = threading.Lock()

def _queue_thumbnail_generation(album_id: int, size_name: str):
    """Queue a thumbnail for background generation."""
    with _thumb_gen_queue_lock:
        if (album_id, size_name) not in _thumb_gen_queue:
            _thumb_gen_queue.append((album_id, size_name))


def _load_cover_paths(album_ids: list[int]) -> dict[int, str]:
    if not album_ids:
        return {}

    placeholders = ",".join("?" * len(album_ids))
    with DatabaseConnection() as db:
        rows = db.execute(
            f"SELECT id, cover_path FROM albums WHERE id IN ({placeholders})",
            album_ids,
        ).fetchall()
    return {row["id"]: row["cover_path"] for row in rows if row["cover_path"]}

def _process_thumb_queue():
    """Process pending thumbnail generations from the queue."""
    items_to_process = []
    with _thumb_gen_queue_lock:
        for _ in range(_THUMB_BATCH_SIZE):
            if not _thumb_gen_queue:
                break
            items_to_process.append(_thumb_gen_queue.popleft())

    if not items_to_process:
        return

    unique_album_ids = sorted({album_id for album_id, _ in items_to_process})
    cover_map = _load_cover_paths(unique_album_ids)

    jobs: list[tuple[Path, Path, tuple[int, int], tuple[int, str]]] = []
    for album_id, size_name in items_to_process:
        thumb_key = (album_id, size_name)
        with _thumb_in_progress_lock:
            if thumb_key in _thumb_in_progress:
                continue
            _thumb_in_progress.add(thumb_key)

        thumb_path = _get_thumb_path(album_id, size_name)
        if thumb_path.exists():
            with _thumb_in_progress_lock:
                _thumb_in_progress.discard(thumb_key)
            continue

        cover_path = cover_map.get(album_id)
        if not cover_path:
            with _thumb_in_progress_lock:
                _thumb_in_progress.discard(thumb_key)
            continue

        source_path = Path(cover_path)
        if not source_path.exists():
            with _thumb_in_progress_lock:
                _thumb_in_progress.discard(thumb_key)
            continue

        jobs.append((source_path, thumb_path, THUMB_SIZES[size_name], thumb_key))

    if not jobs:
        return

    def _run_job(job: tuple[Path, Path, tuple[int, int], tuple[int, str]]) -> bool:
        source_path, thumb_path, thumb_size, _ = job
        return _generate_thumbnail(source_path, thumb_path, thumb_size)

    with ThreadPoolExecutor(max_workers=_THUMB_WORKERS, thread_name_prefix="thumb-worker") as executor:
        futures = [executor.submit(_run_job, job) for job in jobs]
        for job, future in zip(jobs, futures):
            _, _, _, thumb_key = job
            try:
                future.result()
            finally:
                with _thumb_in_progress_lock:
                    _thumb_in_progress.discard(thumb_key)

# Start background thumbnail worker
def _start_thumb_worker():
    """Start background worker for thumbnail generation."""
    def worker():
        while True:
            time.sleep(0.3)  # Check queue every 300ms
            _process_thumb_queue()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

# Start the worker
_start_thumb_worker()

@app.api_route("/api/covers/{album_id}", methods=["GET", "HEAD"])
def get_cover(album_id: int):
    # Check cache first (TTLCache handles expiration automatically)
    with _cover_cache_lock:
        cached_path = _cover_cache.get(album_id)
        if cached_path and Path(cached_path).exists():
            return FileResponse(str(cached_path), media_type="image/jpeg",
                                headers={"Cache-Control": "max-age=86400", "X-Cache": "HIT"})

    db = get_db()
    try:
        album = db.execute(
            "SELECT cover_path FROM albums WHERE id=?", (album_id,)
        ).fetchone()
    finally:
        db.close()

    if album and album["cover_path"]:
        cp = Path(album["cover_path"])
        if cp.exists():
            with _cover_cache_lock:
                _cover_cache[album_id] = str(cp)
            return FileResponse(str(cp), media_type="image/jpeg",
                                headers={"Cache-Control": "max-age=86400"})

    # Cache the negative result too
    with _cover_cache_lock:
        _cover_cache[album_id] = None
    raise HTTPException(404, "No cover")

@app.api_route("/api/covers/{album_id}/thumb", methods=["GET", "HEAD"])
def get_cover_thumbnail(album_id: int, size: str = Query("small", pattern="^(small|medium|large)$")):
    """
    Get a thumbnail-sized version of an album cover.
    Sizes: small (150x150), medium (250x250), large (400x400)
    Optimized for mobile and list views.
    """
    thumb_path = _get_or_create_thumbnail(album_id, size)

    if thumb_path and thumb_path.exists():
        return FileResponse(
            str(thumb_path),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "max-age=86400",
                "Vary": "Accept-Encoding"
            }
        )

    # Fallback to original cover if thumbnail fails
    # This ensures we never return a 404 for missing thumbnails
    return get_cover(album_id)

@app.post("/api/albums/{album_id}/cover")
async def upload_album_cover(album_id: int, file: UploadFile = File(...)):
    """Upload a custom cover for an album."""
    db = get_db()
    album = db.execute("SELECT id, title FROM albums WHERE id=?", (album_id,)).fetchone()
    if not album:
        db.close()
        raise HTTPException(404, "Album not found")
    
    # Save the file to COVERS_DIR
    ext = Path(file.filename).suffix.lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
        db.close()
        raise HTTPException(400, "Unsupported image format")

    # Validate magic bytes for JPEG and PNG
    file.file.seek(0)
    header = file.file.read(8)
    file.file.seek(0)

    if ext in ['.jpg', '.jpeg'] and not header.startswith(b'\xff\xd8'):
        db.close()
        raise HTTPException(400, "Invalid JPEG content")
    if ext == '.png' and not header.startswith(b'\x89PNG'):
        db.close()
        raise HTTPException(400, "Invalid PNG content")

    file.file.seek(0)
    raw_bytes = file.file.read()

    filename = f"custom_{album_id}.jpg"
    target_path = COVERS_DIR / filename

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            if img.mode in ('RGBA', 'LA', 'P', 'CMYK'):
                if img.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'RGBA':
                        background.paste(img, mask=img.split()[-1])
                    else:
                        background.paste(img)
                    img = background
                else:
                    img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            img.thumbnail((ALBUM_COVER_MAX_EDGE, ALBUM_COVER_MAX_EDGE), Image.Resampling.LANCZOS)
            img.save(target_path, 'JPEG', quality=ALBUM_COVER_QUALITY, optimize=True, progressive=True)
    except Exception:
        # Fallback to original bytes if image normalization fails.
        filename = f"custom_{album_id}{ext}"
        target_path = COVERS_DIR / filename
        with open(target_path, "wb") as buffer:
            buffer.write(raw_bytes)
        
    db.execute("UPDATE albums SET cover_path=? WHERE id=?", (str(target_path), album_id))
    db.commit()
    db.close()

    invalidate_cover_cache(album_id)  # Clear cache so new cover is served
    return {"success": True, "path": str(target_path)}

@app.delete("/api/albums/{album_id}")
def delete_album(album_id: int, delete_files: bool = Query(False)):
    """Remove an album's index; optionally archive audio to recoverable storage.

    Audio is never unlinked. If file staging or the SQL transaction fails,
    rollback restores the original files and retains the music index.
    Refuse removal when tracks have playlist/history references: file-only
    recovery cannot safely reconstruct those relations or their original IDs.
    """
    global _album_mutation_active
    # Hold the task reservation throughout filesystem staging and DB commit.
    with _library_task_lock:
        if _album_mutation_active or _scan_job.is_running() or _duplicate_job.is_running():
            raise HTTPException(409, "Library maintenance is busy; try again when scans finish")
        _album_mutation_active = True
    db = None
    archive = None
    try:
        db = get_db()
        # Reserve SQLite's writer before checking references, so another
        # request cannot add history/playlist links between the check and DELETE.
        db.execute("BEGIN IMMEDIATE")
        album = db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
        if not album:
            raise HTTPException(404, "Album not found")
        linked = db.execute(
            "SELECT EXISTS(SELECT 1 FROM playlist_tracks p JOIN tracks t ON t.id=p.track_id "
            "WHERE t.album_id=?) OR "
            "EXISTS(SELECT 1 FROM play_history h JOIN tracks t ON t.id=h.track_id "
            "WHERE t.album_id=?)", (album_id, album_id),
        ).fetchone()[0]
        if linked:
            raise HTTPException(409,
                "Album tracks are referenced by playlists or play history. Removal refused; "
                "files, Track IDs and associations are unchanged. File-only restore cannot "
                "preserve these associations. Keep the album to retain them.")
        tracks = db.execute("SELECT file_path FROM tracks WHERE album_id=?", (album_id,)).fetchall()
        if delete_files:
            try:
                archive = AlbumFileArchive(
                    [track["file_path"] for track in tracks],
                    _resolve_library_roots(db),
                    Path(DATA_DIR) / "album-trash",
                )
                archive.stage()
            except ArchiveError as exc:
                raise HTTPException(409, str(exc)) from exc

        try:
            db.execute("DELETE FROM tracks WHERE album_id=?", (album_id,))
            db.execute("DELETE FROM albums WHERE id=?", (album_id,))
            # Other credits/compilations may still reference this artist; do
            # not delete an artist simply because their primary tracks left.
            artist_id = album["artist_id"]
            refs = db.execute(
                "SELECT EXISTS(SELECT 1 FROM albums WHERE artist_id=?) OR "
                "EXISTS(SELECT 1 FROM tracks WHERE artist_id=?) OR "
                "EXISTS(SELECT 1 FROM track_artists WHERE artist_id=?)",
                (artist_id, artist_id, artist_id),
            ).fetchone()[0]
            if not refs:
                db.execute("DELETE FROM artists WHERE id=?", (artist_id,))
            db.commit()
        except Exception:
            db.rollback()
            if archive:
                archive.restore()
            raise

        result = {"success": True,
                  "message": "Album removed from the library index; re-scanning may assign new Track IDs",
                  "track_ids_preserved": False}
        if archive:
            try:
                archive.commit()
            except OSError as exc:
                log.error("Album archive manifest needs reconciliation: %s", exc)
                result["warning"] = "Files archived; manifest needs recovery check"
            result.update({
                "message": "Album removed; audio moved to recoverable local archive. "
                           "Restoring files and re-scanning may assign new Track IDs; "
                           "playlists and play history are not stored in file archives.",
                "archive_token": archive.token,
                "archived_files": len(archive.entries),
            })
        return result
    finally:
        if db is not None:
            if db.in_transaction:
                db.rollback()
            db.close()
        with _library_task_lock:
            _album_mutation_active = False


@app.get("/api/library/trash")
def list_library_trash():
    """List recoverable archived audio sets (never purge them implicitly)."""
    return {"archives": list_album_archives(Path(DATA_DIR) / "album-trash")}


@app.post("/api/library/trash/{token}/restore")
def restore_library_trash(token: str):
    """Restore archived audio safely while library scanning is paused."""
    global _album_mutation_active
    with _library_task_lock:
        if _album_mutation_active or _scan_job.is_running() or _duplicate_job.is_running():
            raise HTTPException(409, "Library maintenance is busy; retry when scans finish")
        _album_mutation_active = True
    try:
        try:
            return restore_album_files(Path(DATA_DIR) / "album-trash", token)
        except ArchiveError as exc:
            raise HTTPException(409, str(exc)) from exc
    finally:
        with _library_task_lock:
            _album_mutation_active = False


@app.post("/api/albums/{album_id}/open-folder")
async def open_album_folder(album_id: int):
    """Open the album's folder in Windows Explorer."""
    db = get_db()
    track = db.execute("SELECT file_path FROM tracks WHERE album_id=? LIMIT 1", (album_id,)).fetchone()
    db.close()

    if not track or not track["file_path"]:
        raise HTTPException(404, "Album folder not found")

    path = Path(track["file_path"]).parent

    # Verify the path exists and is a valid directory
    if not path.exists():
        raise HTTPException(404, "Physical folder does not exist")

    if not path.is_dir():
        raise HTTPException(400, "Path is not a directory")

    # Open in Windows Explorer using subprocess to avoid issues with os.startfile
    import subprocess
    try:
        # Use explorer.exe to open the folder
        subprocess.Popen(['explorer.exe', str(path)])
        return {"success": True}
    except Exception as e:
        log.error(f"Failed to open folder: {e}")
        raise HTTPException(500, f"Failed to open folder: {str(e)}")

@app.post("/api/albums/{album_id}/refresh")
def refresh_album_metadata(album_id: int):
    """
    Refresh album and track metadata from FLAC files.
    Use this after modifying metadata tags on disk.
    """
    try:
        db = get_db()
        def write_album_cover_file(current_album_id: int, cover_data: bytes) -> str:
            COVERS_DIR.mkdir(parents=True, exist_ok=True)
            cover_path = COVERS_DIR / f"album_{current_album_id}.jpg"
            cover_path.write_bytes(cover_data)
            return str(cover_path)
        try:
            return refresh_album_from_files(
                db,
                album_id,
                extract_metadata=extract_metadata,
                write_album_cover=write_album_cover_file,
            )
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error in refresh_album_metadata: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/albums/{album_id}/favorite")
def toggle_album_favorite(album_id: int):
    """
    Toggle favorite status for an album.
    """
    try:
        db = get_db()
        try:
            return toggle_album_favorite_record(db, album_id)
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error in toggle_album_favorite: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/artists/{artist_id}/refresh")
def refresh_artist_metadata(artist_id: int):
    """
    Refresh all albums and tracks for an artist from FLAC files.
    Also updates artist name if changed in file metadata.
    Use this after modifying metadata tags on disk.
    """
    try:
        db = get_db()
        try:
            return refresh_artist_from_files(
                db,
                artist_id,
                extract_metadata=extract_metadata,
            )
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error in refresh_artist_metadata: {e}")
        raise HTTPException(500, str(e))

# ─── Audio Streaming ──────────────────────────────────────────────────────────

def _parse_range(header: str, size: int) -> tuple[int, int]:
    """Parse one HTTP byte range, including suffix ranges.

    Invalid or unsatisfiable ranges raise ValueError for an HTTP 416 reply.
    Multiple ranges are intentionally not supported (multipart/byteranges).
    """
    if size <= 0:
        raise ValueError("empty resource")
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        raise ValueError("unsupported byte range")

    first, last = match.groups()
    if not first and not last:
        raise ValueError("missing range bounds")

    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError("invalid suffix length")
        return max(0, size - count), size - 1

    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or start > end:
        raise ValueError("range not satisfiable")
    return start, min(end, size - 1)


def _dlna_stream_headers(request: Request, mime: str, fmt: str) -> dict:
    """DLNA headers, only for renderers that ask with getcontentFeatures.dlna.org: 1."""
    if request.headers.get("getcontentFeatures.dlna.org", "").strip() != "1":
        return {}
    return {
        "transferMode.dlna.org": "Streaming",
        "contentFeatures.dlna.org": upnp_server._get_dlna_profile(mime, fmt),
    }


def _stream_file_direct(file_path: str, request: Request, fmt: str = ""):
    """Send an original audio file with RFC 9110 single-range semantics."""
    readable_path = filesystem_path(file_path)
    size = os.path.getsize(readable_path)
    ext = Path(file_path).suffix.lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")
    dlna_headers = _dlna_stream_headers(request, mime, fmt)

    range_header = request.headers.get("Range")
    if range_header:
        try:
            start, end = _parse_range(range_header, size)
        except ValueError:
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{size}",
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "no-cache",
                },
            )
        length = end - start + 1
        range_headers = {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-cache",
            **dlna_headers,
        }
        if request.method == "HEAD":
            return Response(status_code=206, media_type=mime, headers=range_headers)

        def gen():
            with open(readable_path, "rb") as audio_file:
                audio_file.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = audio_file.read(min(262144, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(gen(), status_code=206, media_type=mime, headers=range_headers)

    return FileResponse(
        readable_path,
        media_type=mime,
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache", **dlna_headers},
    )


def _resolve_library_roots(db) -> list[Path]:
    """Return configured library root directories with MUSIC_DIR fallback."""
    raw_paths: list[str] = [str(MUSIC_DIR)]
    try:
        rows = db.execute("SELECT path FROM libraries").fetchall()
        raw_paths.extend(row["path"] for row in rows if row and row["path"])
    except Exception:
        pass

    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        try:
            resolved = Path(raw).resolve()
        except Exception:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots

TRANSCODE_SLOTS = StreamSlots(settings.max_concurrent_streams)
TRANSCODE_DURATION_MARGIN_SECONDS = 30 * 60
TRANSCODE_MAX_SECONDS = 12 * 3600
# libFLAC's default block size; within the FLAC subset at every sample rate.
FLAC_TRANSCODE_BLOCK_SIZE = 4096


def _transcode_time_limit_seconds(duration_seconds, timeout_minutes: int | None = None) -> int:
    """Wall-clock cap for one FFmpeg response.

    The configured (or requested) limit is a floor: a track longer than it,
    such as a live set or opera act, gets its duration plus a 30-minute
    margin. The cap is wall-clock, so pauses consume that margin; a client
    paused longer than the margin is disconnected and the browser's recovery
    path reconnects. A stalled decoder is ended separately by the idle timeout.
    """
    configured = (timeout_minutes or settings.stream_timeout_minutes) * 60
    try:
        duration = max(float(duration_seconds or 0), 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    needed = int(duration) + TRANSCODE_DURATION_MARGIN_SECONDS if duration else 0
    return min(max(configured, needed), TRANSCODE_MAX_SECONDS)


TRANSCODE_SLOT_WAIT_SECONDS = 3.0


async def _acquire_transcode_slot() -> StreamLease:
    # A track switch briefly overlaps the closing transcode with the new one
    # (twice over when the VU meter mirrors the stream). Wait for the old
    # decoder to be reaped instead of failing the new track with 503.
    deadline = time.monotonic() + TRANSCODE_SLOT_WAIT_SECONDS
    while (lease := TRANSCODE_SLOTS.try_acquire()) is None:
        if time.monotonic() >= deadline:
            raise HTTPException(
                503,
                f"Too many concurrent transcoded streams (limit {TRANSCODE_SLOTS.limit}). "
                "Stop another stream or play without DSP/upsampling.",
                headers={"Retry-After": "5"},
            )
        await asyncio.sleep(0.05)
    return lease


def _ffmpeg_input_args(file_path: str, start_seconds: float = 0) -> list[str]:
    # -ss before -i: seek in the demuxer, then decode exactly from there.
    seek = ["-ss", f"{start_seconds:g}"] if start_seconds else []
    return [*seek, "-i", filesystem_path(file_path)]


async def _stream_upsampled(file_path: str, target_rate: str, quality: str = "high",
                           timeout_seconds: int = 3600, dsp_filter: str = "",
                           start_seconds: float = 0):
    """
    Upsample via FFmpeg + SoXR resampler, stream as WAV.

    Uses SoXR High-Quality mode for superior audio quality compared to basic SoX.
    SoXR provides polynomial interpolation with 64-bit floating point precision.

    Args:
        file_path: Path to the source audio file
        target_rate: Target sample rate as string (e.g., "705600" for 705.6kHz)
        quality: standard, high, extreme (libsoxr precision presets)
        timeout_seconds: Wall-clock cap for the response
        dsp_filter: Additional DSP filter chain to apply
        start_seconds: Start this far into the track
    """
    if not await asyncio.to_thread(ffmpeg_supports_soxr):
        raise HTTPException(503, "PCM upsampling requires FFmpeg with libsoxr. Install a full build and restart VELVET.")
    lease = await _acquire_transcode_slot()
    # Get SoXR quality setting
    soxr_quality = UPSAMPLE_QUALITY.get(quality, "soxr:precision=28")

    # Build filter chain: DSP + Upsampling
    filters = []
    if dsp_filter:
        filters.append(dsp_filter)
    filters.append(f"aresample=resampler={soxr_quality}:out_sample_rate={target_rate}")
    filter_chain = ",".join(filters)

    # Build FFmpeg command with SoXR resampler
    # Key improvements:
    # 1. Use libsoxr with validated precision presets
    # 2. output without compression for lower latency
    # 3. Use 32-bit integer for maximum precision in pipeline
    cmd = [
        "ffmpeg", "-nostdin", *_ffmpeg_input_args(file_path, start_seconds),
        "-af", filter_chain,
        "-c:a", "pcm_s32le",      # 32-bit integer for best precision
        "-f", "wav",
        "pipe:1",
        "-loglevel", "error"
    ]

    # Releases the slot only after FFmpeg has exited (or never started).
    guard = TranscodeGuard(lease.release)
    return TranscodeStreamingResponse(
        iter_ffmpeg_stdout(cmd, timeout_seconds=timeout_seconds, guard=guard),
        guard=guard,
        media_type="audio/wav",
        headers={
            "X-Upsampled-Rate": target_rate,
            "X-Upsampled-By":   "FFmpeg+SoXR",
            "X-Quality":        quality,
            "Cache-Control":    "no-cache"
        }
    )


async def _stream_with_dsp(file_path: str, dsp_filter: str, timeout_seconds: int = 3600,
                           start_seconds: float = 0):
    """
    Stream audio with DSP processing only (no upsampling).

    Args:
        file_path: Path to the source audio file
        dsp_filter: FFmpeg filter chain to apply
        timeout_seconds: Wall-clock cap for the response
        start_seconds: Start this far into the track (DLNA re-cast resume)
    """
    lease = await _acquire_transcode_slot()
    cmd = [
        "ffmpeg", "-nostdin", *_ffmpeg_input_args(file_path, start_seconds),
        # The gain is already in the samples: copying the source's
        # REPLAYGAIN_*/R128_* tags would let a player apply it twice.
        "-map_metadata", "-1",
        "-af", dsp_filter,
        "-c:a", "flac",
        "-compression_level", "0",
        # Fixed blocks: by default the encoder takes its block size from the
        # first decoded frame, which after -ss can be a few samples (FLAC's
        # minimum is 16) and FFmpeg then exits without writing any audio.
        "-frame_size", str(FLAC_TRANSCODE_BLOCK_SIZE),
        "-f", "flac",
        "pipe:1",
        "-loglevel", "error"
    ]

    # Releases the slot only after FFmpeg has exited (or never started).
    guard = TranscodeGuard(lease.release)
    return TranscodeStreamingResponse(
        iter_ffmpeg_stdout(cmd, timeout_seconds=timeout_seconds, guard=guard),
        guard=guard,
        media_type="audio/flac",
        headers={
            "X-Output-Mode": "DSP",
            "Cache-Control": "no-cache"
        }
    )


def _select_pcm_upsample_rate(source_rate: int | None, multiplier: str) -> str | None:
    """Preserve the source's 44.1k/48k sample-rate family when upsampling."""
    factors = {"none": 1, "2x": 2, "4x": 4, "8x": 8, "16x": 16}
    if multiplier not in factors:
        raise ValueError("Unsupported upsampling multiplier")
    if multiplier == "none":
        return None
    if source_rate is None or int(source_rate) <= 0:
        raise ValueError("Source sample rate is unknown; rescan audio metadata")
    output_rate = int(source_rate) * factors[multiplier]
    # Protect against accidental very high-rate transcoding and wasted CPU.
    if output_rate > 768000 or output_rate < 8000:
        raise ValueError("Requested PCM output sample rate is unsupported")
    return str(output_rate)


@app.post("/api/stream-ticket")
def create_stream_ticket():
    """Issue the browser's short-lived, stream-only media ticket.

    Media elements cannot send X-API-Key, so stream URLs carry this ticket
    instead of the long-lived API key. It authorizes only audio reads
    (GET/HEAD /api/stream/*), never covers or the API, expires after
    BROWSER_STREAM_TICKET_SECONDS and is revoked by any credential change.
    The client fetches it ahead of playback so starting a track needs no
    network round trip before audio.play().
    """
    ticket, expires_at = security.issue_browser_stream_ticket()
    return JSONResponse({"ticket": ticket, "expires_at": expires_at},
                        headers={"Cache-Control": "no-store"})


# DLNA renderers commonly probe media with HEAD before GET.
@app.api_route("/api/stream/{track_id}", methods=["GET", "HEAD"])
async def stream_track(
    track_id: int,
    request: Request,
    upsample: str = Query("none", description="none|2x|4x|8x|16x"),
    quality: str = Query("high", description="standard|high|extreme - upsampling quality"),
    dop: str = Query("none", description="none|dsd64|dsd128|dsd256 - DSD over PCM output"),
    dsp: int = Query(None, description="DSP profile ID to apply"),
    start: float = Query(0, ge=0, le=TRANSCODE_MAX_SECONDS,
                         description="Start offset in seconds (transcoded streams only)"),
    replaygain: Literal["off", "track", "album"] = Query(
        "off", description="ReplayGain from the file's tags: off|track|album"),
    timeout_minutes: int | None = Query(
        None, ge=5, le=720,
        description="Transcode time limit in minutes (default: VELVET_STREAM_TIMEOUT_MINUTES); "
                    "always extended to cover the track duration",
    ),
):
    """
    Stream a track with optional upsampling and DSP processing.

    Parameters:
    - upsample: Target multiplier (none, 2x, 4x, 8x, 16x)
    - quality: SoXR quality preset (standard=quick, high=balanced, extreme=best)
    - dop: DSD over PCM output (none, dsd64, dsd128, dsd256)
    - dsp: DSP profile ID to apply (parametric EQ, volume, etc.)
    - start: Start offset for a transcoded (DSP/upsampled) stream; direct
      streams seek with HTTP Range instead
    - replaygain: static track/album gain from tags (see /api/tracks/{id}/replaygain)
    - timeout_minutes: Minimum transcode time limit; long tracks get their full length
    """
    db = get_db()
    track = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
    library_roots = _resolve_library_roots(db)

    # Get DSP profile if specified
    dsp_profile = None
    if dsp:
        dsp_profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (dsp,)).fetchone()
        if dsp_profile:
            dsp_profile = dict(dsp_profile)

    db.close()

    if not track:
        raise HTTPException(404, "Track not found")
    if dsp and not dsp_profile:
        # Fail closed: never stream unprocessed audio for a requested preset.
        raise HTTPException(404, "DSP profile not found")

    fp = track["file_path"]
    # Validate resolved path is within configured library roots.
    # This keeps traversal protection while supporting multi-library setups.
    try:
        resolved_path = Path(fp).resolve()
    except Exception:
        raise HTTPException(403, "Access denied: invalid path")

    in_allowed_root = any(
        resolved_path == root or resolved_path.is_relative_to(root)
        for root in library_roots
    )
    if not in_allowed_root:
        raise HTTPException(403, "Access denied: path outside configured libraries")

    if not Path(filesystem_path(fp)).exists():
        raise HTTPException(404, f"File not found on disk: {fp}")

    # Build DSP filter chain
    replaygain_db = 0.0
    if replaygain != "off":
        tags = await asyncio.to_thread(read_replaygain_tags, filesystem_path(fp))
        replaygain_db = resolve_replaygain(tags, replaygain)["applied_db"]
    dsp_filter = build_dsp_filter_chain(dsp_profile, track["sample_rate"], replaygain_db)

    # A WAV/PCM stream is not DoP: until genuine DSD marker packing and
    # compatible DAC transport are implemented, fail clearly instead of
    # presenting ordinary PCM as DSD output.
    if dop != "none":
        if dop not in DSD_RATES:
            raise HTTPException(422, "Unsupported DoP mode")
        raise HTTPException(
            501,
            "DoP output is not implemented. Choose PCM output (dop=none).",
        )

    # Handle PCM upsampling with optional DSP. A 48k source played at
    # 2x must become 96k, not a hard-coded 88.2k.
    try:
        target_rate = _select_pcm_upsample_rate(track["sample_rate"], upsample)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc))
    if target_rate and quality not in UPSAMPLE_QUALITY:
        raise HTTPException(422, "Unsupported upsampling quality")
    if start and not (target_rate or dsp_filter):
        raise HTTPException(422, "start applies only to transcoded (DSP or upsampled) streams")
    if request.method == "HEAD" and (target_rate or dsp_filter):
        # Describe the transcoded stream without starting FFmpeg.
        return Response(media_type="audio/wav" if target_rate else "audio/flac",
                        headers={"Cache-Control": "no-cache"})
    if target_rate:
        limit = _transcode_time_limit_seconds(track["duration"], timeout_minutes)
        return await _stream_upsampled(fp, target_rate, quality, limit, dsp_filter, start_seconds=start)

    # Handle DSP-only (no upsampling)
    if dsp_filter:
        limit = _transcode_time_limit_seconds(track["duration"], timeout_minutes)
        return await _stream_with_dsp(fp, dsp_filter, limit, start_seconds=start)

    return _stream_file_direct(fp, request, track["format"] or "")

# ─── Scan API ─────────────────────────────────────────────────────────────────

# Scan rate limiting - prevent rapid consecutive scans
_SCAN_COOLDOWN_SECONDS = 10  # Minimum seconds between scan requests

async def _scan_libraries_async(music_dirs: list[str]):
    """
    Async wrapper for scan_libraries.
    Uses asyncio.to_thread() to run the synchronous file system operations
    in a thread pool, preventing blocking of the event loop.
    """
    await asyncio.to_thread(scan_libraries, music_dirs, already_started=True)

def _record_library_totals(tracks: int, albums: int, artists: int) -> None:
    """Library totals before a scan; the import progress panel diffs against them."""
    _scan_job.update(
        albums_before=albums,
        artists_before=artists,
        library_tracks=tracks,
        library_albums=albums,
        library_artists=artists,
    )


def _claim_library_scan(begin) -> str | None:
    """Mark a scan/refresh running before it is queued, or return why not.

    Marking it here rather than in the worker thread closes the window in
    which a duplicate check could start before the scan thread does.
    """
    with _library_task_lock:
        if _album_mutation_active:
            return "Album file maintenance in progress"
        if _scan_job.is_running():
            return "Scan already in progress"
        if _alias_enrichment_job.state.is_running():
            return "Stop artist name enrichment before scanning the music library"
        if _duplicate_job.is_running():
            return "Duplicate check in progress; scan again when it finishes"
        begin()
        return None


@app.post("/api/scan")
async def start_scan(background_tasks: BackgroundTasks, body: dict = None):
    allowed, remaining = _scan_job.check_and_mark_request(_SCAN_COOLDOWN_SECONDS)
    if not allowed:
        return {
            "message": f"Please wait {remaining} seconds before requesting another scan",
            "cooldown_remaining": remaining,
            "state": _scan_job.snapshot(),
        }

    # Check if specific folders are requested (for single folder refresh)
    requested_folders = None
    if body and body.get("folders"):
        requested_folders = body["folders"]

    db = get_db()
    all_libraries = [row["path"] for row in db.execute("SELECT path FROM libraries").fetchall()]
    pre_scan_tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    pre_scan_albums = db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    pre_scan_artists = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    db.close()

    if requested_folders:
        # Only scan requested folders (validate they exist in config)
        libraries = [f for f in requested_folders if f in all_libraries]
        if not libraries:
            return {"message": "Folder not found in library configuration", "error": True}
    else:
        # Scan all libraries
        libraries = all_libraries

    if not libraries:
        # Fallback to default if nothing in DB yet (for first scan)
        libraries = [MUSIC_DIR]

    def begin_scan() -> None:
        _scan_job.begin(", ".join(libraries))
        _record_library_totals(pre_scan_tracks, pre_scan_albums, pre_scan_artists)

    busy = _claim_library_scan(begin_scan)
    if busy:
        return {"message": busy, "state": _scan_job.snapshot()}
    background_tasks.add_task(_scan_libraries_async, libraries)
    return {
        "message": f"Scan started ({len(libraries)} folder{'s' if len(libraries) > 1 else ''})",
        "libraries": libraries,
        "state": _scan_job.snapshot(),
    }

@app.post("/api/scan/refresh-folder")
async def refresh_folder_metadata(background_tasks: BackgroundTasks, body: dict = None):
    """
    Force re-read metadata from all audio files in a specific folder.
    Deletes existing tracks and re-inserts with fresh metadata.
    Use this after modifying metadata tags in files.
    """
    if not body or not body.get("folder"):
        raise HTTPException(400, "Missing 'folder' parameter")

    folder_path = body["folder"]

    # Validate folder exists in library config
    db = get_db()
    all_libraries = [row["path"] for row in db.execute("SELECT path FROM libraries").fetchall()]
    pre_scan_tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    pre_scan_albums = db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    pre_scan_artists = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    db.close()

    if folder_path not in all_libraries:
        raise HTTPException(400, "Folder not found in library configuration")

    def begin_refresh() -> None:
        _scan_job.begin_refresh(folder_path)
        _record_library_totals(pre_scan_tracks, pre_scan_albums, pre_scan_artists)

    busy = _claim_library_scan(begin_refresh)
    if busy:
        return {"message": busy, "state": _scan_job.snapshot()}
    background_tasks.add_task(_refresh_folder_async, folder_path)
    return {"message": f"Refresh started for folder: {folder_path}", "state": _scan_job.snapshot()}

async def _refresh_folder_async(folder_path: str):
    """Async wrapper for folder metadata refresh."""
    await asyncio.to_thread(_refresh_folder_sync, folder_path, already_started=True)

def _refresh_folder_sync(folder_path: str, *, already_started: bool = False):
    """
    Synchronous folder metadata refresh - deletes and re-inserts all tracks.
    """
    if not already_started:
        _scan_job.begin_refresh(folder_path)

    log.warning(f"[REFRESH] Starting metadata refresh of {folder_path}")

    db = get_db()

    try:
        existing_count = db.execute(
            "SELECT COUNT(*) FROM tracks WHERE file_path LIKE ?",
            (f"{folder_path}%",),
        ).fetchone()[0]

        if not existing_count:
            log.warning(f"[REFRESH] No tracks found in {folder_path}")
            _scan_job.finish(current_file="No tracks found")
            return

        result = refresh_folder_records(
            db,
            folder_path,
            audio_extensions=AUDIO_EXTENSIONS,
            extract_metadata=extract_metadata,
            find_folder_cover=find_folder_cover,
            save_cover=save_cover,
            set_total=lambda total: _scan_job.set("total", total),
            on_progress=lambda progress, current_file: _scan_job.update(progress=progress, current_file=current_file),
            on_track_added=lambda: _scan_job.increment("tracks_added"),
            on_track_updated=lambda: _scan_job.increment("tracks_updated"),
            on_error=lambda message: log.error(message),
            scan_batch_size=SCAN_BATCH_SIZE,
        )

        log.warning(f"[REFRESH] Deleted {result['deleted_tracks']} tracks from {folder_path}")
        log.warning(f"[REFRESH] Completed. Added {result['discovered_files']} tracks")

    except Exception as e:
        log.error(f"[REFRESH] Error: {e}")
        _scan_job.set("error", str(e))
    finally:
        _scan_job.finish()
        db.close()


def _duplicate_scan_worker() -> None:
    db = None
    try:
        db = get_db()
        roots = _resolve_library_roots(db)
        result = find_exact_duplicates(
            db, roots,
            on_progress=lambda progress, total, name: _duplicate_job.update(
                progress=progress, total=total, current_file=name,
            ),
        )
        _duplicate_job.finish(**result, current_file="")
    except Exception as exc:
        log.exception("Duplicate scan failed")
        _duplicate_job.finish(error=str(exc), current_file="")
    finally:
        if db is not None:
            db.close()


@app.post("/api/library/duplicates/scan", status_code=202)
def start_duplicate_scan(background_tasks: BackgroundTasks):
    """Queue a read-only, byte-exact duplicate check in the background."""
    with _library_task_lock:
        if _album_mutation_active:
            raise HTTPException(409, "Album file maintenance in progress")
        if _scan_job.is_running():
            raise HTTPException(409, "Finish the library scan before checking duplicates")
        if _duplicate_job.is_running():
            raise HTTPException(409, "Duplicate scan already running")
        if _alias_enrichment_job.state.is_running():
            raise HTTPException(409, "Stop artist name enrichment before duplicate scanning")
        _duplicate_job.mark_running(
            progress=0, total=0, current_file="", groups=[], error=None,
            finished_at=None, candidate_files=0, files_examined=0,
            cached_hashes=0, truncated=False,
        )
    background_tasks.add_task(_duplicate_scan_worker)
    return {"message": "Duplicate scan started", "status": "running"}


@app.get("/api/library/duplicates")
def duplicate_scan_status():
    """Return progress and byte-identical groups; never delete music."""
    return _duplicate_job.snapshot()


@app.get("/api/scan/status")
def scan_status():
    state = _scan_job.snapshot()
    db = get_db()
    try:
        library_tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        library_albums = db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        library_artists = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    finally:
        db.close()

    state.update(
        library_tracks=library_tracks,
        library_albums=library_albums,
        library_artists=library_artists,
        albums_added=max(0, library_albums - int(state.get("albums_before") or 0)),
        artists_added=max(0, library_artists - int(state.get("artists_before") or 0)),
    )
    return state

@app.post("/api/scan/enrich-covers")
async def enrich_covers(background_tasks: BackgroundTasks):
    """Fetch missing covers from MusicBrainz (runs in background)."""
    background_tasks.add_task(enrich_album_covers)
    return {"message": "Cover enrichment started (MusicBrainz)"}


@app.post("/api/scan/rescan-covers")
def rescan_local_covers():
    """Rescan albums and look for local cover files (cover.jpg, folder.jpg, etc.)."""
    db = get_db()
    try:
        return rescan_local_album_covers(
            db,
            save_cover=save_cover,
        )
    finally:
        db.close()


@app.post("/api/thumbs/generate")
def generate_thumbnails(background_tasks: BackgroundTasks):
    """
    Generate thumbnails for all albums in the background.
    This pre-generates all thumbnail sizes for faster mobile browsing.
    """
    def _generate_all_thumbnails():
        db = get_db()
        try:
            albums = db.execute("SELECT id, cover_path FROM albums WHERE cover_path IS NOT NULL").fetchall()
        finally:
            db.close()

        jobs: list[tuple[Path, Path, tuple[int, int]]] = []
        for album in albums:
            source_path = Path(album["cover_path"])
            if not source_path.exists():
                continue

            album_id = album["id"]
            for size_name, size in THUMB_SIZES.items():
                thumb_path = _get_thumb_path(album_id, size_name)
                if thumb_path.exists():
                    continue
                jobs.append((source_path, thumb_path, size))

        generated = 0
        failed = 0
        if jobs:
            with ThreadPoolExecutor(max_workers=_THUMB_WORKERS, thread_name_prefix="thumb-prebuild") as executor:
                futures = [executor.submit(_generate_thumbnail, src, thumb, size) for src, thumb, size in jobs]
                for future in futures:
                    if future.result():
                        generated += 1
                    else:
                        failed += 1

        log.info(f"Thumbnail generation complete: {generated} created, {failed} failed")

    background_tasks.add_task(_generate_all_thumbnails)
    return {"message": "Thumbnail generation started in background"}


@app.get("/api/thumbs/status")
def thumb_status():
    """Get thumbnail generation status."""
    db = get_db()
    try:
        total_albums = db.execute("SELECT COUNT(*) as cnt FROM albums WHERE cover_path IS NOT NULL").fetchone()["cnt"]
    finally:
        db.close()

    # Count existing thumbnails
    existing_thumbs = 0
    for f in THUMBS_DIR.glob("*.jpg"):
        existing_thumbs += 1

    # Calculate coverage
    max_thumbs_per_album = len(THUMB_SIZES)
    expected_thumbs = total_albums * max_thumbs_per_album
    coverage = (existing_thumbs / expected_thumbs * 100) if expected_thumbs > 0 else 0

    return {
        "total_albums": total_albums,
        "existing_thumbnails": existing_thumbs,
        "expected_thumbnails": expected_thumbs,
        "coverage_percent": round(coverage, 1),
        "thumb_sizes": list(THUMB_SIZES.keys()),
        "thumbs_directory": str(THUMBS_DIR)
    }


@app.post("/api/thumbs/clear")
def clear_thumbnails():
    """Clear all cached thumbnails to regenerate them."""
    cleared = 0
    for f in THUMBS_DIR.glob("*.jpg"):
        try:
            f.unlink()
            cleared += 1
        except Exception:
            pass
    return {"cleared": cleared, "message": f"Cleared {cleared} thumbnails"}


# ─── Enrichment API ───────────────────────────────────────────────────────────

_fingerprint_job = create_fingerprint_job_state()

# Bio enrichment state
_bio_enrich_job = create_bio_job_state()

async def _run_batch_fingerprint():
    _fingerprint_job.mark_running(
        processed=0,
        total=0,
        current="",
        matched=0,
        finished_at=None,
        error=None,
    )
    try:
        import enrichment
        def cb(i, total, fp):
            _fingerprint_job.update(processed=i, total=total, current=Path(fp).name)
        result = await enrichment.batch_fingerprint(limit=200, progress_callback=cb)
        _fingerprint_job.set("matched", result.get("matched", 0))
    except Exception as e:
        _fingerprint_job.set("error", str(e))
    finally:
        _fingerprint_job.finish()

@app.post("/api/enrich/fingerprint")
async def start_fingerprint(background_tasks: BackgroundTasks):
    if _fingerprint_job.is_running():
        return {"message": "Already running", "state": _fingerprint_job.snapshot()}
    background_tasks.add_task(_run_batch_fingerprint)
    return {"message": "Fingerprint enrichment started"}

@app.get("/api/enrich/fingerprint/status")
def fingerprint_status():
    return _fingerprint_job.snapshot()

@app.post("/api/enrich/fingerprint/{track_id}")
async def fingerprint_track(track_id: int):
    try:
        import enrichment
        result = await enrichment.enrich_track_fingerprint(track_id)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/enrich/bios")
async def start_bio_enrichment(background_tasks: BackgroundTasks):
    if _bio_enrich_job.is_running():
        return {"message": "Already running", "state": _bio_enrich_job.snapshot()}
    background_tasks.add_task(_run_bio_enrichment)
    return {"message": "Bio enrichment started (Wikipedia)"}

@app.get("/api/enrich/bios/status")
def bio_enrichment_status():
    return _bio_enrich_job.snapshot()

async def _run_bio_enrichment():
    _bio_enrich_job.mark_running(
        processed=0,
        total=0,
        current="",
        fetched=0,
        not_found=0,
        error=None,
        finished_at=None,
    )

    try:
        import enrichment

        def cb(processed, total, artist_name):
            _bio_enrich_job.update(
                processed=processed,
                total=total,
                current=artist_name[:40] if artist_name else "",
            )

        result = await enrichment.batch_enrich_bios(limit=500, progress_callback=cb)
        _bio_enrich_job.update(
            fetched=result.get("fetched", 0),
            not_found=result.get("not_found", 0),
        )
    except Exception as e:
        _bio_enrich_job.set("error", str(e))
    finally:
        _bio_enrich_job.finish()

@app.post("/api/artists/{artist_id}/enrich-bio")
async def enrich_artist_bio(artist_id: int):
    try:
        import enrichment
        result = await enrichment.enrich_artist_bio(artist_id)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))

# ─── Play History API ─────────────────────────────────────────────────────────
@app.get("/api/tracks")
def list_tracks(
    limit: int = Query(100, ge=1, le=MAX_TRACKS_LIMIT),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=MAX_SEARCH_QUERY_LENGTH),
    sort: str = "title",
    favorites_only: bool = False,
):
    """Get list of tracks with pagination, joins, and lightweight filtering."""
    started_at = time.perf_counter()
    db = get_db()
    try:
        result = fetch_tracks(
            db,
            limit=limit,
            offset=offset,
            search=search,
            sort=sort,
            favorites_only=favorites_only,
        )
        _log_baseline_metric(
            "list_tracks",
            started_at,
            limit=limit,
            offset=offset,
            sort=sort,
            favorites_only=int(bool(favorites_only)),
            search_len=len((search or "").strip()),
            result_count=len(result.get("tracks", [])),
            total=result.get("total", 0),
        )
        return result
    finally:
        db.close()

@app.get("/api/tracks/{track_id}")
def get_track(track_id: int):
    """Get single track by ID with artist and album info"""
    db = get_db()
    track = db.execute(
        """SELECT t.*, ar.name as artist_name, al.title as album_title, al.id as album_id, al.cover_path
           FROM tracks t
           LEFT JOIN artists ar ON t.artist_id = ar.id
           LEFT JOIN albums al ON t.album_id = al.id
           WHERE t.id=?""",
        (track_id,)
    ).fetchone()
    db.close()
    if not track:
        raise HTTPException(404, "Track not found")
    return dict(track)

@app.get("/api/albums/{album_id}/tracks")
def get_album_tracks(album_id: int):
    """Get all tracks for an album"""
    db = get_db()
    album = db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    tracks = db.execute(
        "SELECT * FROM tracks WHERE album_id=? ORDER BY track_number", 
        (album_id,)
    ).fetchall()
    db.close()
    
    if not album:
        raise HTTPException(404, "Album not found")
    
    return {
        "album": dict(album),
        "tracks": [dict(t) for t in tracks]
    }
@app.post("/api/tracks/{track_id}/played")
def record_play(track_id: int, body: dict = None):
    duration_played = (body or {}).get("duration_played")
    db = get_db()
    try:
        return record_track_play(db, track_id, duration_played)
    finally:
        db.close()

@app.get("/api/history")
def get_history(limit: int = 50):
    db = get_db()
    try:
        return fetch_history(db, limit)
    finally:
        db.close()

@app.get("/api/most-played")
def get_most_played(limit: int = 50):
    db = get_db()
    try:
        return fetch_most_played(db, limit)
    finally:
        db.close()

@app.get("/api/stats/listening")
def listening_stats():
    db = get_db()
    try:
        return fetch_listening_stats(db)
    finally:
        db.close()

# ─── Lyrics API ───────────────────────────────────────────────────────────────

@app.get("/api/tracks/{track_id}/lyrics")
async def get_lyrics(track_id: int):
    """Get lyrics from multiple sources (LRCLib, then fallback to Genius)."""
    db = get_db()
    track = db.execute(
        """SELECT t.title, ar.name as artist_name
           FROM tracks t JOIN artists ar ON t.artist_id=ar.id
           WHERE t.id=?""",
        (track_id,)
    ).fetchone()
    db.close()
    if not track:
        raise HTTPException(404, "Track not found")

    title = track["title"]
    artist = track["artist_name"]

    # Try LRCLib first (most reliable for synced lyrics)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://lrclib.net/api/get",
                params={"track_name": title, "artist_name": artist},
                headers={"User-Agent": "MusicIQ/1.0"}
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("syncedLyrics") or data.get("plainLyrics"):
                    return {
                        "synced": data.get("syncedLyrics"),
                        "plain": data.get("plainLyrics"),
                        "duration": data.get("duration"),
                        "source": "LRCLib"
                    }
    except Exception:
        pass

    # Fallback: Try to fetch from lyrics.ovh (plain lyrics only)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Clean artist name for API
            search_artist = artist.replace(" ", "").lower()
            search_title = title.replace(" ", "").lower()
            resp = await client.get(
                f"https://api.lyrics.ovh/v1/{search_artist}/{search_title}"
            )
            if resp.status_code == 200:
                data = resp.json()
                lyrics = data.get("lyrics")
                if lyrics:
                    return {
                        "synced": None,
                        "plain": lyrics,
                        "duration": None,
                        "source": "lyrics.ovh"
                    }
    except Exception:
        pass

    return {"synced": None, "plain": None, "source": None}

# ─── Config ───────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    db = get_db()
    try:
        return build_config_payload(db, music_dir=MUSIC_DIR, port=PORT, data_dir=DATA_DIR)
    finally:
        db.close()

@app.post("/api/config/libraries")
def add_library(body: dict):
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(400, "Missing path")
    
    db = get_db()
    try:
        added = add_library_path(db, path)
    finally:
        db.close()
    payload = get_config()
    payload.update({"added": added, "path": path})
    return payload

@app.delete("/api/config/libraries/{lib_id}")
def remove_library(lib_id: int):
    db = get_db()
    try:
        remove_library_path(db, lib_id)
    finally:
        db.close()
    return get_config()

@app.post("/api/config")
def set_config(body: dict):
    global MUSIC_DIR
    if "music_dir" in body:
        MUSIC_DIR = body["music_dir"]
        # Also sync to libraries table for legacy compatibility
        db = get_db()
        try:
            sync_music_dir_library(db, MUSIC_DIR)
        finally:
            db.close()
    return get_config()

# ─── Playlists API ───────────────────────────────────────────────────────────────

@app.get("/api/playlists")
def get_playlists():
    """Get all playlists with track counts."""
    db = get_db()
    try:
        return fetch_playlists(db)
    finally:
        db.close()

@app.post("/api/playlists")
def create_playlist(body: dict):
    """Create a new playlist."""
    name = body.get("name", "New Playlist")
    description = body.get("description", "")
    db = get_db()
    try:
        return create_playlist_record(db, name, description)
    finally:
        db.close()

@app.get("/api/playlists/{playlist_id}")
def get_playlist(playlist_id: int):
    """Get playlist details with tracks."""
    db = get_db()
    try:
        return fetch_playlist_detail(db, playlist_id)
    finally:
        db.close()

@app.post("/api/playlists/{playlist_id}/tracks")
def add_track_to_playlist(playlist_id: int, body: dict):
    """Add a track to playlist."""
    track_id = body.get("track_id")
    if not track_id:
        raise HTTPException(400, "track_id required")

    db = get_db()
    try:
        return add_track_to_playlist_record(db, playlist_id, track_id)
    finally:
        db.close()

@app.delete("/api/playlists/{playlist_id}/tracks/{track_id}")
def remove_track_from_playlist(playlist_id: int, track_id: int):
    """Remove a track from playlist."""
    db = get_db()
    try:
        return remove_track_from_playlist_record(db, playlist_id, track_id)
    finally:
        db.close()

@app.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: int):
    """Delete a playlist."""
    db = get_db()
    try:
        return delete_playlist_record(db, playlist_id)
    finally:
        db.close()

@app.put("/api/playlists/{playlist_id}")
def update_playlist(playlist_id: int, body: dict):
    """Update playlist name or description."""
    name = body.get("name")
    description = body.get("description")
    db = get_db()
    try:
        return update_playlist_record(db, playlist_id, name=name, description=description)
    finally:
        db.close()

# ─── DSP (Digital Signal Processing) API ───────────────────────────────────────

def _serialize_dsp_json(value, fallback: str = "[]") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    return json.dumps(value)


DSP_CATEGORIES = {"custom", "taste", "genre", "reference", "correction"}


def _dsp_text(value, field: str, max_length: int, *, required: bool = False):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise DspValidationError(f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise DspValidationError(f"{field} must not be empty")
    if len(value) > max_length:
        raise DspValidationError(f"{field} must be at most {max_length} characters")
    return value


def _normalize_dsp_payload(body: dict) -> dict:
    """Validate a DSP create/update body. Invalid input is rejected (422),
    never clamped or dropped, so a saved profile is exactly what was sent."""
    if not isinstance(body, dict):
        raise HTTPException(422, "DSP profile body must be an object")
    try:
        return _validate_dsp_payload(body)
    except DspValidationError as exc:
        raise HTTPException(422, f"Invalid DSP profile: {exc}")


def _validate_dsp_payload(body: dict) -> dict:
    normalized: dict = {}

    if "name" in body:
        normalized["name"] = _dsp_text(body["name"], "name", 120, required=True)
    if "description" in body:
        normalized["description"] = _dsp_text(body["description"], "description", 1000)
    if "volume_normalize" in body:
        if not isinstance(body["volume_normalize"], (bool, int)) or body["volume_normalize"] not in (0, 1):
            raise DspValidationError("volume_normalize must be true or false")
        normalized["volume_normalize"] = int(body["volume_normalize"])
    if "stereo_width" in body:
        normalized["stereo_width"] = validate_dsp_number(body["stereo_width"], "stereo_width", -100, 100)
    if "reverb_room_size" in body:
        normalized["reverb_room_size"] = validate_dsp_number(body["reverb_room_size"], "reverb_room_size", 0, 100)
    if "reverb_wet_dry" in body:
        normalized["reverb_wet_dry"] = validate_dsp_number(body["reverb_wet_dry"], "reverb_wet_dry", 0, 100)
    if "category" in body:
        if not isinstance(body["category"], str) or body["category"] not in DSP_CATEGORIES:
            raise DspValidationError(f"category must be one of {sorted(DSP_CATEGORIES)}")
        normalized["category"] = body["category"]
    if "preset_key" in body:
        normalized["preset_key"] = _dsp_text(body["preset_key"], "preset_key", 64)
    if "preamp_db" in body:
        normalized["preamp_db"] = validate_dsp_number(body["preamp_db"], "preamp_db",
                                                      MIN_PREAMP_DB, MAX_PREAMP_DB)
    if "sort_order" in body:
        normalized["sort_order"] = int(validate_dsp_number(body["sort_order"], "sort_order", -100000, 100000))
    if "tags" in body:
        normalized["tags"] = _serialize_dsp_json(body["tags"])
    if "filter_chain_json" in body:
        normalized["filter_chain_json"] = _serialize_dsp_json(validate_dsp_filters(body["filter_chain_json"]))
    elif "filters" in body:
        normalized["filter_chain_json"] = _serialize_dsp_json(validate_dsp_filters(body["filters"]))

    if "eq_bands" in body:
        eq_bands = body["eq_bands"]
        if isinstance(eq_bands, str):
            try:
                eq_bands = json.loads(eq_bands)
            except json.JSONDecodeError as exc:
                raise DspValidationError("eq_bands must be a JSON list") from exc
        if not isinstance(eq_bands, list) or not all(isinstance(band, dict) for band in eq_bands):
            raise DspValidationError("eq_bands must be a list of band objects")
        normalized["eq_bands"] = _serialize_dsp_json(eq_bands)
        if "filter_chain_json" not in normalized:
            legacy_filters = [{"type": "peaking", "freq": band.get("freq", 1000), "gain": band.get("gain", 0),
                               "q": band.get("q", 1.4), "enabled": band.get("enabled", True)}
                              for band in eq_bands]
            normalized["filter_chain_json"] = json.dumps(validate_dsp_filters(legacy_filters))

    if "filter_chain_json" in normalized:
        validate_dsp_headroom(json.loads(normalized["filter_chain_json"]))

    return normalized


@app.get("/api/dsp/profiles")
def get_dsp_profiles():
    """Get all DSP profiles."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM dsp_profiles ORDER BY is_default DESC, sort_order ASC, name ASC"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.get("/api/dsp/profiles/{profile_id}")
def get_dsp_profile(profile_id: int):
    """Get a specific DSP profile."""
    db = get_db()
    profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (profile_id,)).fetchone()
    db.close()
    if not profile:
        raise HTTPException(404, "DSP profile not found")
    return dict(profile)


@app.get("/api/dsp/profiles/{profile_id}/resolved")
def get_resolved_dsp_profile(
    profile_id: int,
    sample_rate: int | None = Query(None, ge=8000, le=768000,
                                    description="Source sample rate the EQ runs at"),
):
    """The filters actually applied at a sample rate, with combined-response headroom."""
    db = get_db()
    profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (profile_id,)).fetchone()
    db.close()
    if not profile:
        raise HTTPException(404, "DSP profile not found")
    return resolve_dsp_plan(dict(profile), sample_rate)


@app.post("/api/dsp/profiles")
def create_dsp_profile(body: dict):
    """Create a new DSP profile."""
    normalized = _normalize_dsp_payload(body)
    name = normalized.get("name", "Custom DSP")
    description = normalized.get("description", "")
    eq_bands = normalized.get("eq_bands", "[]")
    volume_normalize = normalized.get("volume_normalize", 0)
    stereo_width = normalized.get("stereo_width", 0)
    reverb_room_size = normalized.get("reverb_room_size", 0)
    reverb_wet_dry = normalized.get("reverb_wet_dry", 0)
    category = normalized.get("category", "custom")
    preset_key = normalized.get("preset_key")
    preamp_db = normalized.get("preamp_db", 0)
    filter_chain_json = normalized.get("filter_chain_json", "[]")
    tags = normalized.get("tags", "[]")
    sort_order = normalized.get("sort_order", 999)

    db = get_db()
    db.execute("""
        INSERT INTO dsp_profiles (
            name, description, eq_bands, volume_normalize, stereo_width,
            reverb_room_size, reverb_wet_dry, category, preset_key, is_factory,
            preamp_db, filter_chain_json, tags, sort_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
    """, (
        name,
        description,
        eq_bands,
        volume_normalize,
        stereo_width,
        reverb_room_size,
        reverb_wet_dry,
        category,
        preset_key,
        preamp_db,
        filter_chain_json,
        tags,
        sort_order,
    ))
    db.commit()
    profile_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return {"id": profile_id, "name": name}


@app.put("/api/dsp/profiles/{profile_id}")
def update_dsp_profile(profile_id: int, body: dict):
    """Update a DSP profile."""
    db = get_db()
    profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile:
        db.close()
        raise HTTPException(404, "DSP profile not found")
    profile = dict(profile)
    if profile.get("is_factory"):
        db.close()
        raise HTTPException(400, "Factory presets are read-only. Duplicate to a custom preset first.")

    updates = []
    params = []
    try:
        normalized = _normalize_dsp_payload(body)
    except HTTPException:
        db.close()
        raise

    if "name" in normalized:
        updates.append("name=?")
        params.append(normalized["name"])
    if "description" in normalized:
        updates.append("description=?")
        params.append(normalized["description"])
    if "eq_bands" in normalized:
        updates.append("eq_bands=?")
        params.append(normalized["eq_bands"])
    if "volume_normalize" in normalized:
        updates.append("volume_normalize=?")
        params.append(normalized["volume_normalize"])
    if "stereo_width" in normalized:
        updates.append("stereo_width=?")
        params.append(normalized["stereo_width"])
    if "reverb_room_size" in normalized:
        updates.append("reverb_room_size=?")
        params.append(normalized["reverb_room_size"])
    if "reverb_wet_dry" in normalized:
        updates.append("reverb_wet_dry=?")
        params.append(normalized["reverb_wet_dry"])
    if "category" in normalized:
        updates.append("category=?")
        params.append(normalized["category"])
    if "preset_key" in normalized:
        updates.append("preset_key=?")
        params.append(normalized["preset_key"])
    if "preamp_db" in normalized:
        updates.append("preamp_db=?")
        params.append(normalized["preamp_db"])
    if "filter_chain_json" in normalized:
        updates.append("filter_chain_json=?")
        params.append(normalized["filter_chain_json"])
    if "tags" in normalized:
        updates.append("tags=?")
        params.append(normalized["tags"])
    if "sort_order" in normalized:
        updates.append("sort_order=?")
        params.append(normalized["sort_order"])

    if updates:
        updates.append("updated_at=strftime('%s', 'now')")
        params.append(profile_id)
        db.execute(f"UPDATE dsp_profiles SET {', '.join(updates)} WHERE id=?", params)
        db.commit()
    db.close()
    return {"success": True}


@app.post("/api/dsp/profiles/{profile_id}/clone")
def clone_dsp_profile(profile_id: int, body: dict | None = None):
    """Clone an existing DSP profile into a new editable custom profile."""
    db = get_db()
    profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile:
        db.close()
        raise HTTPException(404, "DSP profile not found")
    profile = dict(profile)

    clone_name = (body or {}).get("name") or f"{profile['name']} Custom"
    clone_description = (body or {}).get("description") or f"Custom copy of {profile['name']}"

    db.execute(
        """
        INSERT INTO dsp_profiles (
            name, description, is_default, eq_bands, volume_normalize, stereo_width,
            reverb_room_size, reverb_wet_dry, category, preset_key, is_factory,
            preamp_db, filter_chain_json, tags, sort_order
        ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, 999)
        """,
        (
            clone_name,
            clone_description,
            profile.get("eq_bands"),
            profile.get("volume_normalize", 0),
            profile.get("stereo_width", 0),
            profile.get("reverb_room_size", 0),
            profile.get("reverb_wet_dry", 0),
            "custom",
            profile.get("preamp_db", 0),
            profile.get("filter_chain_json"),
            profile.get("tags"),
        ),
    )
    db.commit()
    clone_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return {"id": clone_id, "name": clone_name}


AUTOEQ_MAX_TEXT_LENGTH = 100_000


@app.post("/api/dsp/profiles/import/autoeq")
def import_autoeq_profile(body: dict):
    """Save an AutoEq ParametricEQ.txt as a custom correction preset.

    Filters keep the file's order and values (shelf Q mapped exactly onto
    FFmpeg's shelves); the file's preamp is kept and the playback headroom
    guard adds attenuation where it is not enough.
    """
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or len(text) > AUTOEQ_MAX_TEXT_LENGTH:
        raise HTTPException(422, f"Invalid AutoEq profile: text must be at most {AUTOEQ_MAX_TEXT_LENGTH} characters")
    try:
        imported = parse_autoeq_parametric_eq(text)
    except DspValidationError as exc:
        raise HTTPException(422, f"Invalid AutoEq profile: {exc}")
    return create_dsp_profile({
        "name": body.get("name"),
        "description": "Imported from an AutoEq ParametricEQ.txt headphone correction.",
        "category": "correction",
        "tags": ["autoeq", "correction"],
        "preamp_db": imported["preamp_db"],
        "filters": imported["filters"],
    })


@app.get("/api/dsp/profiles/{profile_id}/autoeq")
def export_autoeq_profile(profile_id: int):
    """A profile's EQ as AutoEq ParametricEQ.txt (other effects are not part of the format)."""
    db = get_db()
    profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (profile_id,)).fetchone()
    db.close()
    if not profile:
        raise HTTPException(404, "DSP profile not found")
    profile = dict(profile)
    text = format_autoeq_parametric_eq(profile.get("preamp_db") or 0.0, profile_filters(profile))
    return {"filename": f"{profile['name']} ParametricEQ.txt", "text": text}


@app.get("/api/tracks/{track_id}/replaygain")
def get_track_replaygain(track_id: int, mode: Literal["off", "track", "album"] = Query("album")):
    """The ReplayGain the stream applies to a track in a mode, read from its tags."""
    db = get_db()
    track = db.execute("SELECT file_path FROM tracks WHERE id=?", (track_id,)).fetchone()
    library_roots = _resolve_library_roots(db)
    db.close()
    if not track:
        raise HTTPException(404, "Track not found")
    resolved_path = Path(track["file_path"]).resolve()
    if not any(resolved_path == root or resolved_path.is_relative_to(root) for root in library_roots):
        raise HTTPException(403, "Access denied: path outside configured libraries")
    tags = read_replaygain_tags(filesystem_path(track["file_path"])) if mode != "off" else {}
    return resolve_replaygain(tags, mode)


@app.delete("/api/dsp/profiles/{profile_id}")
def delete_dsp_profile(profile_id: int):
    """Delete a DSP profile (cannot delete default)."""
    db = get_db()
    profile = db.execute("SELECT is_default, is_factory FROM dsp_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile:
        db.close()
        raise HTTPException(404, "DSP profile not found")
    if profile["is_default"]:
        db.close()
        raise HTTPException(400, "Cannot delete default profile")
    if profile["is_factory"]:
        db.close()
        raise HTTPException(400, "Cannot delete factory preset")

    db.execute("DELETE FROM dsp_profiles WHERE id=?", (profile_id,))
    db.commit()
    db.close()
    return {"success": True}


# ─── Local Playback API (WASAPI/ASIO) ───────────────────────────────────────────

@app.get("/api/playback/devices")
def get_audio_devices():
    """Get list of available audio output devices."""
    try:
        import local_playback
        devices = local_playback.get_output_devices()
        return {"devices": devices, "available": local_playback.SOUNDDEVICE_AVAILABLE}
    except Exception as e:
        log.error(f"Failed to get audio devices: {e}")
        return {"devices": [], "available": False, "error": str(e)}


@app.post("/api/playback/play")
def play_local(body: dict):
    """
    Play track on local audio device (WASAPI/ASIO).

    Request body:
    {
        "track_id": 123,
        "device_id": 0,        // Optional, None for default
        "latency": "medium",   // "low", "medium", "high"
        "volume": 1.0          // 0.0 to 1.0
    }
    """
    track_id = body.get("track_id")
    if not track_id:
        raise HTTPException(400, "track_id required")

    device_id = body.get("device_id")
    latency = body.get("latency", "medium")
    volume = body.get("volume", 1.0)
    dsp_profile_id = body.get("dsp_profile_id")
    upsample = body.get("upsample", "none")

    try:
        import local_playback
        result = local_playback.play_local(
            track_id=track_id,
            device_id=device_id,
            latency=latency,
            volume=volume,
            dsp_profile_id=dsp_profile_id,
            upsample=upsample,
        )
        return result
    except Exception as e:
        log.error(f"Local playback error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/playback/stop")
def stop_local_playback():
    """Stop local playback."""
    try:
        import local_playback
        return local_playback.stop_local()
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/playback/pause")
def pause_local_playback():
    """Pause local playback."""
    try:
        import local_playback
        return local_playback.pause_local()
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/playback/resume")
def resume_local_playback():
    """Resume local playback."""
    try:
        import local_playback
        return local_playback.resume_local()
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/playback/volume")
def set_native_playback_volume(body: dict):
    """Optional 0..1 digital attenuation; unity gain preserves the original PCM."""
    if not isinstance(body, dict) or "volume" not in body:
        raise HTTPException(422, "volume is required")
    import local_playback
    result = local_playback.set_local_volume(body["volume"])
    if not result["success"]:
        raise HTTPException(422, result.get("error", "Native audio is unavailable"))
    return result


@app.get("/api/playback/status")
def get_playback_status():
    """Get current local playback status."""
    try:
        import local_playback
        return local_playback.get_playback_status()
    except Exception as e:
        return {"playing": False, "error": str(e)}


# ─── Health Check ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    """System health check endpoint."""
    import shutil
    db_error = None
    db_ok = False
    pool = get_pool_status()

    try:
        db = get_db()
        db.execute("SELECT 1")
        _return_db(db)
        db_ok = True
        pool = get_pool_status()
    except Exception as e:
        log.error(f"Database error: {e}")
        db_error = type(e).__name__

    ffmpeg_ok = shutil.which("ffmpeg") is not None

    # Determine status
    if not db_ok:
        status = "degraded"
    else:
        status = pool["health"]
        if status == "uninitialized":
            status = "healthy"

    return {
        "status": status,
        "database": "ok" if db_ok else f"error: {db_error}",
        "ffmpeg": "available" if ffmpeg_ok else "not_installed",
        "pool": {
            "available": pool["available"],
            "total": pool["total"],
            "recycling": pool["recycling"],
            "recycle_threshold": pool["recycle_threshold"],
            "initialized": pool["initialized"],
        },
        "timestamp": time.time()
    }

@app.get("/api/health/check-ffmpeg")
def check_ffmpeg():
    """Check if FFmpeg is available."""
    import shutil
    ffmpeg_available = shutil.which("ffmpeg") is not None
    return {"ffmpeg": ffmpeg_available, "soxr": ffmpeg_supports_soxr()}

@app.post("/api/health/recycle-pool")
def recycle_db_pool():
    """
    Manually trigger database connection pool recycling.
    Closes all existing connections and creates fresh ones.
    Useful when connections become stale or corrupted.
    """
    recycle_state = start_pool_recycle()

    if recycle_state["reason"] == "uninitialized":
        return {"success": True, "message": "Pool not initialized"}

    if recycle_state["reason"] == "already_running":
        return {"success": False, "message": "Pool recycling already in progress"}

    return {
        "success": True,
        "message": "Pool recycling started",
        "connections_before_recycle": recycle_state["connections_before_recycle"]
    }

# ─── Remote Access (Tailscale VPN) ───────────────────────────────────────────────

@app.get("/api/remote/status")
def get_remote_status():
    """Get remote access status via Tailscale."""
    return remote_access.get_remote_status()


@app.post("/api/remote/check")
def check_remote_availability():
    """Check if Tailscale is available."""
    return {
        "available": remote_access.is_tailscale_available(),
        "status": remote_access.get_remote_status(),
        "info": remote_access.get_connection_info()
    }


@app.get("/api/remote/info")
def get_remote_info():
    """Get detailed connection information and instructions."""
    return remote_access.get_connection_info()

# ─── Security (Authentication) ───────────────────────────────────────────────────

_PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip",
                  "x-forwarded-host", "tailscale-user-login")


def get_client_ip(request: Request) -> str:
    """Use the ASGI client address, not an untrusted X-Forwarded-For header.

    A local reverse proxy (e.g. `tailscale serve`) connects from 127.0.0.1 on
    behalf of remote devices. Such a request must never inherit loopback
    trust, so it is reported as a non-IP "proxied:<hop>" identity: never
    loopback/private, yet still a per-device key for login rate limiting.
    Forwarded headers only ever lower trust; they never grant it.
    """
    peer = request.client.host if request.client else "unknown"
    if not _is_loopback_client_ip(peer):
        return peer
    forwarded = any(request.headers.get(name) for name in _PROXY_HEADERS)
    if not forwarded and _is_loopback_client_ip(request.url.hostname or ""):
        return peer
    hop = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    return "proxied:" + (hop[:64] if hop else "unknown")


def _is_local_client_ip(client_ip: str) -> bool:
    """Return True when the IP belongs to loopback/private ranges."""
    if not client_ip:
        return False

    normalized = client_ip.strip().lower()
    if normalized in {"localhost", "0.0.0.0"}:
        return True

    # Remove IPv6 zone index if present (e.g. fe80::1%eth0)
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]

    try:
        ip_obj = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return ip_obj.is_loopback or ip_obj.is_private


def _is_loopback_client_ip(client_ip: str) -> bool:
    """Allow first-time password setup only from the server itself."""
    try:
        return ipaddress.ip_address(client_ip).is_loopback
    except ValueError:
        return client_ip.strip().lower() == "localhost"


@app.get("/api/security/status")
def get_security_status():
    """Get security configuration status."""
    return security.get_security_status()


@app.post("/api/security/setup-password")
def setup_password(request: Request, body: dict):
    """Set up password authentication."""
    password = body.get("password", "")
    result = security.setup_password(password)
    return result


@app.post("/api/security/generate-api-key")
def generate_api_key(request: Request):
    """Generate a new API key for programmatic access."""
    return _credential_response(request, security.generate_new_api_key()["api_key"])


def _credential_response(request: Request, api_key: str):
    # Deliver the browser's stream ticket in the same response as the key, so
    # the UI never has to fetch one between a tap and audio.play().
    stream_ticket, stream_expires_at = security.issue_browser_stream_ticket()
    response = JSONResponse({"success": True, "api_key": api_key,
                             "stream_ticket": stream_ticket,
                             "stream_ticket_expires_at": stream_expires_at},
                            headers={"Cache-Control": "no-store"})
    # Image elements cannot attach API headers. One cookie, scoped to every
    # read-only image path; auth_middleware accepts it on _IMAGE_COOKIE_PATH
    # only, so it never authorizes mutations or the rest of the API.
    for cookie_path in ("/api/covers", "/api/artists"):
        response.set_cookie("velvet_cover_key", api_key, path=cookie_path,
                            httponly=True, samesite="strict",
                            secure=request.url.scheme == "https")
    return response


@app.post("/api/security/login")
def login(request: Request):
    return _credential_response(request, security.get_security_manager().get_or_create_api_key())


@app.post("/api/security/disable")
def disable_security(request: Request):
    """Disable authentication (use with caution!)."""
    return security.disable_security()


@app.get("/api/security/reset-api-key")
def reset_api_key(request: Request):
    """Reset API key."""
    return _credential_response(request, security.generate_new_api_key()["api_key"])

# ─── CORS Management ─────────────────────────────────────────────────────────────

@app.get("/api/cors/origins")
def get_cors_origins():
    """Get current allowed CORS origins."""
    return {"origins": ALLOWED_ORIGINS, "count": len(ALLOWED_ORIGINS)}

@app.post("/api/cors/add-origin")
def add_cors_origin(origin: str):
    """
    Add a CORS origin dynamically (e.g., your tunnel URL).
    Use this after starting a tunnel to enable cross-origin access.
    Example: https://abc123.lt.me
    """
    # Validate URL format
    if not origin.startswith(("http://", "https://")):
        return {"error": "Origin must start with http:// or https://"}, 400

    if origin not in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.append(origin)
        log.info(f"[CORS] Added origin: {origin}")
        return {"success": True, "origin": origin, "all_origins": ALLOWED_ORIGINS}
    else:
        return {"success": True, "origin": origin, "message": "Already in list"}

@app.post("/api/cors/remove-origin")
def remove_cors_origin(origin: str):
    """Remove a CORS origin."""
    if origin in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.remove(origin)
        log.info(f"[CORS] Removed origin: {origin}")
        return {"success": True, "origin": origin, "all_origins": ALLOWED_ORIGINS}
    else:
        return {"error": "Origin not found"}, 404

@app.post("/api/cors/allow-all")
def allow_all_origins():
    """
    TEMPORARILY allow all origins for testing.
    WARNING: Not recommended for production with sensitive data.
    """
    global ALLOWED_ORIGINS
    ALLOWED_ORIGINS = ["*"]
    log.warning("[CORS] Allow-all mode enabled - security risk!")
    return {"success": True, "warning": "All origins allowed (security risk!)"}


# ─── Authentication Middleware ──────────────────────────────────────────────────

PUBLIC_FRONTEND_JS_MODULES = frozenset({
    "library-views.js",
    "settings-view.js",
    "player-ui.js",
    "library-cards.js",
    "dsp-controls.js",
    "remote-settings.js",
    "vu-meter-engine.js",
})
PUBLIC_FRONTEND_JS_PATHS = frozenset(
    f"/static/js/{name}" for name in PUBLIC_FRONTEND_JS_MODULES
)

_MEDIA_TICKET_ROUTES = (
    (re.compile(r"/api/stream/(\d+)"), "stream"),
    (re.compile(r"/api/covers/(\d+)(?:/thumb)?"), "cover"),
)

# The image-only cookie is sent for every /api/artists/... request, so the
# middleware accepts it only on these exact read paths.
_IMAGE_COOKIE_PATH = re.compile(r"/api/(?:covers/\d+(?:/thumb)?|artists/\d+/image)")


def _media_ticket_target(path: str):
    """Return (scope, resource_id) when a path may be read with a media ticket."""
    for pattern, scope in _MEDIA_TICKET_ROUTES:
        match = pattern.fullmatch(path)
        if match:
            return scope, int(match.group(1))
    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Middleware to check authentication for remote requests."""

    client_ip = get_client_ip(request)
    path = request.url.path
    auth_enabled = security.is_auth_enabled()

    # CORS preflight carries no credentials and must reach CORSMiddleware.
    if request.method == "OPTIONS" and request.headers.get("Access-Control-Request-Method"):
        return await call_next(request)

    # Media elements and DLNA renderers cannot send headers. Their URLs carry
    # a ticket minted by an authenticated client and scoped to exactly this
    # track or cover; it authorizes this read and nothing else.
    ticket = request.query_params.get("ticket")
    media_target = _media_ticket_target(path) if request.method in ("GET", "HEAD") else None
    if ticket and media_target:
        scope, resource_id = media_target
        allowed = security.verify_media_ticket(ticket, scope, resource_id) or (
            scope == "stream" and security.verify_media_ticket(ticket, "stream-any", 0)
        )
        if not allowed:
            return JSONResponse(status_code=401, content={"error": "Media ticket expired or invalid"})
        return await call_next(request)

    # Before protection is enabled, a network guest must not be able to claim
    # ownership by setting the first password. Bootstrap locally on the host.
    if not auth_enabled and path.startswith("/api/security/") and path != "/api/security/status":
        if path != "/api/security/setup-password":
            return JSONResponse(
                status_code=403,
                content={"error": "Set an initial password before managing credentials."},
            )
        if not _is_loopback_client_ip(client_ip):
            return JSONResponse(
                status_code=403,
                content={"error": "Initial password setup must be done on the server itself."},
            )
        # A loopback reverse proxy or DNS rebinding must not turn a public
        # hostname into permission to claim an unconfigured server.
        if (not _is_loopback_client_ip(request.url.hostname or "") or
                (request.headers.get("origin") and
                 request.headers["origin"] != str(request.base_url).rstrip("/"))):
            return JSONResponse(status_code=403, content={"error": "Use the local server URL for initial setup."})

    # When no password has been configured, do not expose the music library,
    # destructive APIs, or local playback control over LAN/Tailscale.
    # Users can initialize security at http://localhost:8765 on the host PC.
    if not auth_enabled and not _is_loopback_client_ip(client_ip):
        setup_visible = request.method == "GET" and (
            path in {
                "/", "/api/security/status", "/api/health",
                "/api/health/check-ffmpeg", "/sw.js",
                "/static/manifest.json", "/static/icon.png",
                "/static/logo-hires.png",
                "/static/index.html",
            } | PUBLIC_FRONTEND_JS_PATHS
        )
        if not setup_visible:
            return JSONResponse(
                status_code=403,
                content={"error": "VELVET requires first-time password setup on the host PC."},
            )

    # Expose only read-only diagnostics and the security *status* without auth.
    # Never exempt the entire /api/security/ or /api/health/ namespace:
    # those namespaces include state-changing and credential-rotation endpoints.
    public_get_paths = {
        "/", "/api/security/status", "/api/health",
        "/api/health/check-ffmpeg", "/docs", "/redoc", "/openapi.json",
        "/sw.js", "/static/index.html", "/static/manifest.json",
        "/static/icon.png", "/static/logo-hires.png",
    }
    should_skip = request.method == "GET" and (
        path in public_get_paths or path in PUBLIC_FRONTEND_JS_PATHS
        or path.startswith("/docs/")
    )
    sensitive_security_route = (
        (path.startswith("/api/security/") and path != "/api/security/status")
        or path.startswith("/api/dlna/")
    )

    # Explicitly opting out of LAN auth does not bypass credential management.
    enforce_all_networks = os.getenv(
        "VELVET_AUTH_ENFORCE_ALL_NETWORKS", "true"
    ).lower() in ("true", "1", "yes")
    local_bypass = (
        not enforce_all_networks and _is_local_client_ip(client_ip)
    )

    if auth_enabled and not should_skip and (sensitive_security_route or not local_bypass):
        # Credentials are accepted only in headers: query strings end up in
        # logs, history and renderer metadata. Media URLs use tickets above.
        password = request.headers.get("X-Password")
        api_key = request.headers.get("X-API-Key")
        if not api_key and not password and request.method == "GET" \
                and _IMAGE_COOKIE_PATH.fullmatch(path):
            api_key = request.cookies.get("velvet_cover_key")
            # A page of <img> tags replays one stale cookie (e.g. after the key
            # was rotated on another device) dozens of times. Those are not
            # guesses: counting them would lock the owner out before they can
            # log in again. A 256-bit key gains nothing from the exemption.
            from_cookie = True
        else:
            from_cookie = False

        auth_result = security.verify_auth(
            password=password,
            api_key=api_key,
            ip=client_ip,
            record_failure=not from_cookie,
        )
        if not auth_result["allowed"]:
            return JSONResponse(
                status_code=401,
                content={"error": auth_result["error"]},
            )

    response = await call_next(request)
    if path.startswith("/api/security/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ─── Global Exception Handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled exceptions."""
    import traceback
    log.error(f"Unhandled exception: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc) if settings.log_level == "DEBUG" else None}
    )


# ─── Request ID Middleware ─────────────────────────────────────────────────────

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Middleware to add request ID tracking for debugging."""
    # Get or generate request ID
    request_id = request.headers.get(settings.request_id_header)
    if not request_id:
        request_id = str(uuid.uuid4())

    # Log request start
    if settings.log_requests:
        logger = get_request_logger(request_id)
        logger.info(f"Request started: {request.method} {request.url.path}")

    # Process request
    response = await call_next(request)

    # Add request ID to response headers
    response.headers[settings.request_id_header] = request_id

    # Log request completion
    if settings.log_requests:
        logger = get_request_logger(request_id)
        logger.info(f"Request completed: {request.method} {request.url.path} -> {response.status_code}")

    return response


# ─── Frontend ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=18)
def _compressed_static_asset(file_path: str, mtime_ns: int, file_size: int) -> bytes:
    """Compress versioned public assets once per file revision, never audio."""
    return gzip.compress(Path(file_path).read_bytes(), compresslevel=5)


def _accepts_gzip(request: Request) -> bool:
    for item in request.headers.get("Accept-Encoding", "").split(","):
        token, *params = item.strip().split(";")
        if token.strip().lower() != "gzip":
            continue
        for param in params:
            if param.strip().startswith("q="):
                try:
                    return float(param.strip()[2:]) > 0
                except ValueError:
                    return False
        return True
    return False


def _serve_static_text(request: Request, file_path: Path, media_type: str,
                       cache_control: str) -> Response:
    headers = {"Cache-Control": cache_control, "Vary": "Accept-Encoding"}
    if _accepts_gzip(request):
        stat = file_path.stat()
        payload = _compressed_static_asset(str(file_path), stat.st_mtime_ns, stat.st_size)
        headers["Content-Encoding"] = "gzip"
        return Response(content=payload, media_type=media_type, headers=headers)
    return FileResponse(file_path, media_type=media_type, headers=headers)


@app.get("/")
def serve_frontend(request: Request):
    path = Path(__file__).parent / "static" / "index.html"
    response = _serve_static_text(request, path, "text/html", "no-cache, no-store, must-revalidate")
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/static/index.html")
def serve_public_shell(request: Request):
    return _serve_static_text(
        request, Path(__file__).parent / "static" / "index.html", "text/html", "no-cache"
    )


@app.get("/static/js/{module_name}")
def serve_frontend_module(module_name: str, request: Request):
    """Allowlisted public JS only; transparent gzip never touches FLAC or artwork."""
    if module_name not in PUBLIC_FRONTEND_JS_MODULES:
        raise HTTPException(404, "Unknown frontend module")
    file_path = Path(__file__).parent / "static" / "js" / module_name
    return _serve_static_text(request, file_path, "text/javascript", "no-cache")


@app.get("/static/manifest.json")
def serve_manifest():
    path = Path(__file__).parent / "static" / "manifest.json"
    return FileResponse(path, media_type="application/manifest+json")

@app.get("/sw.js")
def serve_sw():
    path = Path(__file__).parent / "sw.js"
    return FileResponse(path, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

@app.get("/static/icon.png")
def serve_icon():
    path = Path(__file__).parent / "static" / "icon.png"
    return FileResponse(path, media_type="image/png")

@app.get("/static/logo-hires.png")
def serve_hires_logo():
    path = Path(__file__).parent / "static" / "logo-hires.png"
    return FileResponse(path, media_type="image/png")


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    import logging
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except:
        local_ip = "localhost"

    # ─── Logging Setup ──────────────────────────────────────────────────────────────
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "velvet.log"

    logging.basicConfig(
        level=logging.WARNING,  # Uvicorn uses its own logger for requests
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

    # Start UPnP/DLNA server for Marantz M1 & Cambridge EXN100
    try:
        import upnp_server
        upnp_server.start()
    except Exception as e:
        log.warning(f"UPnP server failed to start: {e}")

    print("\n" + "="*60)
    print("  VELVET Personal Music Server")
    print("="*60)
    print(f"  Browser UI: http://localhost:{PORT}")
    print(f"  Network:    http://{local_ip}:{PORT}  (phones, tablets)")
    print(f"  Music:      {MUSIC_DIR}")
    print(f"  Data:       {DATA_DIR}")
    print("-"*60)
    print(f"  [HI-FI] UPnP/DLNA: visible to Marantz M1 & Cambridge EXN100")
    print(f"      Look for 'VELVET' in HEOS app or StreamMagic app")
    print("="*60)
    print("  Press Ctrl+C to stop\n")

    log.warning(f"MusicIQ server started on port {PORT}")

    uvicorn.run(
        "server:app",
        host=settings.host,
        port=PORT,
        log_level="warning",
        reload=False
    )
