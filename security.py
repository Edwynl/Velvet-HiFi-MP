#!/usr/bin/env python3
"""
VELVET Security Module
Provides authentication and security features for remote access
"""

import hashlib
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
    logging.warning("bcrypt not available. Using SHA-256 fallback. Install with: pip install bcrypt")

log = logging.getLogger("velvet.security")

# Config file
CONFIG_DIR = Path(__file__).parent / "velvet_data"
CONFIG_FILE = CONFIG_DIR / "security.json"

CONFIG_DIR.mkdir(exist_ok=True)


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


class SecurityManager:
    """Manages authentication and security settings"""

    def __init__(self):
        self._config = SecurityConfig()
        self._failed_attempts: dict[str, list[float]] = {}  # IP -> list of timestamps
        self._lock = Lock()
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
            "updated_at": time.time()
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def is_enabled(self) -> bool:
        """Check if security is enabled"""
        return self._config.enabled

    def get_api_key(self) -> str:
        """Get current API key"""
        return self._config.api_key

    def set_password(self, password: str) -> bool:
        """Set password (stored as bcrypt hash with SHA-256 fallback)"""
        if not password or len(password) < 8:
            return False

        with self._lock:
            if BCRYPT_AVAILABLE:
                # Use bcrypt for secure password hashing
                salt = bcrypt.gensalt(rounds=12)
                self._config.password_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
                log.info("Password set using secure bcrypt hashing")
            else:
                # Fallback to SHA-256 (not recommended for production)
                self._config.password_hash = hashlib.sha256(password.encode()).hexdigest()
                log.warning("SECURITY: Using SHA-256 fallback for password hashing. Install bcrypt for security.")
            self._config.enabled = True
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
        else:
            # Fallback to SHA-256 for backward compatibility
            return hashlib.sha256(password.encode()).hexdigest() == self._config.password_hash

    def generate_api_key(self) -> str:
        """Generate a new API key"""
        api_key = secrets.token_urlsafe(32)
        with self._lock:
            self._config.api_key = api_key
            self._save_config()
        return api_key

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
            self._save_config()
        return True

    def reset_api_key(self) -> str:
        """Reset API key"""
        return self.generate_api_key()


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


def get_security_status() -> dict:
    return get_security_manager().get_status()


def setup_password(password: str) -> dict:
    manager = get_security_manager()
    if manager.set_password(password):
        return {"success": True, "message": "Password set successfully"}
    return {"success": False, "message": "Password must be at least 4 characters"}


def generate_new_api_key() -> dict:
    manager = get_security_manager()
    api_key = manager.generate_api_key()
    return {"success": True, "api_key": api_key}


def disable_security() -> dict:
    manager = get_security_manager()
    manager.disable()
    return {"success": True, "message": "Security disabled (use only on trusted networks)"}
