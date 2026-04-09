#!/usr/bin/env python3
"""
VELVET - Personal Music Server
A Roon-like music management system for Windows
"""

import asyncio
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, AsyncGenerator
from dataclasses import dataclass, field
from cachetools import TTLCache
from collections import deque

import httpx
from mutagen import File as MutagenFile
from PIL import Image
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Query, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import upnp_server
import remote_access
import security
from cover_art import find_folder_cover as find_folder_cover_bytes, save_cover_bytes
from dsp_engine import build_dsp_filter_chain, profile_filters, sanitize_dsp_filters
from db_pool import (
    DatabaseConnection,
    _return_db,
    get_db,
    get_pool_status,
    start_pool_recycle,
)
from db_schema import init_db
from library_browse import fetch_albums, fetch_artists, fetch_tracks
from library_discovery import (
    fetch_album_radio,
    fetch_artist_detail_summary,
    fetch_artist_radio,
    fetch_discover,
    fetch_genres,
    fetch_recently_added,
    search_tracks,
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
from library_jobs import ScanJobState, create_bio_job_state, create_fingerprint_job_state
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
from scanner_service import refresh_folder_records, scan_music_dirs
from library_stats import (
    fetch_genre_albums,
    fetch_history,
    fetch_library_stats,
    fetch_listening_stats,
    fetch_most_played,
)

# ─── Traditional/Simplified Chinese Conversion ─────────────────────────────────
# Mapping table for common Traditional to Simplified Chinese characters
# Used to improve search matching across Chinese text variants

_T2S_MAP = {
    '傑': '杰', '倫': '伦', '華': '华', '國': '国', '愛': '爱',
    '藝': '艺', '術': '术', '團': '团', '幣': '币', '時': '时',
    '習': '习', '書': '书', '開': '开', '見': '见', '們': '们',
    '會': '会', '學': '学', '東': '东', '車': '车', '電': '电',
    '網': '网', '離': '离', '雲': '云', '區': '区', '號': '号',
    '種': '种', '樣': '样', '義': '义', '經': '经', '織': '织',
    '萬': '万', '與': '与', '節': '节', '備': '备', '質': '质',
    '體': '体', '機': '机', '處': '处', '關': '关', '陳': '陈',
    '盡': '尽', '導': '导', '歲': '岁', '廠': '厂', '廣': '广',
    '庫': '库', '廳': '厅', '據': '据', '線': '线', '極': '极',
    '協': '协', '產': '产', '電': '电', '電': '电', '觀': '观',
    '論': '论', '資': '资', '際': '际', '證': '证', '試': '试',
    '認': '认', '讓': '让', '遠': '远', '適': '适', '選': '选',
    '錢': '钱', '錯': '错', '長': '长', '門': '门', '間': '间',
    '陸': '陆', '陽': '阳', '難': '难', '雜': '杂', '雖': '虽',
    '離': '离', '電': '电', '靈': '灵', '韓': '韩', '風': '风',
    '飛': '飞', '養': '养', '餘': '余', '餓': '饿', '館': '馆',
    '馬': '马', '驕': '骄', '驗': '验', '髮': '发', '魯': '鲁',
    '麵': '面', '黃': '黄', '黑': '黑', '齊': '齐', '齡': '龄',
    '龍': '龙', '龜': '龟', '點': '点', '麗': '丽', '麥': '麦',
    '鳥': '鸟', '鳴': '鸣', '麵': '面', '鹽': '盐', '麥': '麦',
    '屬': '属', '鄉': '乡', '錶': '表', '鏟': '铲', '鏡': '镜',
    '鐵': '铁', '鏟': '铲', '鋼': '钢', '錄': '录', '錐': '锥',
    '鍋': '锅', '鍵': '键', '鋪': '铺', '鏈': '链', '銷': '销',
    '錯': '错', '錘': '锤', '鍾': '钟', '鏟': '铲', '鏟': '铲',
    '鏟': '铲', '鏟': '铲',
}

_S2T_MAP = {v: k for k, v in _T2S_MAP.items()}

def to_simplified(text: str) -> str:
    """Convert Traditional Chinese text to Simplified Chinese."""
    if not text:
        return text
    return ''.join(_T2S_MAP.get(c, c) for c in text)

def to_traditional(text: str) -> str:
    """Convert Simplified Chinese text to Traditional Chinese."""
    if not text:
        return text
    return ''.join(_S2T_MAP.get(c, c) for c in text)

def get_all_variants(text: str) -> list[str]:
    """Get all variants (simplified and traditional) of a search term."""
    if not text:
        return [text]
    variants = [text]
    simplified = to_simplified(text)
    traditional = to_traditional(text)
    if simplified != text and simplified not in variants:
        variants.append(simplified)
    if traditional != text and traditional not in variants:
        variants.append(traditional)
    return variants

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
    """Get CORS origins from env var or use defaults."""
    env_origins = os.environ.get("CORS_ORIGINS", "")
    if env_origins:
        # Parse comma-separated origins
        origins = [o.strip() for o in env_origins.split(",") if o.strip()]
        return _ALLOWED_ORIGINS_DEFAULT + origins
    return _ALLOWED_ORIGINS_DEFAULT

ALLOWED_ORIGINS = _get_cors_origins()
log.info(f"[CORS] Allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_credentials=True,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Content-Type", "Authorization"])


