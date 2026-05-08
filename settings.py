"""
VELVET Settings Module

Pydantic-based configuration management for VELVET.
All settings can be configured via environment variables or .env file.
"""

from pathlib import Path
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScanConstants:
    """Constants for library scanning."""

    AUDIO_EXTENSIONS: set = {
        ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus",
        ".wav", ".aiff", ".aif", ".wv", ".ape",
        ".dsf", ".dff",
        ".mka", ".wma", ".alac",
    }

    UPSAMPLE_RATES: dict = {
        "none": None,
        "2x": "88200",
        "4x": "176400",
        "8x": "352800",
        "16x": "705600",
    }

    UPSAMPLE_QUALITY: dict = {
        "standard": "soxr_qq",
        "high": "soxr_hq",
        "extreme": "soxr_vhq",
    }

    DSD_RATES: dict = {
        "dsd64": 2822400,
        "dsd128": 5644800,
        "dsd256": 11289600,
        "dsd512": 22579200,
    }


class VelvetSettings(BaseSettings):
    """Main settings class for VELVET."""

    model_config = SettingsConfigDict(
        env_prefix="VELVET_",
        env_nested_delimiter="__",
        env_file=Path(__file__).with_name(".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    music_dir: str = Field(default=r"C:\Music")
    data_dir: Path = Field(default=Path("velvet_data"))

    port: int = Field(default=8765, ge=1024, le=65535)
    host: str = Field(default="0.0.0.0")
    log_level: str = Field(default="WARNING")

    db_wal_mode: bool = Field(default=True)
    db_synchronous: str = Field(default="NORMAL")
    db_cache_size: int = Field(default=10000)

    cors_origins: list[str] = Field(
        default=["http://localhost:8765", "http://127.0.0.1:8765"]
    )
    cors_credentials: bool = Field(default=True)

    scan_batch_size: int = Field(default=50, ge=10, le=1000)
    default_artist_limit: int = Field(default=500, ge=1, le=10000)
    default_album_limit: int = Field(default=200, ge=1, le=10000)
    default_track_limit: int = Field(default=50, ge=1, le=10000)

    stream_timeout_minutes: int = Field(default=60, ge=5, le=120)
    stream_chunk_size: int = Field(default=65536, ge=8192, le=1048576)

    cover_cache_max_size: int = Field(default=1000, ge=0)
    cover_cache_ttl: int = Field(default=86400, ge=0)

    api_key_length: int = Field(default=32, ge=16, le=128)
    rate_limit_enabled: bool = Field(default=False)
    rate_limit_requests: int = Field(default=100, ge=1)
    rate_limit_window: int = Field(default=60, ge=1)

    upnp_enabled: bool = Field(default=True)
    upnp_friendly_name: str = Field(default="VELVET")

    remote_access_enabled: bool = Field(default=False)
    remote_access_method: str = Field(default="auto")

    lyrics_enabled: bool = Field(default=True)
    discogs_enabled: bool = Field(default=True)
    musicbrainz_enabled: bool = Field(default=True)
    fingerprinting_enabled: bool = Field(default=False)

    worker_threads: int = Field(default=4, ge=1, le=32)
    max_concurrent_streams: int = Field(default=3, ge=1, le=10)

    structured_logging: bool = Field(default=False)
    log_requests: bool = Field(default=False)
    request_id_header: str = Field(default="X-Request-ID")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(exist_ok=True)
        self.covers_dir.mkdir(exist_ok=True)
        self.cache_dir.mkdir(exist_ok=True)
        self.log_dir.mkdir(exist_ok=True)

    def to_dict(self) -> dict:
        return {
            "music_dir": self.music_dir,
            "data_dir": str(self.data_dir),
            "port": self.port,
            "host": self.host,
            "log_level": self.log_level,
            "scan_batch_size": self.scan_batch_size,
            "default_artist_limit": self.default_artist_limit,
            "default_album_limit": self.default_album_limit,
            "default_track_limit": self.default_track_limit,
            "upnp_enabled": self.upnp_enabled,
            "upnp_friendly_name": self.upnp_friendly_name,
            "remote_access_enabled": self.remote_access_enabled,
            "structured_logging": self.structured_logging,
            "lyrics_enabled": self.lyrics_enabled,
            "discogs_enabled": self.discogs_enabled,
            "musicbrainz_enabled": self.musicbrainz_enabled,
        }


@lru_cache()
def get_settings() -> VelvetSettings:
    settings = VelvetSettings()
    settings.ensure_directories()
    return settings


def get_setting(key: str, default=None):
    settings = get_settings()
    return getattr(settings, key, default)
