#!/usr/bin/env python3
"""Velvet Remote Bridge: local DLNA control and byte-preserving media relay.

No audio files, search index or Velvet credentials are stored on disk.
Requires Python 3.10+ only; tested against Velvet-HiFi-MP main APIs (Oct 2026).
"""
from __future__ import annotations

import argparse
import contextlib
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
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import (HTTPHandler, HTTPSHandler, HTTPRedirectHandler,
                            Request, build_opener)
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
CAST_RE = re.compile(r'^/api/cast/([^/]+)$')
CAST_CONTROL_RE = re.compile(r'^/api/cast/control/([^/]+)/(Play|Pause|Stop)$')
CAST_STATUS_RE = re.compile(r'^/api/cast/status/([^/]+)$')
CAST_RESULT_RE = re.compile(r'^/api/cast/result/([^/]+)/([^/]+)$')
MEDIA_RE = re.compile(r'^/media/([A-Za-z0-9_-]{24,120})/(\d+)\.([a-z0-9]{2,5})$')
MIME = {'flac': 'audio/flac', 'wav': 'audio/wav', 'aiff': 'audio/aiff',
        'aif': 'audio/aiff', 'mp3': 'audio/mpeg', 'm4a': 'audio/mp4',
        'alac': 'audio/mp4', 'aac': 'audio/aac', 'ogg': 'audio/ogg'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(HTTPHandler(), HTTPSHandler(), NoRedirect())


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


def auto_tailscale_ip() -> str | None:
    paths = ['tailscale']
    if sys.platform == 'win32':
        paths.append(r'C:\Program Files\Tailscale\tailscale.exe')
    for executable in paths:
        try:
            output = subprocess.run([executable, 'ip', '-4'], capture_output=True,
                                    text=True, timeout=4, check=True).stdout.strip()
            a = ipaddress.ip_address(output)
            if a in ipaddress.ip_network('100.64.0.0/10'):
                return output
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return None


def auto_lan_ip() -> str:
    choices: list[str] = []
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
        return cls(url, control, cp, lan_ip, mp, network,
                   str(raw.get('preferred_renderer', 'EXN100')))


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

    def url(self, cfg: BridgeConfig) -> str:
        return f'http://{cfg.lan_ip}:{cfg.media_port}/media/{self.token}/{self.track_id}.{self.extension}'


class HTTPFailure(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Bridge:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.devices: dict[str, Renderer] = {}
        self.grants: dict[str, MediaGrant] = {}
        self.last_track: dict[str, int] = {}
        self.cast_results: dict[tuple[str, str], dict[str, Any] | None] = {}
        self.generations: dict[str, int] = {}
        self.locks: dict[str, threading.RLock] = {}
        self.lock = threading.RLock()
        self.last_scan = 0.0
        self.scan_lock = threading.Lock()
        self._auth_cache: dict[bytes, float] = {}

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

    def upstream_json(self, path: str, credential: str, *, cookie: str = '') -> dict:
        headers = {'X-API-Key': credential}
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

    def authenticate(self, credential: str) -> None:
        if not credential:
            raise HTTPFailure(401, 'Log into Velvet before using the bridge')
        digest = hashlib.sha256(credential.encode('utf-8')).digest()
        with self.lock:
            if self._auth_cache.get(digest, 0) > time.monotonic():
                return
        self.upstream_json('/api/stats', credential)
        with self.lock:
            self._auth_cache[digest] = time.monotonic() + 8
            if len(self._auth_cache) > 32:
                self._auth_cache = {k: v for k, v in self._auth_cache.items()
                                    if v > time.monotonic()}

    def authorize_control(self, credential: str, cookie: str = '') -> None:
        """Respect Velvet's separate administrator gate on playback mutations."""
        self.authenticate(credential)
        security_status = self.upstream_json('/api/security/status', credential, cookie=cookie)
        if security_status.get('admin_protected') and not security_status.get('admin_unlocked'):
            raise HTTPFailure(403, 'Administrator verification required in Velvet')

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
            if not name or not udn or len(udn) > 256:
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
                until = time.monotonic() + 2.0
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
                    try:
                        with self.upnp_open(location) as response:
                            payload = response.read(MAX_DEVICE_XML + 1)
                        if len(payload) > MAX_DEVICE_XML:
                            continue
                        item = self.parse_renderer(payload, location, addr[0])
                        if item:
                            found[item.udn] = item
                    except (URLError, HTTPError, TimeoutError, OSError, ValueError):
                        continue
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

    def bump(self, udn: str) -> int:
        with self.lock:
            gen = self.generations.get(udn, 0) + 1
            self.generations[udn] = gen
            return gen

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
                   duration: float, ext: str) -> MediaGrant:
        ttl = min(12 * 3600, max(3600, duration + 1800))
        token = secrets.token_urlsafe(32)
        grant = MediaGrant(token, track_id, device.ip, time.time() + ttl,
                           credential, MIME[ext], ext)
        with self.lock:
            for k, g in list(self.grants.items()):
                if g.expires_at < time.time():
                    del self.grants[k]
            self.grants[token] = grant
        return grant

    def retire_older_grants(self, device_ip: str, keep_token: str) -> None:
        with self.lock:
            for key, grant in list(self.grants.items()):
                if grant.renderer_ip == device_ip and key != keep_token:
                    del self.grants[key]

    def grant_for(self, token: str, track_id: int, ext: str, peer_ip: str) -> MediaGrant:
        with self.lock:
            grant = self.grants.get(token)
        if (not grant or grant.track_id != track_id or grant.extension != ext or
                grant.expires_at <= time.time() or peer_ip != grant.renderer_ip):
            raise HTTPFailure(403, 'Media ticket invalid, expired or wrong renderer')
        return grant

    def render_didl(self, track: dict, media_url: str, mime: str) -> str:
        title = html.escape(str(track.get('title') or 'Unknown Track'))
        artist = html.escape(str(track.get('artist_name') or 'Unknown Artist'))
        album = html.escape(str(track.get('album_title') or 'Unknown Album'))
        uri = html.escape(media_url, quote=True)
        duration = max(0, float(track.get('duration') or 0))
        seconds = int(duration)
        dstr = f'{seconds // 3600:02d}:{(seconds // 60) % 60:02d}:{seconds % 60:02d}'
        return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                f'<item id="{int(track["id"])}" parentID="0" restricted="1">'
                f'<dc:title>{title}</dc:title><upnp:artist>{artist}</upnp:artist>'
                f'<upnp:album>{album}</upnp:album>'
                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                f'<res protocolInfo="http-get:*:{mime}:*" duration="{dstr}">{uri}</res>'
                '</item></DIDL-Lite>')

    def cast(self, udn: str, payload: dict, credential: str, *, cookie: str = '') -> dict:
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
        generation = self.bump(udn)
        if rid:
            with self.lock:
                self.cast_results[(udn, rid)] = None
        grant = None
        set_uri_accepted = False
        try:
            track = self.upstream_json(f'/api/tracks/{raw_id}', credential)
            ext = str(track.get('format') or '').lower().lstrip('.')
            if ext not in MIME:
                filename = str(track.get('file_path') or '')
                ext = os.path.splitext(filename)[-1].lower().lstrip('.')
            if ext not in MIME:
                raise HTTPFailure(415, 'This track format is not yet supported by the remote bridge')
            if int(track.get('id', 0)) != raw_id:
                raise HTTPFailure(502, 'Parent Velvet returned the wrong track')
            with self.lock_for(udn):
                with self.lock:
                    if self.generations.get(udn) != generation:
                        result = {'success': False, 'superseded': True}
                        self._store_result(udn, rid, result)
                        return result
                # This ticket exists only in RAM, and is bound to the EXN100 IP.
                grant = self._new_grant(raw_id, device, credential, float(track.get('duration') or 0), ext)
                url = grant.url(self.cfg)
                metadata = self.render_didl(track, url, grant.mime)
                # Capture pause state BEFORE loading a new URI; SetURI may reset it.
                paused = recast and self.transport(device) == 'PAUSED_PLAYBACK'
                if self.soap(device, 'SetAVTransportURI', {
                    'CurrentURI': url, 'CurrentURIMetaData': metadata}) is None:
                    # Some UPnP renderers reject nonempty DIDL metadata.
                    if self.soap(device, 'SetAVTransportURI', {
                        'CurrentURI': url, 'CurrentURIMetaData': ''}) is None:
                        raise HTTPFailure(502, 'EXN100 rejected DLNA stream URL')
                set_uri_accepted = True
                if paused:
                    transport = 'loaded'
                else:
                    transport = 'playing' if self.soap(device, 'Play', {'Speed': '1'}) is not None else 'play_failed'
                sought = None
                if start > 0 and transport != 'play_failed':
                    seek_time = int(start)
                    sought = self.soap(device, 'Seek', {
                        'Unit': 'REL_TIME',
                        'Target': f'{seek_time // 3600:02d}:{seek_time // 60 % 60:02d}:{seek_time % 60:02d}'}) is not None
                with self.lock:
                    self.last_track[udn] = raw_id
                if transport in ('playing', 'loaded'):
                    self.retire_older_grants(device.ip, grant.token)
                result = {'success': transport in ('playing', 'loaded'), 'transport': transport,
                          'start_offset': 0, 'position_restored': sought,
                          'dsp': {'status': 'disabled', 'stream': 'direct', 'effective_profile_id': None},
                          'replaygain': {'mode': 'off', 'applied_db': 0}}
                self._store_result(udn, rid, result)
                return result
        except HTTPFailure as exc:
            if grant is not None and not set_uri_accepted:
                with self.lock:
                    self.grants.pop(grant.token, None)
            self._store_result(udn, rid, {'success': False, 'error': exc.reason})
            raise
        except (TypeError, ValueError) as exc:
            self._store_result(udn, rid, {'success': False, 'error': 'Invalid track metadata'})
            raise HTTPFailure(502, 'Invalid track metadata') from exc

    def control(self, udn: str, action: str, credential: str, *, cookie: str = '') -> dict:
        self.authorize_control(credential, cookie)
        device = self.ensure_device(udn)
        if action == 'Stop':
            self.bump(udn)
        with self.lock_for(udn):
            success = self.soap(device, action, {'Speed': '1'} if action == 'Play' else {}) is not None
            if success and action == 'Stop':
                with self.lock:
                    self.last_track.pop(udn, None)
                    for k, grant in list(self.grants.items()):
                        if grant.renderer_ip == device.ip:
                            del self.grants[k]
            return {'success': success}

    def status(self, udn: str, credential: str) -> dict:
        self.authenticate(credential)
        device = self.ensure_device(udn)
        state = self.transport(device)
        position, duration, uri = self.position(device)
        track_id = None
        match = MEDIA_RE.fullmatch(urlparse(uri).path) if uri else None
        if match:
            tok, tid, ext = match.groups()
            with self.lock:
                grant = self.grants.get(tok)
                if grant and grant.track_id == int(tid) and grant.extension == ext:
                    track_id = grant.track_id
        return {'state': state, 'position': position, 'duration': duration,
                'track_id': track_id, 'is_playing': state == 'PLAYING'}