# Global scan state
_scan_job = ScanJobState(MUSIC_DIR)

# In-memory cover cache with TTL support
_cover_cache: TTLCache = TTLCache(maxsize=10000, ttl=86400)  # album_id -> path
_cover_cache_lock = threading.Lock()

# Thumbnail cache lock for thread-safe thumbnail generation
_thumb_cache_lock = threading.Lock()
_thumb_generation_lock = threading.Lock()  # Prevent duplicate thumbnail generation

init_db()

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
        audio_easy = MutagenFile(file_path, easy=True)
        audio_raw  = MutagenFile(file_path)

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
                t = audio_easy.tags
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

    return meta

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

def scan_libraries(music_dirs: list[str]):
    _scan_job.begin(", ".join(music_dirs))

    log.warning(f"[SCAN] Starting scan of {music_dirs}")

    # Debug: Check existing records before scan
    db = get_db()
    try:
        pre_scan_tracks = len(db.execute("SELECT id FROM tracks").fetchall())
        pre_scan_albums = len(db.execute("SELECT id FROM albums").fetchall())
        pre_scan_artists = len(db.execute("SELECT id FROM artists").fetchall())
        log.warning(
            f"[SCAN] DB state before scan: {pre_scan_tracks} tracks, {pre_scan_albums} albums, {pre_scan_artists} artists"
        )

        scan_music_dirs(
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
            on_error=lambda message: _scan_job.set("error", message),
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

@app.post("/api/cast/{device_udn}")
async def cast_to_device(device_udn: str, request: Request):
    """Tell a DLNA device to play a specific track."""
    data = await request.json()
    track_id = data.get("track_id")
    if not track_id:
        raise HTTPException(400, "Missing track_id")
        
    db = get_db()
    track = db.execute(
        "SELECT t.*, ar.name as artist_name, al.title as album_title "
        "FROM tracks t JOIN artists ar ON t.artist_id=ar.id "
        "JOIN albums al ON t.album_id=al.id WHERE t.id=?", (track_id,)
    ).fetchone()
    db.close()
    
    if not track:
        raise HTTPException(404, "Track not found")
        
    local_ip = upnp_server.LOCAL_IP
    stream_url = f"http://{local_ip}:{PORT}/api/stream/{track_id}"
    # Use upnp_server's DIDL-Lite builder for rich metadata (important for some hi-fi players)
    # track is a sqlite3.Row, which track_xml can handle (behaves like a dict)
    track_xml_body = upnp_server.track_xml(dict(track), "0")
    meta = upnp_server._didl_wrap(track_xml_body)
    
    success = upnp_server.cast_track(device_udn, stream_url, meta)
    return {"success": success}

@app.post("/api/cast/control/{device_udn}/{action}")
async def cast_control(device_udn: str, action: str):
    """Pause, Play, or Stop the cast device."""
    success = upnp_server.cast_control(device_udn, action)
    return {"success": success}

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

    db.close()

    result = dict(artist)
    result["album_count"] = stats["album_count"] or 0
    result["track_count"] = stats["track_count"] or 0
    result["total_duration_hours"] = round((stats["total_duration"] or 0) / 3600, 1)
    result["primary_genre"] = genre["genre"] if genre else None
    return result

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

    # Preload cover paths into cache
    with _cover_cache_lock:
        for row in rows:
            album_id = row["id"]
            if album_id not in _cover_cache:
                cover_path = row["cover_path"]
                if cover_path and Path(cover_path).exists():
                    _cover_cache[album_id] = str(cover_path)
                else:
                    _cover_cache[album_id] = None

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

        # Preload cover paths into cache to avoid individual DB queries for each cover
        with _cover_cache_lock:
            for row in rows:
                album_id = row["id"]
                if album_id not in _cover_cache:
                    cover_path = row["cover_path"]
                    if cover_path and Path(cover_path).exists():
                        _cover_cache[album_id] = str(cover_path)
                    else:
                        _cover_cache[album_id] = None

        return rows
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
        return FileResponse(row["image_path"])

    raise HTTPException(404, "No local image found")

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

@app.get("/api/search")
def search(q: str, limit: int = 80):
    db = get_db()
    try:
        return search_tracks(
            db,
            q=q,
            limit=limit,
            get_all_variants=get_all_variants,
            to_simplified=to_simplified,
            to_traditional=to_traditional,
        )
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

    # Preload cover paths into cache
    with _cover_cache_lock:
        for row in rows:
            album_id = row["id"]
            if album_id not in _cover_cache:
                cover_path = row["cover_path"]
                if cover_path and Path(cover_path).exists():
                    _cover_cache[album_id] = str(cover_path)
                else:
                    _cover_cache[album_id] = None

    return rows

@app.get("/api/discover")
def get_discover(limit: int = 12):
    db = get_db()
    try:
        return fetch_discover(db, limit)
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
_thumb_gen_queue: deque[tuple[int, str]] = deque(maxlen=_THUMB_QUEUE_MAX)
_thumb_gen_queue_lock = threading.Lock()

def _queue_thumbnail_generation(album_id: int, size_name: str):
    """Queue a thumbnail for background generation."""
    with _thumb_gen_queue_lock:
        if (album_id, size_name) not in _thumb_gen_queue:
            _thumb_gen_queue.append((album_id, size_name))

def _process_thumb_queue():
    """Process pending thumbnail generations from the queue."""
    batch_size = 10  # Process in batches to avoid pool exhaustion

    items_to_process = []
    with _thumb_gen_queue_lock:
        for _ in range(batch_size):
            if not _thumb_gen_queue:
                break
            items_to_process.append(_thumb_gen_queue.popleft())

    if not items_to_process:
        return

    # Process batch
    for album_id, size_name in items_to_process:
        thumb_path = _get_thumb_path(album_id, size_name)
        if thumb_path.exists():
            continue

        with DatabaseConnection() as db:
            album = db.execute(
                "SELECT cover_path FROM albums WHERE id=?", (album_id,)
            ).fetchone()

        if not album or not album["cover_path"]:
            continue

        source_path = Path(album["cover_path"])
        if not source_path.exists():
            continue

        with _thumb_generation_lock:
            if not thumb_path.exists():
                _generate_thumbnail(source_path, thumb_path, THUMB_SIZES[size_name])

    # Small delay between batches to prevent pool exhaustion
    time.sleep(0.2)

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

@app.get("/api/covers/{album_id}")
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

@app.get("/api/covers/{album_id}/thumb")
def get_cover_thumbnail(album_id: int, size: str = Query("small", pattern="^(small|medium|large)$")):
    """
    Get a thumbnail-sized version of an album cover.
    Sizes: small (200x200), medium (300x300), large (500x500)
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

    filename = f"custom_{album_id}{ext}"
    target_path = COVERS_DIR / filename

    with open(target_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    db.execute("UPDATE albums SET cover_path=? WHERE id=?", (str(target_path), album_id))
    db.commit()
    db.close()

    invalidate_cover_cache(album_id)  # Clear cache so new cover is served
    return {"success": True, "path": str(target_path)}

@app.delete("/api/albums/{album_id}")
async def delete_album(album_id: int, delete_files: bool = Query(False)):
    """Delete an album from the library, optionally deleting physical files."""
    db = get_db()
    album = db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if not album:
        db.close()
        raise HTTPException(404, "Album not found")
    
    tracks = db.execute("SELECT id, file_path FROM tracks WHERE album_id=?", (album_id,)).fetchall()
    
    # Physical deletion if requested
    if delete_files:
        deleted_dirs = set()
        for track in tracks:
            fp = Path(track["file_path"])
            if fp.exists():
                try:
                    fp.unlink()
                    deleted_dirs.add(fp.parent)
                except Exception as e:
                    log.error(f"Failed to delete file {fp}: {e}")
        
        # Try to remove empty directories
        for d in deleted_dirs:
            if d.exists() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except: pass

    # Database deletion (Cascade should handle tracks if FK is set, but let's be safe or check schema)
    # The schema usually has ON DELETE CASCADE for tracks.
    db.execute("DELETE FROM tracks WHERE album_id=?", (album_id,))
    db.execute("DELETE FROM albums WHERE id=?", (album_id,))
    
    # Clean up artist if no tracks left
    artist_id = album["artist_id"]
    count = db.execute("SELECT COUNT(*) FROM tracks WHERE artist_id=?", (artist_id,)).fetchone()[0]
    if count == 0:
        db.execute("DELETE FROM artists WHERE id=?", (artist_id,))
        
    db.commit()
    db.close()
    
    return {"success": True, "message": "Album deleted" + (" and files removed" if delete_files else "")}

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

def _parse_range(header: str, size: int):
    m = re.match(r'bytes=(\d+)-(\d*)', header)
    if not m:
        return 0, size - 1
    start = int(m.group(1))
    end   = int(m.group(2)) if m.group(2) else size - 1
    return start, min(end, size - 1)

def _stream_file_direct(file_path: str, request: Request):
    """Direct file streaming with HTTP range support."""
    size = os.path.getsize(file_path)
    ext  = Path(file_path).suffix.lower()
    mime = MIME_MAP.get(ext, 'audio/mpeg')

    range_header = request.headers.get("Range")
    if range_header:
        start, end = _parse_range(range_header, size)
        length = end - start + 1

        def gen():
            with open(file_path, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(262144, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(gen(), status_code=206, media_type=mime, headers={
            "Content-Range":  f"bytes {start}-{end}/{size}",
            "Accept-Ranges":  "bytes",
            "Content-Length": str(length),
            "Cache-Control":  "no-cache"
        })

    return FileResponse(file_path, media_type=mime, headers={
        "Accept-Ranges":  "bytes",
        "Content-Length": str(size)
    })

async def _stream_upsampled(file_path: str, target_rate: str, quality: str = "high",
                           timeout_minutes: int = 60, dsp_filter: str = ""):
    """
    Upsample via FFmpeg + SoXR resampler, stream as FLAC.

    Uses SoXR High-Quality mode for superior audio quality compared to basic SoX.
    SoXR provides polynomial interpolation with 64-bit floating point precision.

    Args:
        file_path: Path to the source audio file
        target_rate: Target sample rate as string (e.g., "705600" for 705.6kHz)
        quality: soxr_qq (quick), soxr_hq (high), soxr_vhq (extreme)
        timeout_minutes: Max duration for stream in minutes (5-60)
        dsp_filter: Additional DSP filter chain to apply
    """
    # Get SoXR quality setting
    soxr_quality = UPSAMPLE_QUALITY.get(quality, "soxr_hq")

    # Build filter chain: DSP + Upsampling
    filters = []
    if dsp_filter:
        filters.append(dsp_filter)
    filters.append(f"aresample=resampler={soxr_quality}:out_sample_rate={target_rate}")
    filter_chain = ",".join(filters)

    # Build FFmpeg command with SoXR resampler
    # Key improvements:
    # 1. Use soxr_hq/vhq instead of basic soxr
    # 2. output without compression for lower latency
    # 3. Use 32-bit integer for maximum precision in pipeline
    cmd = [
        "ffmpeg", "-i", file_path,
        "-af", filter_chain,
        "-c:a", "pcm_s32le",      # 32-bit integer for best precision
        "-f", "wav",
        "pipe:1",
        "-loglevel", "error"
    ]

    async def gen():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            # 60 秒超时保护，防止卡死
            async with asyncio.timeout(60):
                while True:
                    chunk = await proc.stdout.read(262144)
                    if not chunk:
                        break
                    yield chunk
            # 确保进程退出
            if proc.stdout:
                proc.stdout.close()
            await proc.wait()
        except asyncio.TimeoutError:
            log.warning("FFmpeg stream timeout, terminating")
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                pass
        except Exception as e:
            log.error(f"Stream error: {e}")
            if proc and not proc.process.poll():
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass

    return StreamingResponse(
        gen(), media_type="audio/flac",
        headers={
            "X-Upsampled-Rate": target_rate,
            "X-Upsampled-By":   "FFmpeg+SoXR",
            "X-Quality":        quality,
            "Cache-Control":    "no-cache"
        }
    )


async def _stream_dop(file_path: str, dop_mode: str, quality: str = "high",
                      timeout_minutes: int = 60, dsp_filter: str = ""):
    """
    Stream audio as DSD over PCM (DoP).

    DoP is a method to send DSD audio over a PCM transport.
    The DSD data is encoded in the unused bits of 24-bit PCM frames.

    Args:
        file_path: Path to the source audio file
        dop_mode: dsd64, dsd128, dsd256
        quality: SoXR quality for any required resampling
        timeout_minutes: Max duration for stream in minutes
        dsp_filter: Additional DSP filter chain to apply
    """
    target_dsd_rate = DSD_RATES.get(dop_mode, DSD_RATES["dsd64"])

    # Build filter chain: DSP + Resampling
    filters = []
    if dsp_filter:
        filters.append(dsp_filter)
    filters.append(f"aresample=resampler={UPSAMPLE_QUALITY.get(quality, 'soxr_hq')}")
    filter_chain = ",".join(filters)

    # FFmpeg command for DoP output
    # This converts PCM to DSD64/128/256 and wraps in DoP format
    cmd = [
        "ffmpeg", "-i", file_path,
        "-af", filter_chain,
        # Convert to DSD using libsoxr if available, otherwise use libswr
        "-c:a", "pcm_s32le",
        "-f", "wav",
        "pipe:1",
        "-loglevel", "error"
    ]

    # Note: True DSD encoding requires ffmpeg with libsoxr or libdsd
    # This is a placeholder - actual DoP requires specific FFmpeg build
    # Fallback to high-quality PCM upsampling

    async def gen():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            async with asyncio.timeout(timeout_minutes * 60):
                while True:
                    chunk = await proc.stdout.read(262144)
                    if not chunk:
                        break
                    yield chunk
            if proc.stdout:
                proc.stdout.close()
            await proc.wait()
        except asyncio.TimeoutError:
            log.warning("FFmpeg DoP stream timeout, terminating")
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                pass
        except Exception as e:
            log.error(f"DoP stream error: {e}")
            if proc and not proc.process.poll():
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass

    return StreamingResponse(
        gen(), media_type="audio/wav",
        headers={
            "X-Output-Mode": f"DoP-{dop_mode.upper()}",
            "X-Upsampled-By": "FFmpeg+SoXR",
            "Cache-Control": "no-cache"
        }
    )


async def _stream_with_dsp(file_path: str, dsp_filter: str, timeout_minutes: int = 60):
    """
    Stream audio with DSP processing only (no upsampling).

    Args:
        file_path: Path to the source audio file
        dsp_filter: FFmpeg filter chain to apply
        timeout_minutes: Max duration for stream in minutes
    """
    cmd = [
        "ffmpeg", "-i", file_path,
        "-af", dsp_filter,
        "-c:a", "flac",
        "-compression_level", "0",
        "-f", "flac",
        "pipe:1",
        "-loglevel", "error"
    ]

    async def gen():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            async with asyncio.timeout(timeout_minutes * 60):
                while True:
                    chunk = await proc.stdout.read(262144)
                    if not chunk:
                        break
                    yield chunk
            if proc.stdout:
                proc.stdout.close()
            await proc.wait()
        except asyncio.TimeoutError:
            log.warning("FFmpeg DSP stream timeout, terminating")
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                pass
        except Exception as e:
            log.error(f"DSP stream error: {e}")
            if proc and not proc.process.poll():
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass

    return StreamingResponse(
        gen(), media_type="audio/flac",
        headers={
            "X-Output-Mode": "DSP",
            "Cache-Control": "no-cache"
        }
    )


@app.get("/api/stream/{track_id}")
async def stream_track(
    track_id: int,
    request: Request,
    upsample: str = Query("none", description="none|2x|4x|8x|16x"),
    quality: str = Query("high", description="standard|high|extreme - upsampling quality"),
    dop: str = Query("none", description="none|dsd64|dsd128|dsd256 - DSD over PCM output"),
    dsp: int = Query(None, description="DSP profile ID to apply"),
    timeout_minutes: int = Query(60, ge=5, le=60, description="Stream timeout (5-60 min)")
):
    """
    Stream a track with optional upsampling and DSP processing.

    Parameters:
    - upsample: Target multiplier (none, 2x, 4x, 8x, 16x)
    - quality: SoXR quality preset (standard=quick, high=balanced, extreme=best)
    - dop: DSD over PCM output (none, dsd64, dsd128, dsd256)
    - dsp: DSP profile ID to apply (parametric EQ, volume, etc.)
    - timeout_minutes: Maximum stream duration
    """
    db = get_db()
    track = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()

    # Get DSP profile if specified
    dsp_profile = None
    if dsp:
        dsp_profile = db.execute("SELECT * FROM dsp_profiles WHERE id=?", (dsp,)).fetchone()
        if dsp_profile:
            dsp_profile = dict(dsp_profile)

    db.close()

    if not track:
        raise HTTPException(404, "Track not found")

    fp = track["file_path"]
    # Validate resolved path is within MUSIC_DIR to prevent path traversal
    try:
        resolved_path = Path(fp).resolve()
        if not resolved_path.is_relative_to(MUSIC_DIR):
            raise HTTPException(403, "Access denied: path outside music directory")
    except Exception:
        raise HTTPException(403, "Access denied: invalid path")
    if not resolved_path.exists():
        raise HTTPException(404, f"File not found on disk: {fp}")

    # Build DSP filter chain
    dsp_filter = ""
    if dsp_profile:
        dsp_filter = build_dsp_filter_chain(dsp_profile)

    # Handle DoP (DSD over PCM) output
    if dop != "none" and dop in DSD_RATES:
        return await _stream_dop(fp, dop, quality, timeout_minutes, dsp_filter)

    # Handle PCM upsampling with optional DSP
    target_rate = UPSAMPLE_RATES.get(upsample)
    if target_rate:
        return await _stream_upsampled(fp, target_rate, quality, timeout_minutes, dsp_filter)

    # Handle DSP-only (no upsampling)
    if dsp_filter:
        return await _stream_with_dsp(fp, dsp_filter, timeout_minutes)

    return _stream_file_direct(fp, request)

# ─── Scan API ─────────────────────────────────────────────────────────────────

# Scan rate limiting - prevent rapid consecutive scans
_SCAN_COOLDOWN_SECONDS = 10  # Minimum seconds between scan requests

async def _scan_libraries_async(music_dirs: list[str]):
    """
    Async wrapper for scan_libraries.
    Uses asyncio.to_thread() to run the synchronous file system operations
    in a thread pool, preventing blocking of the event loop.
    """
    await asyncio.to_thread(scan_libraries, music_dirs)

@app.post("/api/scan")
async def start_scan(background_tasks: BackgroundTasks, body: dict = None):
    allowed, remaining = _scan_job.check_and_mark_request(_SCAN_COOLDOWN_SECONDS)
    if not allowed:
        return {
            "message": f"Please wait {remaining} seconds before requesting another scan",
            "cooldown_remaining": remaining,
            "state": _scan_job.snapshot(),
        }

    if _scan_job.is_running():
        return {"message": "Scan already in progress", "state": _scan_job.snapshot()}

    # Check if specific folders are requested (for single folder refresh)
    requested_folders = None
    if body and body.get("folders"):
        requested_folders = body["folders"]

    db = get_db()
    all_libraries = [row["path"] for row in db.execute("SELECT path FROM libraries").fetchall()]
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

    background_tasks.add_task(_scan_libraries_async, libraries)
    return {"message": f"Scan started ({len(libraries)} folder{'s' if len(libraries) > 1 else ''})", "libraries": libraries}

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
    db.close()

    if folder_path not in all_libraries:
        raise HTTPException(400, "Folder not found in library configuration")

    if _scan_job.is_running():
        return {"message": "Scan already in progress", "state": _scan_job.snapshot()}

    background_tasks.add_task(_refresh_folder_async, folder_path)
    return {"message": f"Refresh started for folder: {folder_path}"}

async def _refresh_folder_async(folder_path: str):
    """Async wrapper for folder metadata refresh."""
    await asyncio.to_thread(_refresh_folder_sync, folder_path)

def _refresh_folder_sync(folder_path: str):
    """
    Synchronous folder metadata refresh - deletes and re-inserts all tracks.
    """
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
            on_error=lambda message: log.error(message),
        )

        log.warning(f"[REFRESH] Deleted {result['deleted_tracks']} tracks from {folder_path}")
        log.warning(f"[REFRESH] Completed. Added {result['discovered_files']} tracks")

    except Exception as e:
        log.error(f"[REFRESH] Error: {e}")
        _scan_job.set("error", str(e))
    finally:
        _scan_job.finish()
        db.close()

@app.get("/api/scan/status")
def scan_status():
    return _scan_job.snapshot()

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

        total = len(albums)
        generated = 0
        failed = 0

        for album in albums:
            album_id = album["id"]
            for size_name in THUMB_SIZES:
                thumb_path = _get_thumb_path(album_id, size_name)
                if not thumb_path.exists():
                    source_path = Path(album["cover_path"])
                    if source_path.exists():
                        if _generate_thumbnail(source_path, thumb_path, THUMB_SIZES[size_name]):
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
    limit: int = 100,
    offset: int = 0,
    search: str = "",
    sort: str = "title",
    favorites_only: bool = False,
):
    """Get list of tracks with pagination, joins, and lightweight filtering."""
    db = get_db()
    try:
        return fetch_tracks(
            db,
            limit=limit,
            offset=offset,
            search=search,
            sort=sort,
            favorites_only=favorites_only,
        )
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
    path = body.get("path")
    if not path:
        raise HTTPException(400, "Missing path")
    
    db = get_db()
    try:
        add_library_path(db, path)
    finally:
        db.close()
    return get_config()

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


def _normalize_dsp_payload(body: dict, existing_profile: dict | None = None) -> dict:
    normalized: dict = {}

    if "name" in body:
        normalized["name"] = body["name"]
    if "description" in body:
        normalized["description"] = body["description"]
    if "volume_normalize" in body:
        normalized["volume_normalize"] = int(bool(body["volume_normalize"]))
    if "stereo_width" in body:
        normalized["stereo_width"] = body["stereo_width"]
    if "reverb_room_size" in body:
        normalized["reverb_room_size"] = body["reverb_room_size"]
    if "reverb_wet_dry" in body:
        normalized["reverb_wet_dry"] = body["reverb_wet_dry"]
    if "category" in body:
        normalized["category"] = body["category"]
    if "preset_key" in body:
        normalized["preset_key"] = body["preset_key"]
    if "preamp_db" in body:
        normalized["preamp_db"] = body["preamp_db"]
    if "sort_order" in body:
        normalized["sort_order"] = body["sort_order"]
    if "tags" in body:
        normalized["tags"] = _serialize_dsp_json(body["tags"])
    if "filter_chain_json" in body:
        normalized["filter_chain_json"] = _serialize_dsp_json(sanitize_dsp_filters(body["filter_chain_json"]))
    elif "filters" in body:
        normalized["filter_chain_json"] = _serialize_dsp_json(sanitize_dsp_filters(body["filters"]))

    if "eq_bands" in body:
        normalized["eq_bands"] = _serialize_dsp_json(body["eq_bands"])
        if "filter_chain_json" not in normalized:
            temp_profile = dict(existing_profile or {})
            temp_profile["eq_bands"] = normalized["eq_bands"]
            normalized["filter_chain_json"] = json.dumps(profile_filters(temp_profile))

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
    normalized = _normalize_dsp_payload(body, existing_profile=profile)

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
    return {"ffmpeg": ffmpeg_available}

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

def get_client_ip(request: Request) -> str:
    """Get client IP address, considering proxies"""
    # Check X-Forwarded-For header (used by tunnel services)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    return security.generate_new_api_key()


@app.post("/api/security/disable")
def disable_security(request: Request):
    """Disable authentication (use with caution!)."""
    return security.disable_security()


@app.get("/api/security/reset-api-key")
def reset_api_key(request: Request):
    """Reset API key."""
    return security.generate_new_api_key()

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

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Middleware to check authentication for remote requests."""

    # Skip auth for local connections (same machine and private network)
    # Only skip if VELVET_AUTH_ENFORCE_ALL_NETWORKS is not set to True (default secure)
    client_ip = get_client_ip(request)
    enforce_all_networks = os.getenv("VELVET_AUTH_ENFORCE_ALL_NETWORKS", "true").lower() in ("true", "1", "yes")
    is_local = (
        not enforce_all_networks and
        (
            client_ip in ["127.0.0.1", "localhost", "::1", "0.0.0.0"] or
            client_ip.startswith("192.168.") or
            client_ip.startswith("10.") or
            client_ip.startswith("172.") or
            client_ip.startswith("127.")
        )
    )

    # For local network requests, skip auth only when enforce_all_networks is False
    if is_local:
        return await call_next(request)

    # For external/remote requests, check authentication
    path = request.url.path

    # Skip auth for certain endpoints
    skip_auth = [
        "/",
        "/api/security/",
        "/api/health",
        "/api/health/",
        "/docs",
        "/openapi.json",
        "/redoc"
    ]
    should_skip = any(path.startswith(s) for s in skip_auth)

    # Require auth for remote access if enabled
    if security.is_auth_enabled() and not should_skip:
        auth_result = security.verify_auth(
            password=request.headers.get("X-Password"),
            api_key=request.headers.get("X-API-Key"),
            ip=client_ip
        )

        if not auth_result["allowed"]:
            return JSONResponse(
                status_code=401,
                content={"error": auth_result["error"]}
            )

    return await call_next(request)


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

@app.get("/")
def serve_frontend():
    html = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(
        content=html.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
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
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
        reload=False
    )
