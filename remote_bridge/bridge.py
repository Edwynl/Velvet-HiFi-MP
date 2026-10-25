#!/usr/bin/env python3
"""Velvet Remote Bridge: local DLNA control and byte-preserving media relay.

No audio files, search index or Velvet credentials are stored on disk.
Requires Python 3.10+ only; tested against Velvet-HiFi-MP main APIs (Oct 2026).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import ctypes
from dataclasses import replace
import http.client
from dataclasses import dataclass
import html
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import _socket
import ssl
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import (HTTPHandler, HTTPSHandler, HTTPRedirectHandler,
                            ProxyHandler, Request, build_opener)
import xml.etree.ElementTree as ET

LOG = logging.getLogger('velvet.bridge')
SSDP_GROUP = ('239.255.255.250', 1900)
HOP_HEADERS = frozenset({'connection', 'keep-alive', 'proxy-authenticate',
                         'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
                         'upgrade', 'host'})
BODY_LIMIT = 25 * 1024 * 1024
MAX_DEVICE_XML = 128 * 1024
MAX_API_RESPONSE = 2 * 1024 * 1024
UPSTREAM_TIMEOUT = 25
SOCKET_CHUNK = 128 * 1024
# Slow or silent clients may hold a thread only this long before a request.
REQUEST_TIMEOUT = 30
# A paused renderer stops reading; give the browser's proxied audio as long.
CONTROL_STREAM_TIMEOUT = 600
# Sliding: every media read or status poll for the current URI renews it,
# so a long pause works while a forgotten ticket still dies.
GRANT_IDLE_TTL = 6 * 3600
MAX_SSDP_LOCATIONS = 32
# Velvet's UI gives /api/zones 8 s: SSDP replies plus description fetches,
# all of them, stay within this.
SCAN_BUDGET = 5.0
SSDP_WINDOW = 2.0
# Connect plus response headers from the parent, for a media request.
MEDIA_OPEN_TIMEOUT = 15
MEDIA_READ_TIMEOUT = 60
# A renderer that stops reading this long is dropped; on resume it opens a
# fresh Range GET (the grant is still valid), so pause is not a held thread.
MEDIA_STALL_TIMEOUT = 300
MAX_MEDIA_STREAMS_PER_IP = 4
MAX_MEDIA_STREAMS = 16
TAILNET_STATUS_TTL = 30
# Bridge-owned queue: the monitor looks at each renderer with a session this
# often; a track counts as finished when STOPPED within END_TOLERANCE seconds
# of its end (or after last being seen PLAYING that close to it).
MONITOR_INTERVAL = 1.0
END_TOLERANCE = 4.0
SNAPSHOT_FRESH = 1.2
MAX_QUEUE = 5000
REPEAT_MODES = frozenset({'none', 'all', 'one'})
MAX_HTML = 4 * 1024 * 1024
QUEUE_SYNC_JS = Path(__file__).with_name('queue_sync.js')
QUEUE_SYNC_TAG = b'<script src="/bridge/queue-sync.js"></script>'
VELVET_PAGES = frozenset({'/', '/index.html', '/static/index.html'})
PROXY_IDENTITY_HEADERS = frozenset({'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto',
                                    'forwarded', 'x-real-ip'})
# Shown by Velvet's front-end with innerHTML: allow no markup characters.
UNSAFE_NAME_RE = re.compile(r'[<>&"\'`\x00-\x1f\x7f]')
SAFE_UDN_RE = re.compile(r'[A-Za-z0-9:._-]{1,128}')
CAST_RE = re.compile(r'^/api/cast/([^/]+)$')
CAST_CONTROL_RE = re.compile(r'^/api/cast/control/([^/]+)/(Play|Pause|Stop)$')
CAST_STATUS_RE = re.compile(r'^/api/cast/status/([^/]+)$')
CAST_RESULT_RE = re.compile(r'^/api/cast/result/([^/]+)/([^/]+)$')
BRIDGE_SESSION_RE = re.compile(r'^/bridge/session/([^/]+)$')
BRIDGE_QUEUE_RE = re.compile(r'^/bridge/queue/([^/]+)$')
MEDIA_RE = re.compile(r'^/media/([A-Za-z0-9_-]{24,120})/(\d+)\.([a-z0-9]{2,5})$')
COVER_RE = re.compile(r'^/media/([A-Za-z0-9_-]{24,120})/cover\.jpg$')
MIME = {'flac': 'audio/flac', 'wav': 'audio/wav', 'aiff': 'audio/aiff',
        'aif': 'audio/aiff', 'mp3': 'audio/mpeg', 'm4a': 'audio/mp4',
        'alac': 'audio/mp4', 'aac': 'audio/aac', 'ogg': 'audio/ogg'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}): never send the parent's X-API-Key through an HTTP proxy
# from the environment or Windows settings; Tailscale is the only path.
OPENER = build_opener(ProxyHandler({}), HTTPHandler(), HTTPSHandler(), NoRedirect())


def keep_awake(active: bool) -> None:
    """Hold off Windows idle sleep while this thread relays audio."""
    if sys.platform != 'win32':
        return
    es_continuous, es_system_required = 0x80000000, 0x00000001
    flags = es_continuous | (es_system_required if active else 0)
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.kernel32.SetThreadExecutionState(flags)


def leaf(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def child_text(element: ET.Element, name: str) -> str:
    for elt in element.iter():
        if leaf(elt.tag) == name:
            return (elt.text or '').strip()
    return ''


def is_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return isinstance(a, ipaddress.IPv4Address) and (
        a in ipaddress.ip_network('10.0.0.0/8') or
        a in ipaddress.ip_network('172.16.0.0/12') or
        a in ipaddress.ip_network('192.168.0.0/16'))


def tailscale_cli(*args: str) -> str | None:
    paths = ['tailscale']
    if sys.platform == 'win32':
        paths.append(r'C:\Program Files\Tailscale\tailscale.exe')
    for executable in paths:
        try:
            return subprocess.run([executable, *args], capture_output=True,
                                  text=True, timeout=4, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def auto_tailscale_ip() -> str | None:
    output = (tailscale_cli('ip', '-4') or '').strip()
    try:
        if ipaddress.ip_address(output) in ipaddress.ip_network('100.64.0.0/10'):
            return output
    except ValueError:
        pass
    return None


def auto_lan_ip() -> str:
    choices: list[str] = []
    # The default route's source address first: Windows also lists Hyper-V,
    # WSL and Docker switches (e.g. 192.168.48.1), on which no EXN100 lives.
    # A UDP connect sends nothing.
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(('192.0.2.1', 9))
        choices.append(probe.getsockname()[0])
    with contextlib.suppress(OSError):
        choices.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    with contextlib.suppress(OSError):
        choices.extend(x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None,
                                                            socket.AF_INET))
    for ip in choices:
        if is_lan(ip) and not ip.startswith('172.'):
            return ip
    for ip in choices:
        if is_lan(ip):
            return ip
    raise ValueError('Cannot detect a home LAN IPv4 address. Set lan_ip in config.json.')


def validate_url(raw: str) -> str:
    if 'REPLACE' in raw or not raw.strip():
        raise ValueError('Edit config.json: replace upstream_url with parents home Tailscale Velvet IP.')
    parsed = urlparse(raw.strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('upstream_url must be http(s)://host:port, without credentials.')
    if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise ValueError('upstream_url must not include a path or query.')
    return raw.strip().rstrip('/')


@dataclass(frozen=True)
class BridgeConfig:
    upstream_url: str
    control_bind: str
    control_port: int
    lan_ip: str
    media_port: int
    renderer_network: ipaddress.IPv4Network
    preferred_renderer: str = 'EXN100'
    # Extra Host names (e.g. a MagicDNS name) the phone may use for :8877.
    allowed_hosts: tuple[str, ...] = ()
    # Tailscale IPs or login names allowed to use :8877. Empty: only devices
    # of the Tailscale user who owns this bridge PC.
    allowed_peers: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path) -> 'BridgeConfig':
        raw = json.loads(path.read_text(encoding='utf-8'))
        url = validate_url(raw.get('upstream_url', ''))
        control = raw.get('control_bind', 'auto')
        if control == 'auto':
            control = auto_tailscale_ip() or '127.0.0.1'
        if control != '127.0.0.1':
            try:
                if ipaddress.ip_address(control) not in ipaddress.ip_network('100.64.0.0/10'):
                    raise ValueError('control_bind must be a Tailscale IP or 127.0.0.1.')
            except ValueError as exc:
                raise ValueError('control_bind must be a Tailscale IP or 127.0.0.1.') from exc
        lan_ip = raw.get('lan_ip', 'auto')
        if lan_ip == 'auto':
            lan_ip = auto_lan_ip()
        if not is_lan(lan_ip):
            raise ValueError('lan_ip must be a private home LAN IPv4 address.')
        network_raw = raw.get('renderer_network', 'auto')
        network = ipaddress.ip_network(f'{lan_ip}/24' if network_raw == 'auto' else network_raw,
                                      strict=False)
        if not isinstance(network, ipaddress.IPv4Network) or not network.is_private or ipaddress.ip_address(lan_ip) not in network:
            raise ValueError('renderer_network must contain the LAN IP and be private.')
        cp = int(raw.get('control_port', 8877))
        mp = int(raw.get('media_port', 8878))
        if not 1024 <= cp <= 65535 or not 1024 <= mp <= 65535 or cp == mp:
            raise ValueError('control_port and media_port must differ and be >=1024.')
        hosts = raw.get('allowed_hosts', [])
        if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
            raise ValueError('allowed_hosts must be a list of host names.')
        peers = raw.get('allowed_peers', [])
        if not isinstance(peers, list) or not all(isinstance(x, str) and x.strip() for x in peers):
            raise ValueError('allowed_peers must be a list of Tailscale IPs or login names.')
        return cls(url, control, cp, lan_ip, mp, network,
                   str(raw.get('preferred_renderer', 'EXN100')),
                   tuple(h.strip().rstrip('.').lower() for h in hosts),
                   tuple(x.strip().lower() for x in peers))

    def host_allowed(self, host_header: str) -> bool:
        """Refuse DNS-rebinding hosts: the parent trusts every bridge request."""
        host = urlparse(f'//{host_header}').hostname or ''
        host = host.rstrip('.').lower()
        if host in {self.control_bind, '127.0.0.1', 'localhost', *self.allowed_hosts}:
            return True
        # MagicDNS names cannot be pointed elsewhere by a web page.
        machine = socket.gethostname().lower()
        return host == machine or (host.startswith(machine + '.') and host.endswith('.ts.net'))


@dataclass(frozen=True)
class Renderer:
    udn: str
    name: str
    ip: str
    control_url: str

    def ui(self) -> dict[str, str]:
        return {'udn': self.udn, 'name': self.name}


@dataclass(frozen=True)
class MediaGrant:
    token: str
    track_id: int
    renderer_ip: str
    expires_at: float
    credential: str
    mime: str
    extension: str
    album_id: int = 0

    def url(self, cfg: BridgeConfig) -> str:
        return f'http://{cfg.lan_ip}:{cfg.media_port}/media/{self.token}/{self.track_id}.{self.extension}'

    def cover_url(self, cfg: BridgeConfig) -> str:
        return f'http://{cfg.lan_ip}:{cfg.media_port}/media/{self.token}/cover.jpg' if self.album_id else ''


class ActiveStream:
    """One open media relay. cancel() ends it from any thread: the send loop
    sees the event within a second, and closing the parent connection wakes
    a connect, header wait or read blocked on a stalled parent at once (a
    cross-thread shutdown()
    wakes neither a blocked send nor a blocked recv on Windows)."""

    def __init__(self):
        self.cancelled = threading.Event()
        self._source: socket.socket | None = None
        self._lock = threading.Lock()

    def attach(self, sock: socket.socket) -> None:
        """Register the parent connection before it connects, so a Stop also
        ends a connect or a header wait on a hung Tailscale link."""
        with self._lock:
            self._source = sock
            cancelled = self.cancelled.is_set()
        if cancelled:
            self.cancel()
            raise ConnectionAbortedError('media grant revoked')

    def cancel(self) -> None:
        with self._lock:
            self.cancelled.set()
            sock, self._source = self._source, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            # The C-level close: socket.close() defers while the response's
            # file object holds the socket. The fd is marked closed, so a
            # later close by the relay thread is a no-op, never a reused fd.
            with contextlib.suppress(OSError):
                _socket.socket.close(sock)


@dataclass(frozen=True)
class QueueSpec:
    ids: tuple[int, ...]
    index: int
    repeat: str


def parse_queue(raw, track_id) -> QueueSpec | None:
    """A queue from the UI ({ids, index, repeat}) whose current entry is
    `track_id`, within limits; None otherwise."""
    if not isinstance(raw, dict):
        return None
    ids, index, repeat = raw.get('ids'), raw.get('index'), raw.get('repeat', 'none')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_QUEUE
            or not all(type(i) is int and 0 < i < 2 ** 31 for i in ids)
            or type(index) is not int or not 0 <= index < len(ids) or ids[index] != track_id
            or repeat not in REPEAT_MODES):
        return None
    return QueueSpec(tuple(ids), index, repeat)


@dataclass
class QueueSession:
    """What a renderer plays and what follows, in memory only (no library
    copy: track ids of the parent's one database)."""
    ids: tuple[int, ...]
    index: int
    repeat: str
    credential: str
    token: str  # the grant of the stream the renderer should be playing
    track_id: int
    advance_seq: int = 0  # bumped by each bridge-made advance (UI follows it)
    auto_pending: tuple[int, int] | None = None  # (track, index) the UI has yet to confirm
    duration: float = 0.0
    was_playing: bool = False
    last_position: float = 0.0
    idle: bool = False  # stopped, or the queue ended: nothing to advance or keep awake


class HTTPFailure(Exception):
    def __init__(self, status: int, reason: str, code: str | None = None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        # Velvet's front-end acts on `code` (e.g. opens its admin dialog).
        self.code = code


class Bridge:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.devices: dict[str, Renderer] = {}
        self.grants: dict[str, MediaGrant] = {}
        self.last_track: dict[str, int] = {}
        self.cast_results: dict[tuple[str, str], dict[str, Any] | None] = {}
        # Per renderer, every cast and control takes an arrival number.
        # generations: that of the newest cast or Stop (older casts skip);
        # intents: the newest Play/Pause, which a cast that arrived before
        # it must honour even if the cast reaches the renderer later.
        self.arrivals: dict[str, int] = {}
        self.generations: dict[str, int] = {}
        self.intents: dict[str, tuple[int, str]] = {}
        # The newest arrival number that has reached the renderer.
        self.executed: dict[str, int] = {}
        # Users' casts/controls in progress, per renderer (the monitor waits).
        self.inflight: dict[str, int] = {}
        self.sessions: dict[str, QueueSession] = {}
        self.advance_counter = 0
        # (monotonic time, state, position, duration, TrackURI) from the monitor.
        self.snapshots: dict[str, tuple[float, str, float, float, str]] = {}
        self.locks: dict[str, threading.RLock] = {}
        self.lock = threading.RLock()
        self.last_scan = 0.0
        self.scan_lock = threading.Lock()
        self._auth_cache: dict[bytes, float] = {}
        # Open media relays by grant token; revoking a grant cancels them.
        self.streams: dict[str, set[ActiveStream]] = {}
        self.peer_streams: dict[str, int] = {}
        self._tailscale_cache: dict[str, tuple[float, dict]] = {}

    def _tailscale_json(self, *args: str) -> dict:
        """`tailscale <args> --json`, cached briefly ({} if unavailable)."""
        key = ' '.join(args)
        with self.lock:
            expires, cached = self._tailscale_cache.get(key, (0.0, {}))
            if expires > time.monotonic():
                return cached
        try:
            data = json.loads(tailscale_cli(*args, '--json') or '{}')
        except ValueError:
            data = {}
        with self.lock:
            self._tailscale_cache[key] = (time.monotonic() + TAILNET_STATUS_TTL, data)
        return data

    def tailnet_status(self) -> dict:
        return self._tailscale_json('status')

    def serve_proxies_here(self) -> bool:
        """Is this PC's Tailscale Serve proxying to the control port, without
        Funnel? Only then is a loopback request's identity header Serve's own
        (Serve drops a client's copy and sets the verified login)."""
        config = self._tailscale_json('serve', 'status')
        if any((config.get('AllowFunnel') or {}).values()):
            return False
        targets = {f'http://127.0.0.1:{self.cfg.control_port}', f'http://localhost:{self.cfg.control_port}'}
        return any(str(handler.get('Proxy', '')).rstrip('/') in targets
                   for site in (config.get('Web') or {}).values()
                   for handler in (site.get('Handlers') or {}).values())

    def peer_allowed(self, ip: str, login: str = '', proxied: bool = False) -> bool:
        """May this control client use the parent through the bridge?

        The parent sees every relayed request as this bridge, a verified
        tailnet peer it serves passwordlessly, so the bridge must not widen
        that to tailnet devices the parent's own ACL would refuse.

        `ip` is the TCP peer. A direct client is identified only by it, via
        the tailnet map: any identity header it sends is its own claim.
        `login` (Tailscale-User-Login) counts only on a loopback connection
        while Serve verifiably proxies here; `proxied` says the request
        carries proxy headers at all.
        """
        try:
            loopback = ipaddress.ip_address(ip).is_loopback
        except ValueError:
            return False
        if loopback:
            if not login and not proxied:
                return True  # a browser on this PC itself
            if not login or not self.serve_proxies_here():
                return False  # forwarded, but not by a verified Serve
        allowed = self.cfg.allowed_peers
        if not loopback and ip.lower() in allowed:
            return True
        status = self.tailnet_status()
        users = status.get('User') or {}
        if not loopback:
            peer = next((p for p in (status.get('Peer') or {}).values()
                         if ip in (p.get('TailscaleIPs') or [])), None)
            if peer is None:
                return False
            login = str((users.get(str(peer.get('UserID'))) or {}).get('LoginName') or '')
        login = login.lower()
        if allowed:
            return bool(login) and login in allowed
        owner = str((users.get(str((status.get('Self') or {}).get('UserID'))) or {}).get('LoginName') or '')
        return bool(login) and login == owner.lower()

    def upstream(self, path: str, *, method='GET', headers: dict | None = None,
                 data: bytes | None = None, timeout=UPSTREAM_TIMEOUT):
        if not path.startswith('/') or path.startswith('//'):
            raise HTTPFailure(400, 'Invalid upstream path')
        # Never accept an absolute URL from a browser or renderer.
        url = self.cfg.upstream_url + path
        req = Request(url, data=data, headers=headers or {}, method=method)
        try:
            return OPENER.open(req, timeout=timeout)
        except HTTPError as exc:
            return exc
        except (URLError, TimeoutError, OSError) as exc:
            LOG.warning('Upstream request failed for %s', path.split('?', 1)[0])
            raise HTTPFailure(502, 'Parent Velvet is unavailable over Tailscale') from exc

    @contextlib.contextmanager
    def open_media(self, path: str, method: str, headers: dict, stream: ActiveStream):
        """The parent's response for a media relay, over a socket `stream` can
        close at any point: connecting, waiting for headers, or reading."""
        parsed = urlparse(self.cfg.upstream_url)
        https = parsed.scheme == 'https'
        host, port = parsed.hostname, parsed.port or (443 if https else 80)
        connection = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(
            host, port, timeout=MEDIA_OPEN_TIMEOUT)
        try:
            try:
                family, kind, proto, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
                sock = socket.socket(family, kind, proto)
                stream.attach(sock)
                sock.settimeout(MEDIA_OPEN_TIMEOUT)
                sock.connect(address)
                if https:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                    stream.attach(sock)
                connection.sock = sock
                connection.request(method, path, headers=headers)
                response = connection.getresponse()
            except OSError as exc:
                if stream.cancelled.is_set():
                    raise ConnectionAbortedError('media grant revoked') from exc
                LOG.warning('Upstream media request failed')
                raise HTTPFailure(502, 'Parent Velvet is unavailable over Tailscale') from exc
            sock.settimeout(MEDIA_READ_TIMEOUT)
            with response:
                yield response
        finally:
            connection.close()

    def upstream_json(self, path: str, credential: str, *, cookie: str = '') -> dict:
        headers = {'X-API-Key': credential} if credential else {}
        if cookie:
            headers['Cookie'] = cookie
        with self.upstream(path, headers=headers, timeout=15) as response:
            if response.status != 200:
                raise HTTPFailure(401 if response.status in (401, 403) else 502,
                                  'Parent Velvet rejected authentication or request')
            if response.headers.get('Content-Length') and int(response.headers['Content-Length']) > MAX_API_RESPONSE:
                raise HTTPFailure(502, 'Parent response too large')
            payload = response.read(MAX_API_RESPONSE + 1)
            if len(payload) > MAX_API_RESPONSE:
                raise HTTPFailure(502, 'Parent response too large')
            try:
                return json.loads(payload)
            except (ValueError, UnicodeDecodeError) as exc:
                raise HTTPFailure(502, 'Invalid response from parent Velvet') from exc

    def authenticate(self, credential: str, cookie: str = '') -> None:
        # The parent decides, as for any proxied request. On main it treats
        # this tailnet peer as a passwordless listener, so its front-end
        # sends no X-API-Key at all; demanding one here hid every renderer.
        digest = hashlib.sha256(f'{credential}\0{cookie}'.encode('utf-8')).digest()
        with self.lock:
            if self._auth_cache.get(digest, 0) > time.monotonic():
                return
        self.upstream_json('/api/stats', credential, cookie=cookie)
        with self.lock:
            self._auth_cache[digest] = time.monotonic() + 8
            if len(self._auth_cache) > 32:
                self._auth_cache = {k: v for k, v in self._auth_cache.items()
                                    if v > time.monotonic()}

    def authorize_control(self, credential: str, cookie: str = '') -> None:
        """Respect Velvet's separate administrator gate on playback mutations."""
        self.authenticate(credential, cookie)
        security_status = self.upstream_json('/api/security/status', credential, cookie=cookie)
        if security_status.get('admin_protected') and not security_status.get('admin_unlocked'):
            # The same answer as the parent's own gate, so the UI asks for the
            # admin password and retries instead of only failing the cast.
            raise HTTPFailure(403, 'Unlock administrator access for this session.',
                              code='admin_password_required')

    def valid_renderer_ip(self, ip: str) -> bool:
        try:
            return ipaddress.ip_address(ip) in self.cfg.renderer_network and ip != self.cfg.lan_ip
        except ValueError:
            return False

    def parse_renderer(self, payload: bytes, location: str, responder_ip: str) -> Renderer | None:
        parsed_url = urlparse(location)
        if parsed_url.scheme != 'http' or parsed_url.hostname != responder_ip or not self.valid_renderer_ip(responder_ip):
            return None
        if parsed_url.username or parsed_url.password or parsed_url.fragment:
            return None
        try:
            if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
                return None
            root = ET.fromstring(payload)
            dev = next((x for x in root.iter() if leaf(x.tag) == 'device'), None)
            if dev is None:
                return None
            name, udn = child_text(dev, 'friendlyName'), child_text(dev, 'UDN')
            # Any LAN device can answer SSDP. Velvet renders the name with
            # innerHTML and puts the UDN in /api/cast/{udn} paths, so a
            # crafted name would run script with the user's admin session.
            name = UNSAFE_NAME_RE.sub('', name).strip()[:80]
            if not name or not SAFE_UDN_RE.fullmatch(udn):
                return None
            # A responder may contain multiple services; require AVTransport.
            service = next((x for x in dev.iter() if leaf(x.tag) == 'service' and
                            child_text(x, 'serviceType') == 'urn:schemas-upnp-org:service:AVTransport:1'), None)
            if service is None:
                return None
            control = child_text(service, 'controlURL')
            if not control:
                return None
            url = urljoin(location, control)
            dest = urlparse(url)
            if dest.scheme != 'http' or dest.hostname != responder_ip or dest.username or dest.password or dest.fragment:
                return None
            return Renderer(udn, name, responder_ip, url)
        except (ET.ParseError, ValueError):
            return None

    def discover(self, *, force=False) -> list[Renderer]:
        with self.lock:
            if not force and time.monotonic() - self.last_scan < 40:
                return list(self.devices.values())
        if not self.scan_lock.acquire(blocking=False):
            with self.lock:
                return list(self.devices.values())
        found: dict[str, Renderer] = {}
        deadline = time.monotonic() + SCAN_BUDGET
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            with s:
                s.settimeout(0.3)
                s.bind((self.cfg.lan_ip, 0))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(self.cfg.lan_ip))
                for target in ('urn:schemas-upnp-org:device:MediaRenderer:1', 'ssdp:all'):
                    msg = ('M-SEARCH * HTTP/1.1\r\n'
                           f'HOST: {SSDP_GROUP[0]}:{SSDP_GROUP[1]}\r\n'
                           'MAN: "ssdp:discover"\r\nMX: 2\r\n'
                           f'ST: {target}\r\n\r\n').encode('ascii')
                    s.sendto(msg, SSDP_GROUP)
                # Collect first, fetch afterwards: ssdp:all draws several replies
                # per LAN device, and fetching each inline (5 s timeout) both
                # repeated work and let the 2 s window run long.
                locations: dict[str, str] = {}
                until = time.monotonic() + SSDP_WINDOW
                while time.monotonic() < until:
                    try:
                        data, addr = s.recvfrom(8192)
                    except socket.timeout:
                        continue
                    if not self.valid_renderer_ip(addr[0]):
                        continue
                    headers = {}
                    for line in data.decode('latin1', errors='replace').split('\r\n')[1:]:
                        if ':' in line:
                            k, v = line.split(':', 1)
                            headers[k.strip().lower()] = v.strip()
                    location = headers.get('location', '')
                    loc = urlparse(location)
                    if loc.scheme != 'http' or loc.hostname != addr[0]:
                        continue
                    if location not in locations and len(locations) < MAX_SSDP_LOCATIONS:
                        locations[location] = addr[0]
            found = self.describe(locations, deadline)
        except OSError as exc:
            LOG.warning('UPnP discovery error: %s', exc)
        finally:
            with self.lock:
                # Keep stale items briefly if scan fails due to network hiccup.
                if found:
                    self.devices = found
                self.last_scan = time.monotonic()
                cached = list(self.devices.values())
            self.scan_lock.release()
        return sorted(cached, key=lambda d: (self.cfg.preferred_renderer.lower() not in d.name.lower(), d.name.lower()))

    def describe(self, locations: dict[str, str], deadline: float) -> dict[str, Renderer]:
        """Fetch device descriptions in parallel; whatever is not in by the
        deadline is skipped, so one dead TV cannot hide the EXN100."""
        def fetch(location: str, responder: str) -> Renderer | None:
            timeout = max(0.2, deadline - time.monotonic())
            try:
                with self.upnp_open(location, timeout=timeout) as response:
                    payload = response.read(MAX_DEVICE_XML + 1)
            except (URLError, HTTPError, TimeoutError, OSError, ValueError, HTTPFailure):
                return None
            return None if len(payload) > MAX_DEVICE_XML else self.parse_renderer(payload, location, responder)

        found: dict[str, Renderer] = {}
        if not locations:
            return found
        # One worker per location (at most MAX_SSDP_LOCATIONS): a queue behind
        # dead devices could push the EXN100 past the deadline.
        pool = concurrent.futures.ThreadPoolExecutor(len(locations), thread_name_prefix='ssdp-describe')
        try:
            futures = [pool.submit(fetch, loc, ip) for loc, ip in locations.items()]
            done, _ = concurrent.futures.wait(futures, timeout=max(0.0, deadline - time.monotonic()))
            for future in done:
                item = future.result()
                if item:
                    found[item.udn] = item
        finally:
            # Stragglers end on their own socket timeout; nobody waits for them.
            pool.shutdown(wait=False, cancel_futures=True)
        return found

    def upnp_open(self, url: str, *, data: bytes | None = None,
                  headers: dict | None = None, timeout=5):
        parsed = urlparse(url)
        if parsed.scheme != 'http' or not parsed.hostname or not self.valid_renderer_ip(parsed.hostname):
            raise HTTPFailure(403, 'Renderer endpoint is outside the home LAN')
        return OPENER.open(Request(url, data=data, headers=headers or {},
                                   method='POST' if data is not None else 'GET'), timeout=timeout)

    def soap(self, device: Renderer, action: str, values: dict[str, str] | None = None) -> bytes | None:
        service = 'urn:schemas-upnp-org:service:AVTransport:1'
        fields = '<InstanceID>0</InstanceID>' + ''.join(
            f'<{k}>{html.escape(str(v), quote=True)}</{k}>' for k, v in (values or {}).items())
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                f'<s:Body><u:{action} xmlns:u="{service}">{fields}</u:{action}></s:Body></s:Envelope>')
        try:
            with self.upnp_open(device.control_url, data=body.encode('utf-8'),
                                headers={'Content-Type': 'text/xml; charset="utf-8"',
                                         'SOAPACTION': f'"{service}#{action}"'}, timeout=8) as response:
                data = response.read(128 * 1024)
                if b'<Fault' in data or b':Fault>' in data or b'<faultcode>' in data:
                    return None
                return data
        except (HTTPError, URLError, TimeoutError, OSError, HTTPFailure) as exc:
            LOG.warning('Renderer %s rejected %s (%s)', device.name, action, type(exc).__name__)
            return None

    def soap_value(self, device: Renderer, action: str, name: str) -> str:
        result = self.soap(device, action)
        if not result:
            return ''
        try:
            return child_text(ET.fromstring(result), name)
        except ET.ParseError:
            return ''

    def transport(self, device: Renderer) -> str:
        return self.soap_value(device, 'GetTransportInfo', 'CurrentTransportState') or 'UNKNOWN'

    def position(self, device: Renderer) -> tuple[float, float, str]:
        result = self.soap(device, 'GetPositionInfo')
        if not result:
            return 0.0, 0.0, ''
        try:
            root = ET.fromstring(result)
            def secs(s: str) -> float:
                parts = s.split(':')
                return (int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])) if len(parts) == 3 else 0.0
            return secs(child_text(root, 'RelTime')), secs(child_text(root, 'TrackDuration')), child_text(root, 'TrackURI')
        except (ValueError, ET.ParseError):
            return 0.0, 0.0, ''

    def ensure_device(self, udn: str) -> Renderer:
        with self.lock:
            device = self.devices.get(udn)
        if not device:
            self.discover(force=True)
            with self.lock:
                device = self.devices.get(udn)
        if not device:
            raise HTTPFailure(404, 'EXN100 is not discovered; check LAN/Wi-Fi and Windows firewall')
        return device

    def lock_for(self, udn: str) -> threading.RLock:
        with self.lock:
            return self.locks.setdefault(udn, threading.RLock())

    def arrive(self, udn: str) -> int:
        """Number a cast or control on arrival, before any upstream call (the
        admin check alone can take a different time for each request)."""
        with self.lock:
            seq = self.arrivals[udn] = self.arrivals.get(udn, 0) + 1
            return seq

    def commit(self, udn: str, seq: int, action: str) -> None:
        """Apply an authorized request at its arrival position: a newer cast,
        Stop or Play/Pause that committed first keeps precedence."""
        with self.lock:
            if action in ('Cast', 'Stop'):
                self.generations[udn] = max(self.generations.get(udn, 0), seq)
            elif seq > self.intents.get(udn, (0, ''))[0]:
                self.intents[udn] = (seq, action)

    def claim_turn(self, udn: str, seq: int) -> bool:
        """Under the renderer lock: may request `seq` still act? False once a
        newer cast or control has reached the renderer, so an older Play,
        Pause or Stop (delayed in its admin check) never undoes it."""
        with self.lock:
            if seq < self.executed.get(udn, 0):
                return False
            self.executed[udn] = seq
            return True

    def paused_after(self, udn: str, seq: int) -> bool:
        """Did the user's newest Play/Pause, sent after `seq`, ask for Pause?"""
        with self.lock:
            intent_seq, action = self.intents.get(udn, (0, ''))
        return intent_seq > seq and action == 'Pause'

    def request_result(self, udn: str, rid: str) -> dict:
        with self.lock:
            key = (udn, rid)
            if key not in self.cast_results:
                raise HTTPFailure(404, 'Unknown cast request')
            result = self.cast_results[key]
        return {'done': False} if result is None else {'done': True, **result}

    def _store_result(self, udn: str, rid: str | None, data: dict):
        if rid:
            with self.lock:
                self.cast_results[(udn, rid)] = data
                # Prevent unbounded status memory; keep only most recent 64.
                while len(self.cast_results) > 64:
                    self.cast_results.pop(next(iter(self.cast_results)))

    def _new_grant(self, track_id: int, device: Renderer, credential: str,
                   ext: str, album_id: int = 0) -> MediaGrant:
        token = secrets.token_urlsafe(32)
        grant = MediaGrant(token, track_id, device.ip, time.time() + GRANT_IDLE_TTL,
                           credential, MIME[ext], ext, album_id)
        with self.lock:
            for k, g in list(self.grants.items()):
                if g.expires_at < time.time():
                    del self.grants[k]
            self.grants[token] = grant
        return grant

    def retire_older_grants(self, device_ip: str, keep_token: str | None = None) -> None:
        """Revoke this renderer's grants (but `keep_token`) and end their open
        relays: a revoked ticket must not keep streaming what it already opened."""
        with self.lock:
            tokens = [key for key, grant in self.grants.items()
                      if grant.renderer_ip == device_ip and key != keep_token]
            for key in tokens:
                del self.grants[key]
                for stream in self.streams.pop(key, ()):
                    stream.cancel()

    @contextlib.contextmanager
    def track_stream(self, token: str, peer_ip: str):
        """Register an open media relay; bounded per renderer and in total so
        stalled clients cannot pile up threads (and keep-awake requests)."""
        with self.lock:
            if (sum(self.peer_streams.values()) >= MAX_MEDIA_STREAMS or
                    self.peer_streams.get(peer_ip, 0) >= MAX_MEDIA_STREAMS_PER_IP):
                raise HTTPFailure(503, 'Too many open media streams')
            if token not in self.grants:  # revoked between check and register
                raise HTTPFailure(403, 'Media ticket invalid, expired or wrong renderer')
            stream = ActiveStream()
            self.streams.setdefault(token, set()).add(stream)
            self.peer_streams[peer_ip] = self.peer_streams.get(peer_ip, 0) + 1
        try:
            yield stream
        finally:
            with self.lock:
                left = self.peer_streams.get(peer_ip, 0) - 1
                if left > 0:
                    self.peer_streams[peer_ip] = left
                else:
                    self.peer_streams.pop(peer_ip, None)
                streams = self.streams.get(token)
                if streams is not None:
                    streams.discard(stream)
                    if not streams:
                        del self.streams[token]

    def grant_for(self, token: str, track_id: int | None, ext: str | None, peer_ip: str) -> MediaGrant:
        """The live grant for this renderer, renewed by the use (None: any track)."""
        with self.lock:
            grant = self.grants.get(token)
            if (not grant or track_id not in (None, grant.track_id) or ext not in (None, grant.extension) or
                    grant.expires_at <= time.time() or peer_ip != grant.renderer_ip):
                raise HTTPFailure(403, 'Media ticket invalid, expired or wrong renderer')
            grant = self.grants[token] = replace(grant, expires_at=time.time() + GRANT_IDLE_TTL)
        return grant

    def render_didl(self, track: dict, grant: MediaGrant) -> str:
        title = html.escape(str(track.get('title') or 'Unknown Track'))
        artist = html.escape(str(track.get('artist_name') or 'Unknown Artist'))
        album = html.escape(str(track.get('album_title') or 'Unknown Album'))
        uri = html.escape(grant.url(self.cfg), quote=True)
        duration = max(0, float(track.get('duration') or 0))
        seconds = int(duration)
        dstr = f'{seconds // 3600:02d}:{(seconds // 60) % 60:02d}:{seconds % 60:02d}'
        # As main's upnp_server: DLNA.ORG_OP=01 advertises byte seeking (the
        # relay passes Range through), and the rate/depth let the renderer
        # show the source format. Main's DLNA profile names, by extension.
        profile = {'flac': 'FLAC', 'mp3': 'MP3', 'm4a': 'M4A', 'alac': 'M4A', 'aac': 'M4A',
                   'ogg': 'OGG', 'wav': 'LPCM', 'aiff': 'LPCM', 'aif': 'LPCM'}[grant.extension]
        res = (f'protocolInfo="http-get:*:{grant.mime}:DLNA.ORG_PN={profile};DLNA.ORG_OP=01;'
               'DLNA.ORG_FLAGS=01700000000000000000000000000000" '
               f'duration="{dstr}"')
        if int(track.get('file_size') or 0) > 0:
            res += f' size="{int(track["file_size"])}"'
        if grant.extension in ('flac', 'wav', 'aiff', 'aif'):
            res += (f' sampleFrequency="{int(track.get("sample_rate") or 44100)}"'
                    f' bitsPerSample="{int(track.get("bit_depth") or 16)}"'
                    f' nrAudioChannels="{int(track.get("channels") or 2)}"')
        cover = grant.cover_url(self.cfg)
        art = f'<upnp:albumArtURI>{html.escape(cover, quote=True)}</upnp:albumArtURI>' if cover else ''
        return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                f'<item id="t{int(track["id"])}" parentID="0" restricted="1">'
                f'<dc:title>{title}</dc:title><dc:creator>{artist}</dc:creator>'
                f'<upnp:artist>{artist}</upnp:artist><upnp:album>{album}</upnp:album>{art}'
                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                f'<res {res}>{uri}</res>'
                '</item></DIDL-Lite>')

    @contextlib.contextmanager
    def in_flight(self, udn: str):
        """A user's cast or control is being handled: the monitor must not
        auto-advance under it (it would supersede the user's choice)."""
        with self.lock:
            self.inflight[udn] = self.inflight.get(udn, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                self.inflight[udn] -= 1

    def cast(self, udn: str, payload: dict, credential: str, *, cookie: str = '') -> dict:
        with self.in_flight(udn):
            generation = self.arrive(udn)
            self.authorize_control(credential, cookie)
            device = self.ensure_device(udn)
            raw_id = payload.get('track_id')
            if type(raw_id) is not int or raw_id < 1:
                raise HTTPFailure(400, 'Invalid track_id')
            rid = payload.get('request_id')
            if rid is not None and (not isinstance(rid, str) or len(rid) not in range(1, 65)):
                raise HTTPFailure(422, 'Invalid request_id')
            if payload.get('dsp_profile_id') not in (None, 0, False):
                raise HTTPFailure(422, 'Remote Bridge v0.1 streams original files only: disable Velvet DSP/EQ')
            if payload.get('replaygain', 'off') != 'off':
                raise HTTPFailure(422, 'Remote Bridge v0.1: disable ReplayGain for original-file output')
            start = payload.get('start_seconds', 0)
            if isinstance(start, bool) or not isinstance(start, (int, float)) or not 0 <= start <= 720 * 60:
                raise HTTPFailure(422, 'Invalid start_seconds')
            recast = payload.get('recast', False)
            if type(recast) is not bool:
                raise HTTPFailure(422, 'Invalid recast')
            # Sent by queue_sync.js with each cast; a malformed one only costs
            # the auto-advance (the cast itself still plays).
            queue = parse_queue(payload.get('bridge_queue'), raw_id)
            self.commit(udn, generation, 'Cast')
            if rid:
                with self.lock:
                    self.cast_results[(udn, rid)] = None
            return self._play(udn, device, raw_id, credential, generation,
                              start=start, recast=recast, rid=rid, queue=queue)

    def _play(self, udn: str, device: Renderer, track_id: int, credential: str, generation: int, *,
              start: float = 0, recast: bool = False, rid: str | None = None,
              queue: QueueSpec | None = None, expect_token: str | None = None) -> dict:
        """Load and play one track: a user's cast, or (with `expect_token`,
        the stream that just ended) the bridge's own advance to the next."""
        grant = None
        set_uri_accepted = False
        try:
            track = self.upstream_json(f'/api/tracks/{track_id}', credential)
            ext = str(track.get('format') or '').lower().lstrip('.')
            if ext not in MIME:
                filename = str(track.get('file_path') or '')
                ext = os.path.splitext(filename)[-1].lower().lstrip('.')
            if ext not in MIME:
                raise HTTPFailure(415, 'This track format is not yet supported by the remote bridge')
            if int(track.get('id', 0)) != track_id:
                raise HTTPFailure(502, 'Parent Velvet returned the wrong track')
            with self.lock_for(udn):
                with self.lock:
                    if self.generations.get(udn) != generation:
                        result = {'success': False, 'superseded': True}
                        self._store_result(udn, rid, result)
                        return result
                    session = self.sessions.get(udn)
                    if expect_token is not None and (session is None or session.token != expect_token):
                        return {'success': False, 'superseded': True}  # the user moved on meanwhile
                    # Still the newest cast: it runs even after a newer
                    # Play/Pause (honouring that Pause below), and every
                    # control older than it is void from now on.
                    self.executed[udn] = max(self.executed.get(udn, 0), generation)
                    if (expect_token is None and queue and not recast and not start and session
                            and session.auto_pending == (track_id, queue.index) and session.token in self.grants):
                        # The UI catching up with an advance the bridge already
                        # made (its own auto-next, or queue_sync.js following
                        # the session): confirm it, never restart the track.
                        session.auto_pending = None
                        session.ids, session.index, session.repeat = queue.ids, queue.index, queue.repeat
                        result = self._play_result('playing', None, absorbed=True)
                        self._store_result(udn, rid, result)
                        return result
                # This ticket exists only in RAM, and is bound to the EXN100 IP.
                grant = self._new_grant(track_id, device, credential, ext, int(track.get('album_id') or 0))
                url = grant.url(self.cfg)
                metadata = self.render_didl(track, grant)
                # Capture pause state BEFORE loading a new URI; SetURI may reset it.
                paused = recast and self.transport(device) == 'PAUSED_PLAYBACK'
                def set_uri() -> bool:
                    # Some renderers reject nonempty DIDL metadata.
                    return any(self.soap(device, 'SetAVTransportURI', {
                        'CurrentURI': url, 'CurrentURIMetaData': meta}) is not None
                        for meta in (metadata, ''))
                set_uri_accepted = set_uri()
                if not set_uri_accepted:
                    # As main's cast_track: a renderer busy with a stream may
                    # take a new URI only once stopped.
                    self.soap(device, 'Stop')
                    time.sleep(0.5)
                    set_uri_accepted = set_uri()
                if not set_uri_accepted:
                    # Main answers 200 with success false here; the UI rolls back.
                    with self.lock:
                        self.grants.pop(grant.token, None)
                    result = {'success': False, 'transport': 'failed',
                              'error': f'{device.name} rejected the stream URL'}
                    self._store_result(udn, rid, result)
                    return result
                # A Pause sent after this cast may already have run (it did not
                # wait for the track lookup): load the stream but do not Play.
                if paused or self.paused_after(udn, generation):
                    transport = 'loaded'
                else:
                    transport = 'playing' if self.soap(device, 'Play', {'Speed': '1'}) is not None else 'play_failed'
                    if transport == 'play_failed':
                        time.sleep(1)  # still opening the stream (main waits 2 s up front)
                        transport = 'playing' if self.soap(device, 'Play', {'Speed': '1'}) is not None else 'play_failed'
                sought = None
                if start > 0 and transport != 'play_failed':
                    seek_time = int(start)
                    sought = self.soap(device, 'Seek', {
                        'Unit': 'REL_TIME',
                        'Target': f'{seek_time // 3600:02d}:{seek_time // 60 % 60:02d}:{seek_time % 60:02d}'}) is not None
                with self.lock:
                    self.last_track[udn] = track_id
                if transport in ('playing', 'loaded'):
                    self.retire_older_grants(device.ip, grant.token)
                    self._start_session(udn, track_id, credential, grant.token,
                                        queue or QueueSpec((track_id,), 0, 'none'), auto=expect_token is not None,
                                        duration=float(track.get('duration') or 0))
                result = self._play_result(transport, sought)
                self._store_result(udn, rid, result)
                return result
        except HTTPFailure as exc:
            if grant is not None and not set_uri_accepted:
                with self.lock:
                    self.grants.pop(grant.token, None)
            self._store_result(udn, rid, {'success': False, 'error': exc.reason})
            raise
        except (TypeError, ValueError, KeyError) as exc:
            self._store_result(udn, rid, {'success': False, 'error': 'Invalid track metadata'})
            raise HTTPFailure(502, 'Invalid track metadata') from exc
        except Exception:
            # Never leave /api/cast/result pending for the UI's 3-minute poll.
            self._store_result(udn, rid, {'success': False, 'error': 'Cast failed'})
            raise

    @staticmethod
    def _play_result(transport: str, sought: bool | None, **extra) -> dict:
        return {'success': transport in ('playing', 'loaded'), 'transport': transport,
                'start_offset': 0, 'position_restored': sought,
                'dsp': {'requested_profile_id': None, 'effective_profile_id': None,
                        'status': 'off', 'reason': 'VELVET DSP off', 'stream': 'direct'},
                'replaygain': {'mode': 'off', 'applied_db': 0}, **extra}

    def _start_session(self, udn: str, track_id: int, credential: str, token: str,
                       queue: QueueSpec, *, auto: bool, duration: float) -> None:
        """The renderer now plays `token`: from here the bridge, not the
        phone, owns what plays next."""
        with self.lock:
            previous = self.sessions.get(udn)
            if auto:
                self.advance_counter += 1
            advance_seq = self.advance_counter if auto else (previous.advance_seq if previous else 0)
            self.sessions[udn] = QueueSession(
                ids=queue.ids, index=queue.index, repeat=queue.repeat, credential=credential,
                token=token, track_id=track_id, advance_seq=advance_seq,
                auto_pending=(track_id, queue.index) if auto else None, duration=duration)

    def update_queue(self, udn: str, payload: dict, credential: str, cookie: str = '') -> dict:
        """The UI's queue changed (added, removed, reordered, repeat mode) while
        the renderer plays: what the bridge advances to must follow it."""
        self.authorize_control(credential, cookie)
        track_id = payload.get('track_id')
        queue = parse_queue(payload, track_id) if type(track_id) is int else None
        if queue is None:
            raise HTTPFailure(422, 'Invalid queue')
        with self.lock:
            session = self.sessions.get(udn)
            if session is None or session.track_id != track_id:
                return {'success': False, 'stale': True}  # describes another track
            session.ids, session.index, session.repeat = queue.ids, queue.index, queue.repeat
        return {'success': True}

    def session_info(self, udn: str, credential: str, cookie: str = '') -> dict:
        self.authenticate(credential, cookie)
        with self.lock:
            session = self.sessions.get(udn)
            if session is None:
                return {'active': False}
            return {'active': True, 'track_id': session.track_id, 'queue_index': session.index,
                    'queue_length': len(session.ids), 'repeat': session.repeat,
                    'advance_seq': session.advance_seq, 'idle': session.idle}

    @staticmethod
    def next_index(session: QueueSession) -> int | None:
        """Velvet's cast-mode playNext(): the following track, wrapping only
        on repeat 'all' (its cast path plays on for 'one' as well)."""
        following = session.index + 1
        if following < len(session.ids):
            return following
        return 0 if session.repeat == 'all' else None

    def monitor_once(self, udn: str) -> None:
        """One look at a renderer with a session; advance once at the end of a
        track, with no phone involved (Safari may be suspended)."""
        with self.lock:
            session = self.sessions.get(udn)
            device = self.devices.get(udn)
        if session is None or device is None:
            return
        state = self.transport(device)
        position, duration, uri = self.position(device)
        with self.lock:
            self.snapshots[udn] = (time.monotonic(), state, position, duration, uri)
        match = MEDIA_RE.fullmatch(urlparse(uri).path) if uri else None
        token = match.group(1) if match else None
        with self.lock:
            if self.sessions.get(udn) is not session:
                return
            if uri and token is None:
                # Another source took the renderer (StreamMagic, radio...).
                del self.sessions[udn]
                return
            if token is not None and token != session.token:
                return  # one of our newer streams is being loaded: not ours to judge
            if state in ('PLAYING', 'TRANSITIONING'):
                session.idle = False
                if state == 'PLAYING':
                    session.was_playing = True
                    session.last_position = max(session.last_position, position)
                    session.duration = duration or session.duration
                return
            if state not in ('STOPPED', 'NO_MEDIA_PRESENT') or not session.was_playing:
                return  # paused (stays awake), an unknown/failed read, or never started
            length = duration or session.duration
            finished = length > 0 and max(position, session.last_position) >= length - END_TOLERANCE
            if not finished:
                session.was_playing, session.idle = False, True  # stopped mid-track elsewhere
                return
            following = self.next_index(session)
            if following is None:
                session.was_playing, session.idle = False, True  # end of the queue
                return
            if self.inflight.get(udn):
                return  # a user's cast or control is on its way; look again next tick
            session.was_playing = False
            expect, next_track = session.token, session.ids[following]
            queue = QueueSpec(session.ids, following, session.repeat)
            credential = session.credential
        generation = self.arrive(udn)
        self.commit(udn, generation, 'Cast')
        try:
            result = self._play(udn, device, next_track, credential, generation,
                                queue=queue, expect_token=expect)
        except HTTPFailure as exc:
            LOG.warning('Auto-advance to the next track failed: %s', exc.reason)
            with self.lock:
                if self.sessions.get(udn) is session:
                    session.idle = True
            return
        if result.get('success'):
            LOG.info('Advanced to queue position %d', following + 1)

    def wants_awake(self) -> bool:
        """Keep Windows awake while any renderer is playing or paused in a
        session, including the gaps between tracks and a long Pause."""
        with self.lock:
            return any(not s.idle for s in self.sessions.values())

    def run_monitor(self, stop: threading.Event) -> None:
        awake = False
        while not stop.wait(MONITOR_INTERVAL):
            for udn in list(self.sessions):
                try:
                    self.monitor_once(udn)
                except Exception:
                    LOG.exception('Queue monitor error')
            wanted = self.wants_awake()
            if wanted != awake:
                keep_awake(wanted)  # this long-lived thread holds the request
                awake = wanted
        keep_awake(False)

    def control(self, udn: str, action: str, credential: str, *, cookie: str = '') -> dict:
        with self.in_flight(udn):
            seq = self.arrive(udn)
            self.authorize_control(credential, cookie)
            device = self.ensure_device(udn)
            self.commit(udn, seq, action)
            with self.lock_for(udn):
                if not self.claim_turn(udn, seq):
                    # Newer playback already reached the renderer; it stands.
                    return {'success': True, 'superseded': True}
                success = self.soap(device, action, {'Speed': '1'} if action == 'Play' else {}) is not None
                # The user left this renderer: revoke even if it did not answer,
                # cut relays already open, and end the queue session.
                if action == 'Stop':
                    with self.lock:
                        self.last_track.pop(udn, None)
                        self.sessions.pop(udn, None)
                    self.retire_older_grants(device.ip)
                return {'success': success}

    def status(self, udn: str, credential: str, cookie: str = '') -> dict:
        self.authenticate(credential, cookie)
        device = self.ensure_device(udn)
        with self.lock:
            snapshot = self.snapshots.get(udn)
        if snapshot and time.monotonic() - snapshot[0] < SNAPSHOT_FRESH:
            # The monitor read it a moment ago: spare the renderer a second poll.
            _, state, position, duration, uri = snapshot
        else:
            state = self.transport(device)
            position, duration, uri = self.position(device)
        track_id = None
        match = MEDIA_RE.fullmatch(urlparse(uri).path) if uri else None
        if match:
            tok, tid, ext = match.groups()
            # Polling the loaded stream keeps its grant alive through a pause.
            with contextlib.suppress(HTTPFailure):
                track_id = self.grant_for(tok, int(tid), ext, device.ip).track_id
        return {'state': state, 'position': position, 'duration': duration,
                'track_id': track_id, 'is_playing': state == 'PLAYING'}

def is_identity_header(name: str) -> bool:
    """A proxy's forwarding or Tailscale identity claim."""
    return name.lower() in PROXY_IDENTITY_HEADERS or name.lower().startswith('tailscale-')


def strip_hops(headers, *, body: bool = False) -> dict[str, str]:
    out = {}
    connection_tokens = {x.strip().lower() for x in headers.get('Connection', '').split(',')}
    for k, value in headers.items():
        if k.lower() in HOP_HEADERS | connection_tokens or k.lower() in {'content-length', 'accept-encoding', 'origin', 'referer'}:
            continue
        # Identity and forwarding claims are this bridge's to judge, never
        # the parent's (main also refuses passwordless access when it sees them).
        if is_identity_header(k):
            continue
        out[k] = value
    return out


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    protocol_version = 'HTTP/1.0'  # For responses without Content-Length, EOF is framing.
    server_version = 'VelvetRemoteBridge/0.1'
    timeout = REQUEST_TIMEOUT
    headers_sent = False

    def log_request(self, code='-', size='-'):
        # Never log URLs, whose media paths contain time-limited credentials.
        LOG.info('%s %s %s', self.client_address[0], self.command, code)

    def log_message(self, fmt, *args):
        LOG.info('%s - request error', self.client_address[0])

    def end_headers(self):
        self.headers_sent = True
        super().end_headers()

    def do_HEAD(self):
        self.dispatch()

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_PATCH(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def do_OPTIONS(self):
        self.dispatch()

    def respond_bytes(self, content: bytes, content_type: str):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def respond_json(self, payload: dict | list, status: int = 200):
        if self.headers_sent:
            # Mid-body: a JSON error here would be spliced into the audio.
            self.close_connection = True
            return
        content = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def dispatch(self):
        try:
            if self.server.role == 'media':
                return self.dispatch_media()
            return self.dispatch_control()
        except HTTPFailure as exc:
            body = {'error': exc.reason, 'detail': exc.reason}
            if exc.code:
                body['code'] = exc.code
            self.respond_json(body, exc.status)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (OSError, http.client.HTTPException) as exc:
            # Upstream stalled or dropped (Tailscale hiccup, parent restart).
            LOG.warning('Relay interrupted: %s', type(exc).__name__)
            with contextlib.suppress(OSError):
                self.respond_json({'error': 'Parent Velvet stream interrupted'}, 502)
        except Exception:
            LOG.exception('Unexpected bridge error')
            with contextlib.suppress(OSError):
                self.respond_json({'error': 'Internal bridge error'}, 500)

    def dispatch_media(self):
        if self.command not in ('GET', 'HEAD'):
            raise HTTPFailure(405, 'Media listener is read only')
        url = urlparse(self.path)
        if url.query or url.fragment:
            raise HTTPFailure(404, 'Unknown media path')
        bridge = self.server.bridge
        cover = COVER_RE.fullmatch(url.path)
        if cover:
            grant = bridge.grant_for(cover.group(1), None, None, self.client_address[0])
            if not grant.album_id:
                raise HTTPFailure(404, 'No album art')
            with bridge.upstream(f'/api/covers/{grant.album_id}/thumb?size=medium', method=self.command,
                                 headers={'X-API-Key': grant.credential} if grant.credential else {},
                                 timeout=30) as upstream:
                self.relay(upstream)
            return
        match = MEDIA_RE.fullmatch(url.path)
        if not match:
            raise HTTPFailure(404, 'Unknown media path')
        token, track, ext = match.groups()
        grant = bridge.grant_for(token, int(track), ext, self.client_address[0])
        headers = {'Accept': grant.mime}
        if grant.credential:
            headers['X-API-Key'] = grant.credential
        # Main adds contentFeatures/transferMode only for renderers that ask.
        if self.headers.get('getcontentFeatures.dlna.org'):
            headers['getcontentFeatures.dlna.org'] = self.headers['getcontentFeatures.dlna.org']
        requested = self.headers.get('Range', '')
        # An unparseable or multi-range header is ignored (RFC 9110): full 200.
        if re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', requested):
            headers['Range'] = requested
        with bridge.track_stream(grant.token, self.client_address[0]) as stream:
            keep_awake(True)
            try:
                # Relay original FLAC bytes, with Range/HEAD support and NO transcoding.
                with bridge.open_media(f'/api/stream/{grant.track_id}?upsample=none&replaygain=off',
                                       self.command, headers, stream) as upstream:
                    self.relay(upstream, stream_timeout=MEDIA_STALL_TIMEOUT, cancelled=stream.cancelled)
            finally:
                keep_awake(False)

    def read_json(self) -> dict:
        try:
            payload = json.loads(self.read_body())
            if not isinstance(payload, dict):
                raise ValueError('Expected JSON object')
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPFailure(400, 'Invalid cast JSON') from exc
        return payload

    def read_body(self) -> bytes:
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except ValueError as exc:
            raise HTTPFailure(400, 'Invalid Content-Length') from exc
        if n < 0 or n > BODY_LIMIT:
            raise HTTPFailure(413, 'Request too large')
        return self.rfile.read(n)

    def dispatch_control(self):
        bridge = self.server.bridge
        proxied = any(is_identity_header(k) for k in self.headers.keys())
        if not bridge.peer_allowed(self.client_address[0], self.headers.get('Tailscale-User-Login', ''), proxied):
            raise HTTPFailure(403, 'This Tailscale device may not use the bridge '
                                   '(add it to allowed_peers in config.json)')
        if not bridge.cfg.host_allowed(self.headers.get('Host', '')):
            raise HTTPFailure(421, 'Open the bridge by its Tailscale IP or MagicDNS name '
                                   '(or add the name to allowed_hosts in config.json)')
        path = urlparse(self.path).path
        if path == '/bridge/health' and self.command == 'GET':
            return self.respond_json({'bridge': 'velvet-remote', 'upstream': bridge.cfg.upstream_url,
                                      'control_bind': bridge.cfg.control_bind, 'lan_ip': bridge.cfg.lan_ip,
                                      'devices': [d.ui() for d in bridge.devices.values()]})
        # The original Velvet browser attaches X-API-Key to all /api requests.
        credential = self.headers.get('X-API-Key', '')
        cookie = self.headers.get('Cookie', '')
        if self.command not in ('GET', 'HEAD', 'OPTIONS'):
            self.check_local_origin()
        if path == '/bridge/queue-sync.js' and self.command in ('GET', 'HEAD'):
            return self.respond_bytes(QUEUE_SYNC_JS.read_bytes(), 'text/javascript; charset=utf-8')
        match = BRIDGE_SESSION_RE.fullmatch(path)
        if match and self.command == 'GET':
            return self.respond_json(bridge.session_info(unquote(match.group(1)), credential, cookie))
        match = BRIDGE_QUEUE_RE.fullmatch(path)
        if match and self.command == 'POST':
            return self.respond_json(bridge.update_queue(unquote(match.group(1)), self.read_json(),
                                                         credential, cookie))
        if path in ('/api/zones', '/api/devices') and self.command == 'GET':
            bridge.authenticate(credential, cookie)
            devices = bridge.discover(force='refresh=true' in self.path.lower())
            if path == '/api/devices':
                return self.respond_json([d.ui() for d in devices])
            # The local bridge has NO native outputs. Parent M1 is deliberately
            # omitted because it cannot be controlled from the home LAN.
            return self.respond_json({'browser': {'id': 'browser', 'name': 'Local Web Player',
                                                   'type': 'browser', 'available': True},
                                      'native': {'available': False, 'devices': [], 'error': None},
                                      'network': {'devices': [d.ui() for d in devices]}})
        match = CAST_RE.fullmatch(path)
        if match and self.command == 'POST':
            return self.respond_json(bridge.cast(unquote(match.group(1)), self.read_json(), credential,
                                                 cookie=cookie))
        match = CAST_CONTROL_RE.fullmatch(path)
        if match and self.command == 'POST':
            return self.respond_json(bridge.control(unquote(match.group(1)), match.group(2), credential,
                                                    cookie=cookie))
        match = CAST_STATUS_RE.fullmatch(path)
        if match and self.command == 'GET':
            return self.respond_json(bridge.status(unquote(match.group(1)), credential, cookie))
        match = CAST_RESULT_RE.fullmatch(path)
        if match and self.command == 'GET':
            bridge.authenticate(credential, cookie)
            return self.respond_json(bridge.request_result(unquote(match.group(1)), unquote(match.group(2))))
        self.proxy()

    def check_local_origin(self) -> None:
        origin = self.headers.get('Origin')
        if origin:
            host = self.headers.get('Host', '')
            if origin not in (f'http://{host}', f'https://{host}'):
                raise HTTPFailure(403, 'Cross-site modification is blocked')

    def proxy(self):
        bridge = self.server.bridge
        path = self.path
        if not path.startswith('/') or path.startswith('//'):
            raise HTTPFailure(400, 'Invalid path')
        # Authenticate on the PARENT. Never persist credentials in the bridge.
        headers = strip_hops(self.headers)
        # Reject cross-site mutations; then translate the origin to the trusted
        # upstream origin so Velvet's existing admin-CSRF checks still apply.
        origin = self.headers.get('Origin')
        if origin and self.command not in ('GET', 'HEAD', 'OPTIONS'):
            self.check_local_origin()
            headers['Origin'] = bridge.cfg.upstream_url
        body = self.read_body() if self.command not in ('GET', 'HEAD') else None
        with bridge.upstream(path, method=self.command, headers=headers, data=body) as upstream:
            status = getattr(upstream, 'status', None) or upstream.code
            if (self.command == 'GET' and status == 200 and urlparse(path).path in VELVET_PAGES
                    and 'text/html' in upstream.headers.get('Content-Type', '')
                    and not upstream.headers.get('Content-Encoding')):
                return self.relay_page(upstream, status)
            self.relay(upstream)

    def relay_page(self, upstream, status: int):
        """Velvet's page with queue_sync.js added, so the bridge learns the
        queue and can play on while Safari is suspended."""
        page = upstream.read(MAX_HTML + 1)
        if len(page) > MAX_HTML:
            raise HTTPFailure(502, 'Parent page too large')
        end = page.lower().rfind(b'</body>')
        page = page[:end] + QUEUE_SYNC_TAG + page[end:] if end >= 0 else page + QUEUE_SYNC_TAG
        self.send_response(status)
        for key, value in upstream.headers.items():
            if key.lower() in HOP_HEADERS or key.lower() in {
                    'content-length', 'etag', 'last-modified', 'server', 'date', 'x-content-type-options'}:
                continue
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(page)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(page)

    def relay(self, upstream, stream_timeout: float = CONTROL_STREAM_TIMEOUT,
              cancelled: threading.Event | None = None):
        status = getattr(upstream, 'status', None) or upstream.code
        self.send_response(status)
        for key, value in upstream.headers.items():
            # send_response already wrote Server and Date: no duplicates.
            if key.lower() in HOP_HEADERS or key.lower() in {'x-content-type-options', 'server', 'date'}:
                # Preserve CSP, Content-Encoding and other security headers.
                continue
            self.send_header(key, value)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            # Short send slices: a blocked send cannot be interrupted from
            # another thread on Windows, so the loop itself notices a revoked
            # grant and a reader idle past `stream_timeout`.
            self.connection.settimeout(min(1.0, stream_timeout))
            # read1: forward what has arrived. read(n) waits for all n bytes,
            # holding audio back on a slow Tailscale link.
            read = getattr(upstream, 'read1', upstream.read)
            while True:
                chunk = read(SOCKET_CHUNK)
                if not chunk:
                    break
                self.send_body(chunk, stream_timeout, cancelled)

    def send_body(self, chunk: bytes, stream_timeout: float, cancelled: threading.Event | None):
        view = memoryview(chunk)
        last_progress = time.monotonic()
        while view:
            if cancelled is not None and cancelled.is_set():
                raise ConnectionAbortedError('media grant revoked')
            try:
                sent = self.connection.send(view)
            except TimeoutError:
                if time.monotonic() - last_progress >= stream_timeout:
                    raise
                continue
            view = view[sent:]
            last_progress = time.monotonic()


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, role: str, bridge: Bridge):
        self.role = role
        self.bridge = bridge
        super().__init__(address, BridgeHandler)


def serve(cfg: BridgeConfig):
    bridge = Bridge(cfg)
    control = BridgeHTTPServer((cfg.control_bind, cfg.control_port), 'control', bridge)
    try:
        media = BridgeHTTPServer((cfg.lan_ip, cfg.media_port), 'media', bridge)
    except BaseException:
        control.server_close()
        raise
    t = threading.Thread(target=media.serve_forever, daemon=True, name='media-relay')
    t.start()
    stop_monitor = threading.Event()
    threading.Thread(target=bridge.run_monitor, args=(stop_monitor,), daemon=True,
                     name='queue-monitor').start()
    try:
        LOG.info('Control UI: http://%s:%d', cfg.control_bind, cfg.control_port)
        LOG.info('EXN100 media (LAN): http://%s:%d', cfg.lan_ip, cfg.media_port)
        LOG.info('Parent Velvet: %s', cfg.upstream_url)
        LOG.info('No audio files or music library index will be copied.')
        if cfg.control_bind == '127.0.0.1':
            LOG.warning('Tailscale IP not detected. Phone cannot connect until control_bind is configured '
                        'or Tailscale Serve is enabled. NEVER enable Tailscale Funnel for the bridge: '
                        'the parent Velvet trusts every request it relays.')
        control.serve_forever()
    except KeyboardInterrupt:
        LOG.info('Stopping bridge')
    finally:
        stop_monitor.set()
        control.shutdown()
        media.shutdown()
        control.server_close()
        media.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Velvet Remote DLNA Bridge for Windows')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        cfg = BridgeConfig.load(Path(args.config))
        serve(cfg)
    except (ValueError, OSError, FileNotFoundError, json.JSONDecodeError) as exc:
        LOG.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
