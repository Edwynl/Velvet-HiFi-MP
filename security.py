#!/usr/bin/env python3
"""
VELVET Security Module
Provides authentication and security features for remote access
"""

import base64
import hashlib
import hmac
import secrets
import time
import json
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from threading import Lock

# Try to import bcrypt for secure password hashing
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    logging.warning("bcrypt not available. PBKDF2 fallback enabled; install bcrypt for stronger password handling.")

log = logging.getLogger("velvet.security")

# Config file
CONFIG_DIR = Path(__file__).parent / "velvet_data"
CONFIG_FILE = CONFIG_DIR / "security.json"

CONFIG_DIR.mkdir(exist_ok=True)

# Media tickets authorize GET of ONE track or cover without exposing the
# long-lived API key in URLs (HTMLMediaElement and DLNA renderers cannot send
# headers). Lifetime follows track length so long-form music can finish.
MEDIA_SCOPES = frozenset({"stream", "stream-any", "cover"})
MEDIA_TICKET_GRACE_SECONDS = 30 * 60
MEDIA_TICKET_MAX_SECONDS = 12 * 3600
# Browser playback holds one stream-only ticket fetched ahead of time, so a
# tap-to-play never waits on the network before audio.play() (mobile Safari
# drops the user-gesture chain across such awaits).
BROWSER_STREAM_TICKET_SECONDS = 4 * 3600
DLNA_BROWSE_DEFAULT_MINUTES = 60
DLNA_BROWSE_MAX_MINUTES = 8 * 60


@dataclass
class SecurityConfig:
    """Security configuration"""
    password_hash: str = ""
    api_key: str = ""
    enabled: bool = False
    max_failed_attempts: int = 5
    lockout_duration: int = 300  # seconds
    created_at: float = 0
    updated_at: float = 0
    media_secret: str = ""


