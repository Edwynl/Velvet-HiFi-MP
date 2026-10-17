"""Independent, per-operation administrator verification for shared libraries.

Listening credentials never grant management access. Admin credentials live in
a separate file so older versions can still read the listening security config.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from functools import lru_cache
from pathlib import Path

import security

ITERATIONS = 600_000
LOCKOUT_SECONDS = 300
MAX_ATTEMPTS = 5
_MANAGEMENT_PREFIXES = (
    "/api/albums/", "/api/artists/", "/api/library/", "/api/scan",
    "/api/config", "/api/thumbs/", "/api/enrich/", "/api/cors/",
    "/api/dlna/", "/api/dsp/profiles", "/api/health/recycle-pool",
    "/api/security/",
)


def requires_admin(method: str, path: str) -> bool:
    path = path.rstrip("/")
    if path == "/api/security/reset-api-key":
        return True  # Legacy GET endpoint also rotates a credential.
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return False
    if path == "/api/security/login" or re.fullmatch(r"/api/albums/\d+/favorite", path):
        return False
    return path.startswith(_MANAGEMENT_PREFIXES)


class AdminSecurity:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def is_configured(self) -> bool:
        # An unreadable existing file must stay locked, never become a bypass.
        return self.path.exists()

    def password_matches(self, password: str) -> bool:
        if not isinstance(password, str) or not password or len(password) > 1024:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["version"] != 1 or data["iterations"] != ITERATIONS:
                return False
            salt = bytes.fromhex(data["salt"])
            expected = bytes.fromhex(data["digest"])
            if len(salt) != 32 or len(expected) != 32:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
            return secrets.compare_digest(actual, expected)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def set_password(self, password: str) -> None:
        if not isinstance(password, str) or not 8 <= len(password) <= 1024:
            raise ValueError("Use an administrator password of 8 to 1024 characters")
        salt = secrets.token_bytes(32)
        body = {"version": 1, "iterations": ITERATIONS, "salt": salt.hex(),
                "digest": hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                              salt, ITERATIONS).hex()}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(body, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._attempts.clear()

    def verify(self, password: str | None, client_ip: str) -> tuple[int, str, str] | None:
        if not self.is_configured():
            return 403, "admin_setup_required", "Set a separate administrator password on the server PC first."
        if not password:
            return 403, "admin_password_required", "Enter the administrator password for this operation."
        now = time.monotonic()
        with self._lock:
            # Bounded state; unrelated listening rate limits are never affected.
            self._attempts = {ip: [t for t in values if now - t < LOCKOUT_SECONDS]
                              for ip, values in self._attempts.items()
                              if values and now - values[-1] < LOCKOUT_SECONDS}
            failures = self._attempts.get(client_ip, [])
            if len(failures) >= MAX_ATTEMPTS or len(self._attempts) >= 1024:
                return 429, "admin_locked", "Too many administrator password attempts. Try again in five minutes."
            if not self.password_matches(password):
                self._attempts.setdefault(client_ip, []).append(now)
                return 403, "admin_password_invalid", "Incorrect administrator password."
            self._attempts.pop(client_ip, None)
        return None


@lru_cache(maxsize=8)
def _manager(path: str) -> AdminSecurity:
    return AdminSecurity(Path(path))


def get_admin_security() -> AdminSecurity:
    return _manager(str(Path(security.CONFIG_FILE).resolve().with_name("admin-security.json")))


def check_request(method: str, path: str, password: str | None, client_ip: str):
    if requires_admin(method, path):
        return get_admin_security().verify(password, client_ip)
    return None