def strip_hops(headers, *, body: bool = False) -> dict[str, str]:
    out = {}
    connection_tokens = {x.strip().lower() for x in headers.get('Connection', '').split(',')}
    for k, value in headers.items():
        if k.lower() in HOP_HEADERS | connection_tokens or k.lower() in {'content-length', 'accept-encoding', 'origin', 'referer', 'sec-fetch-site', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'}:
            continue
        out[k] = value
    return out


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    protocol_version = 'HTTP/1.0'  # For responses without Content-Length, EOF is framing.
    server_version = 'VelvetRemoteBridge/0.1'

    def log_message(self, fmt, *args):
        # Never log URLs, whose media paths contain time-limited credentials.
        LOG.info('%s - %s', self.client_address[0], fmt.split(' ')[0])

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

    def respond_json(self, payload: dict | list, status: int = 200):
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
            self.respond_json({'error': exc.reason, 'detail': exc.reason}, exc.status)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            LOG.exception('Unexpected bridge error')
            with contextlib.suppress(OSError):
                self.respond_json({'error': 'Internal bridge error'}, 500)

    def dispatch_media(self):
        if self.command not in ('GET', 'HEAD'):
            raise HTTPFailure(405, 'Media listener is read only')
        url = urlparse(self.path)
        match = MEDIA_RE.fullmatch(url.path)
        if not match or url.query or url.fragment:
            raise HTTPFailure(404, 'Unknown media path')
        token, track, ext = match.groups()
        grant = self.server.bridge.grant_for(token, int(track), ext, self.client_address[0])
        headers = {'X-API-Key': grant.credential, 'Accept': grant.mime}
        if 'Range' in self.headers:
            requested = self.headers['Range']
            if not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', requested):
                raise HTTPFailure(416, 'Invalid byte range')
            headers['Range'] = requested
        # Relay original FLAC bytes, with Range/HEAD support and NO transcoding.
        with self.server.bridge.upstream(f'/api/stream/{grant.track_id}?upsample=none&replaygain=off',
                                         method=self.command, headers=headers, timeout=60) as upstream:
            self.relay(upstream)

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
        path = urlparse(self.path).path
        if path == '/bridge/health' and self.command == 'GET':
            return self.respond_json({'bridge': 'velvet-remote', 'upstream': bridge.cfg.upstream_url,
                                      'control_bind': bridge.cfg.control_bind, 'lan_ip': bridge.cfg.lan_ip,
                                      'devices': [d.ui() for d in bridge.devices.values()]})
        # The original Velvet browser attaches X-API-Key to all /api requests.
        credential = self.headers.get('X-API-Key', '')
        if self.command not in ('GET', 'HEAD', 'OPTIONS'):
            self.check_local_origin()
        if path in ('/api/zones', '/api/devices') and self.command == 'GET':
            bridge.authenticate(credential)
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
            try:
                payload = json.loads(self.read_body())
                if not isinstance(payload, dict):
                    raise ValueError('Expected JSON object')
            except (ValueError, UnicodeDecodeError) as exc:
                raise HTTPFailure(400, 'Invalid cast JSON') from exc
            return self.respond_json(bridge.cast(unquote(match.group(1)), payload, credential,
                                                 cookie=self.headers.get('Cookie', '')))
        match = CAST_CONTROL_RE.fullmatch(path)
        if match and self.command == 'POST':
            return self.respond_json(bridge.control(unquote(match.group(1)), match.group(2), credential,
                                                    cookie=self.headers.get('Cookie', '')))
        match = CAST_STATUS_RE.fullmatch(path)
        if match and self.command == 'GET':
            return self.respond_json(bridge.status(unquote(match.group(1)), credential))
        match = CAST_RESULT_RE.fullmatch(path)
        if match and self.command == 'GET':
            bridge.authenticate(credential)
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
            self.relay(upstream)

    def relay(self, upstream):
        status = getattr(upstream, 'status', None) or upstream.code
        self.send_response(status)
        for key, value in upstream.headers.items():
            if key.lower() in HOP_HEADERS or key.lower() == 'x-content-type-options':
                # Preserve CSP, Content-Encoding and other security headers.
                continue
            self.send_header(key, value)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            while True:
                chunk = upstream.read(SOCKET_CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)


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
    try:
        LOG.info('Control UI: http://%s:%d', cfg.control_bind, cfg.control_port)
        LOG.info('EXN100 media (LAN): http://%s:%d', cfg.lan_ip, cfg.media_port)
        LOG.info('Parent Velvet: %s', cfg.upstream_url)
        LOG.info('No audio files or music library index will be copied.')
        if cfg.control_bind == '127.0.0.1':
            LOG.warning('Tailscale IP not detected. Phone cannot connect until control_bind is configured or Tailscale Serve is enabled.')
        control.serve_forever()
    except KeyboardInterrupt:
        LOG.info('Stopping bridge')
    finally:
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