class SecurityManager:
    """Manages authentication and security settings"""

    def __init__(self):
        self._config = SecurityConfig()
        self._failed_attempts: dict[str, list[float]] = {}  # IP -> list of timestamps
        self._lock = Lock()
        self._dlna_browse_until = 0.0  # in-memory: a restart revokes the grant
        self._load_config()

    def _load_config(self):
        """Load security config from file"""
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                self._config = SecurityConfig(**data)
            except Exception:
                pass

    def _save_config(self):
        """Save security config to file"""
        data = {
            "password_hash": self._config.password_hash,
            "api_key": self._config.api_key,
            "enabled": self._config.enabled,
            "max_failed_attempts": self._config.max_failed_attempts,
            "lockout_duration": self._config.lockout_duration,
            "created_at": self._config.created_at,
            "updated_at": time.time(),
            "media_secret": self._config.media_secret,
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def is_enabled(self) -> bool:
        """Check if security is enabled"""
        return self._config.enabled

    def get_api_key(self) -> str:
        """Get current API key"""
        return self._config.api_key

    def set_password(self, password: str) -> bool:
        """Set password using bcrypt or salted PBKDF2 for long passwords."""
        if not isinstance(password, str) or len(password) < 8:
            return False

        with self._lock:
            if BCRYPT_AVAILABLE and len(password.encode("utf-8")) <= 72:
                # Use bcrypt for secure password hashing
                salt = bcrypt.gensalt(rounds=12)
                self._config.password_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
                log.info("Password set using secure bcrypt hashing")
            else:
                # Secure, salted and iterated fallback when bcrypt is unavailable.
                # Do not store fast, unsalted SHA-256 hashes for new passwords.
                salt = secrets.token_bytes(16)
                iterations = 600_000
                digest = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), salt, iterations
                )
                self._config.password_hash = (
                    f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"
                )
                log.info("Password stored using PBKDF2-HMAC-SHA256")
            self._config.enabled = True
            # A password change revokes previously issued credentials.
            self._config.api_key = ""
            self._rotate_media_secret_locked()
            self._config.created_at = time.time()
            self._save_config()
        return True

    def check_password(self, password: str) -> bool:
        """Verify password"""
        if not self._config.enabled or not self._config.password_hash:
            return True  # No security = allow all

        if BCRYPT_AVAILABLE and self._config.password_hash.startswith("$2"):
            # Use bcrypt for verification
            try:
                return bcrypt.checkpw(password.encode("utf-8"), self._config.password_hash.encode("utf-8"))
            except Exception as e:
                log.error(f"bcrypt verification failed: {e}")
                return False
        if self._config.password_hash.startswith("pbkdf2_sha256$"):
            try:
                _, rounds, salt_hex, digest_hex = self._config.password_hash.split("$", 3)
                expected = bytes.fromhex(digest_hex)
                actual = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"),
                    bytes.fromhex(salt_hex), int(rounds),
                )
                return secrets.compare_digest(actual, expected)
            except (ValueError, TypeError):
                return False
        # Existing SHA-256 records are accepted ONLY for backwards
        # compatibility; new passwords never use unsalted SHA-256.
        if len(self._config.password_hash) == 64:
            candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
            return secrets.compare_digest(candidate, self._config.password_hash)
        return False

    def generate_api_key(self) -> str:
        """Generate a new API key"""
        api_key = secrets.token_urlsafe(32)
        with self._lock:
            self._config.api_key = api_key
            self._rotate_media_secret_locked()
            self._save_config()
        return api_key

    def get_or_create_api_key(self) -> str:
        """Issue the current credential without revoking other logged-in clients."""
        with self._lock:
            if not self._config.api_key:
                self._config.api_key = secrets.token_urlsafe(32)
                self._save_config()
            return self._config.api_key

    def verify_api_key(self, api_key: str) -> bool:
        """Verify API key"""
        if not self._config.enabled:
            return True
        if not api_key or not self._config.api_key:
            return False
        return secrets.compare_digest(api_key, self._config.api_key)

    def check_rate_limit(self, ip: str) -> bool:
        """
        Check if IP is rate limited due to failed attempts.
        Returns True if request is allowed.
        """
        if not self._config.enabled:
            return True

        with self._lock:
            now = time.time()
            # Clean old attempts
            if ip in self._failed_attempts:
                self._failed_attempts[ip] = [
                    t for t in self._failed_attempts[ip]
                    if now - t < self._config.lockout_duration
                ]

            # Check if locked out
            if ip in self._failed_attempts:
                if len(self._failed_attempts[ip]) >= self._config.max_failed_attempts:
                    return False

            return True

    def record_failed_attempt(self, ip: str):
        """Record a failed authentication attempt"""
        with self._lock:
            if ip not in self._failed_attempts:
                self._failed_attempts[ip] = []
            self._failed_attempts[ip].append(time.time())

    def clear_failed_attempts(self, ip: str):
        """Clear failed attempts after successful auth"""
        with self._lock:
            if ip in self._failed_attempts:
                del self._failed_attempts[ip]

    def get_status(self) -> dict:
        """Get security status"""
        return {
            "enabled": self._config.enabled,
            "has_password": bool(self._config.password_hash),
            "has_api_key": bool(self._config.api_key),
            "max_failed_attempts": self._config.max_failed_attempts,
            "lockout_duration": self._config.lockout_duration
        }

    def disable(self) -> bool:
        """Disable security (for local network use only)"""
        with self._lock:
            self._config.enabled = False
            self._rotate_media_secret_locked()
            self._dlna_browse_until = 0.0
            self._save_config()
        return True

    def reset_api_key(self) -> str:
        """Reset API key"""
        return self.generate_api_key()

    # ─── Scoped media tickets ────────────────────────────────────────────────

    def _rotate_media_secret_locked(self):
        """Invalidate every outstanding media ticket. Caller holds the lock."""
        self._config.media_secret = secrets.token_hex(32)
        self._dlna_browse_until = 0.0

    def _media_key(self) -> bytes:
        with self._lock:
            if not self._config.media_secret:
                self._config.media_secret = secrets.token_hex(32)
                self._save_config()
            return bytes.fromhex(self._config.media_secret)

    def _sign_media(self, scope: str, resource_id: int, expires: int) -> str:
        message = f"velvet-media-v1|{scope}|{resource_id}|{expires}".encode("ascii")
        digest = hmac.new(self._media_key(), message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def issue_media_ticket(self, scope: str, resource_id: int, ttl_seconds: int) -> tuple[str, int]:
        """Return (ticket, expires_at) for read-only access to one resource."""
        if scope not in MEDIA_SCOPES:
            raise ValueError("Unsupported media ticket scope")
        ttl = int(min(max(int(ttl_seconds), 60), MEDIA_TICKET_MAX_SECONDS))
        expires = int(time.time()) + ttl
        return f"{expires}.{self._sign_media(scope, int(resource_id), expires)}", expires

    def verify_media_ticket(self, ticket: str, scope: str, resource_id) -> bool:
        if not ticket or scope not in MEDIA_SCOPES:
            return False
        try:
            expires_text, signature = ticket.split(".", 1)
            expires = int(expires_text)
            resource = int(resource_id)
        except (TypeError, ValueError):
            return False
        if expires < time.time():
            return False
        expected = self._sign_media(scope, resource, expires)
        return secrets.compare_digest(signature, expected)

    # ─── DLNA control-point browse window ────────────────────────────────────

    def grant_dlna_browse(self, minutes: int) -> float:
        """Let LAN DLNA controllers browse for a bounded window (set by an authenticated user)."""
        minutes = min(max(int(minutes), 1), DLNA_BROWSE_MAX_MINUTES)
        with self._lock:
            self._dlna_browse_until = time.time() + minutes * 60
            return self._dlna_browse_until

    def revoke_dlna_browse(self):
        with self._lock:
            self._dlna_browse_until = 0.0

    def dlna_browse_expires_at(self) -> float:
        """Expiry timestamp of the active browse grant, or 0 when inactive."""
        until = self._dlna_browse_until
        return until if until > time.time() else 0.0


# Global instance
_security_manager: Optional[SecurityManager] = None
_manager_lock = Lock()


def get_security_manager() -> SecurityManager:
    """Get or create security manager"""
    global _security_manager
    with _manager_lock:
        if _security_manager is None:
            _security_manager = SecurityManager()
        return _security_manager


# Convenience functions
def is_auth_enabled() -> bool:
    return get_security_manager().is_enabled()


def verify_auth(password: str = None, api_key: str = None, ip: str = None) -> dict:
    """
    Verify authentication.
    Returns: {"allowed": bool, "error": str or None}
    """
    manager = get_security_manager()

    # If auth not enabled, allow all
    if not manager.is_enabled():
        return {"allowed": True, "error": None}

    # A fresh browser loads several protected resources before showing login.
    # Missing credentials are a challenge, not a guessed password; counting
    # them would lock out the user before their first login attempt.
    if not password and not api_key:
        return {"allowed": False, "error": "Authentication required"}

    # Check rate limit
    if ip and not manager.check_rate_limit(ip):
        return {"allowed": False, "error": "Too many failed attempts. Please wait."}

    # Check API key first (can be used instead of password)
    if api_key and manager.verify_api_key(api_key):
        if ip:
            manager.clear_failed_attempts(ip)
        return {"allowed": True, "error": None}

    # Check password
    if password and manager.check_password(password):
        if ip:
            manager.clear_failed_attempts(ip)
        return {"allowed": True, "error": None}

    # Record failed attempt
    if ip:
        manager.record_failed_attempt(ip)

    return {"allowed": False, "error": "Invalid password or API key"}


def media_ticket_ttl(duration_seconds=None) -> int:
    """Ticket lifetime: track length plus grace, bounded for long-form music."""
    try:
        duration = max(float(duration_seconds or 0), 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return min(int(duration) + MEDIA_TICKET_GRACE_SECONDS, MEDIA_TICKET_MAX_SECONDS)


def issue_media_ticket(scope: str, resource_id: int, ttl_seconds: int) -> tuple[str, int]:
    return get_security_manager().issue_media_ticket(scope, resource_id, ttl_seconds)


def issue_browser_stream_ticket() -> tuple[str, int]:
    """Stream-only ticket for the web player: any track, audio reads only."""
    return issue_media_ticket("stream-any", 0, BROWSER_STREAM_TICKET_SECONDS)


def verify_media_ticket(ticket: str, scope: str, resource_id) -> bool:
    return get_security_manager().verify_media_ticket(ticket, scope, resource_id)


def get_security_status() -> dict:
    return get_security_manager().get_status()


def setup_password(password: str) -> dict:
    manager = get_security_manager()
    if manager.set_password(password):
        return {"success": True, "message": "Password set successfully"}
    return {"success": False, "message": "Password must be at least 8 characters"}


def generate_new_api_key() -> dict:
    manager = get_security_manager()
    api_key = manager.generate_api_key()
    return {"success": True, "api_key": api_key}


def disable_security() -> dict:
    manager = get_security_manager()
    manager.disable()
    return {"success": True, "message": "Security disabled (use only on trusted networks)"}
