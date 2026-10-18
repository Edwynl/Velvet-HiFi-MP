"""Administrator sessions are separate from listening keys and media tickets."""
import hashlib
import json
import re
import secrets
import threading
import time
from functools import lru_cache
from pathlib import Path

import security

COOKIE = 'velvet_admin_session'
SESSION_SECONDS = 12 * 3600


def requires_admin(method, path):
    path = path.rstrip('/')
    if path in {'/api/security/admin-login', '/api/security/admin-status'}:
        return False
    if path in {'/api/config', '/api/security/reset-api-key'} or path.startswith((
        '/api/library/', '/api/remote/', '/api/cors/', '/api/dlna/')):
        return True
    if method in {'GET', 'HEAD', 'OPTIONS'}:
        return False
    if path in {'/api/security/login', '/api/stream-ticket'}:
        return False
    if re.fullmatch(r'/api/(?:tracks/\d+/played|albums/\d+/favorite|artists/\d+/enrich-bio)', path):
        return False
    if method == 'POST' and (path == '/api/playlists' or re.fullmatch(r'/api/playlists/\d+/tracks', path)):
        return False
    return path.startswith('/api/')


class AdminSecurity:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._sessions = {}
        self._attempts = {}

    def _verifier(self):
        # Preserve an existing separate admin password. Otherwise reuse the
        # current password as admin without rewriting or disabling security.json.
        try:
            if self.path.exists():
                return self.path.read_bytes()
            current = json.loads(Path(security.CONFIG_FILE).read_text(encoding='utf-8'))
            return (current.get('password_hash') or '').encode()
        except (OSError, ValueError):
            return b'locked' if self.path.exists() or Path(security.CONFIG_FILE).exists() else b''

    def is_configured(self):
        return bool(self._verifier())

    def password_matches(self, password):
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            return False
        verifier = self._verifier()
        if self.path.exists():
            try:
                data = json.loads(verifier)
                if data['version'] != 1 or data['iterations'] != 600000:
                    return False
                salt, expected = bytes.fromhex(data['salt']), bytes.fromhex(data['digest'])
                if len(salt) != 32 or len(expected) != 32:
                    return False
                actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
                return secrets.compare_digest(actual, expected)
            except (ValueError, KeyError, TypeError):
                return False
        return security.password_hash_matches(password, verifier.decode())

    def unlock(self, password, client):
        now = time.monotonic()
        with self._lock:
            self._attempts = {ip: [t for t in times if now-t < 300]
                              for ip, times in self._attempts.items() if times and now-times[-1] < 300}
            if len(self._attempts.get(client, [])) >= 5 or len(self._attempts) >= 1024:
                return None, 'Too many attempts. Try again in five minutes.'
            if not self.password_matches(password):
                self._attempts.setdefault(client, []).append(now)
                return None, 'Incorrect administrator password.'
            self._attempts.pop(client, None)
            self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
            if len(self._sessions) >= 1024:
                return None, 'Too many active sessions. Try again later.'
            token = secrets.token_urlsafe(32)
            self._sessions[hashlib.sha256(token.encode()).digest()] = (
                hashlib.sha256(self._verifier()).digest(), now + SESSION_SECONDS)
            return token, None

    def session_valid(self, token):
        if not token or len(token) > 128:
            return False
        with self._lock:
            entry = self._sessions.get(hashlib.sha256(token.encode()).digest())
            return bool(entry and entry[1] > time.monotonic() and secrets.compare_digest(
                entry[0], hashlib.sha256(self._verifier()).digest()))


@lru_cache(maxsize=8)
def _manager(path):
    return AdminSecurity(path)


def get_admin_security():
    return _manager(str(Path(security.CONFIG_FILE).resolve().with_name('admin-security.json')))
