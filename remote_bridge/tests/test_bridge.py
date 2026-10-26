"""Offline unit and integration tests; no EXN100 or Tailscale required."""
import contextlib
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import io
import json
import re
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge

AUDIO = b'fLaC' + bytes(range(256)) * 32
CREDENTIAL = 'credential-for-testing'
COOKIE = 'velvet_admin_session=test-cookie'
COVER = bytes.fromhex('ffd8ffe0') + b'cover'


class Parent:
    # main treats a tailnet peer (the bridge) as a passwordless listener
    passwordless = False
    seen_headers = []
    track_headers = {}  # what the parent last received for /api/tracks/5


class ParentHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond()

    def respond(self):
        if self.path == '/api/security/status':
            self.reply({'admin_protected': True, 'admin_unlocked': COOKIE in self.headers.get('Cookie', '')})
        elif self.path == '/api/stats':
            if self.headers.get('X-API-Key') != CREDENTIAL and not Parent.passwordless:
                self.reply({'error': 'Unauthorized'}, 401)
            else:
                self.reply({'tracks': 1})
        elif self.path == '/api/tracks/5':
            Parent.track_headers = {k.lower(): v for k, v in self.headers.items()}
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
            else:
                self.reply({'id': 5, 'title': '测试 测试', 'artist_name': '徐佳莹',
                            'album_title': 'Album', 'album_id': 9, 'duration': 210, 'format': 'FLAC',
                            'file_size': len(AUDIO), 'channels': 2,
                            'sample_rate': 96000, 'bit_depth': 24, 'file_path': 'D:/Music/t.flac'})
        elif re.fullmatch(r'/api/tracks/(6|7|8|98)', self.path):
            # The rest of the album, for queue tests (track 8 lasts over an hour).
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
                return
            tid = int(self.path.rsplit('/', 1)[1])
            self.reply({'id': tid, 'title': f'Track {tid}', 'artist_name': '徐佳莹', 'album_title': 'Album',
                        'album_id': 9, 'duration': 4000 if tid == 8 else 200,
                        'format': 'DSF' if tid == 98 else 'FLAC',  # 98: a format the bridge refuses
                        'sample_rate': 44100, 'bit_depth': 16, 'file_path': f'D:/Music/{tid}.' + ('dsf' if tid == 98 else 'flac')})
        elif self.path.startswith('/api/stream/5'):
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
                return
            assert 'dsp=' not in self.path and 'upsample=none' in self.path
            Parent.seen_headers.append(dict(self.headers))
            data = AUDIO
            status = 200
            range_value = self.headers.get('Range')
            if range_value:
                m = bridge.re.fullmatch(r'bytes=(\d+)-(\d*)', range_value)
                if m:
                    lo = int(m.group(1))
                    hi = int(m.group(2)) if m.group(2) else len(data) - 1
                    data = data[lo:hi + 1]
                    status = 206
                else:
                    self.reply({'error': 'Bad Range'}, 416)
                    return
            self.send_response(status)
            self.send_header('Content-Type', 'audio/flac')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(len(data)))
            if status == 206:
                self.send_header('Content-Range', f'bytes {lo}-{hi}/{len(AUDIO)}')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        elif self.path == '/api/covers/9/thumb?size=medium':
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(COVER)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(COVER)
        elif self.path == '/':
            self.send_response(200)
            html = b'<html><body>Parent Velvet UI - Artist Album Track</body></html>'
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(html)))
            self.send_header('Content-Security-Policy', "default-src 'self'")
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(html)
        else:
            self.reply({'detail': 'Not found'}, 404)

    def do_POST(self):
        if self.path == '/api/security/admin-login':
            n = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(n))
            ok = payload.get('password') == 'admin-password'
            # As main's unlock_admin: a session cookie scoped to /api only.
            self.reply({'success': ok}, 200 if ok else 403,
                       {'Set-Cookie': f'{COOKIE}; Path=/api; HttpOnly; SameSite=Strict'} if ok else None)
        else:
            self.reply({'detail': 'Not found'}, 404)

    def reply(self, obj, status=200, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Type', 'application/json')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)


class ServerFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ThreadingHTTPServer(('127.0.0.1', 0), ParentHandler)
        cls.parent_thread = threading.Thread(target=cls.parent.serve_forever, daemon=True)
        cls.parent_thread.start()
        cfg = bridge.BridgeConfig('http://127.0.0.1:' + str(cls.parent.server_port),
                                  '127.0.0.1', 0, '127.0.0.1', 0,
                                  ipaddress.ip_network('127.0.0.0/8'))
        cls.core = bridge.Bridge(cfg)
        cls.renderer = bridge.Renderer('uuid:EXN100-test', 'EXN100 Living Room', '127.0.0.1',
                                       'http://127.0.0.1:9999/upnp/control')
        cls.core.devices[cls.renderer.udn] = cls.renderer
        cls.core.last_scan = time.monotonic()
        cls.control = bridge.BridgeHTTPServer(('127.0.0.1', 0), 'control', cls.core)
        cls.media = bridge.BridgeHTTPServer(('127.0.0.1', 0), 'media', cls.core)
        cls.core.cfg = bridge.BridgeConfig(cfg.upstream_url, cfg.control_bind, cls.control.server_port,
                                           cfg.lan_ip, cls.media.server_port, cfg.renderer_network)
        cls.threads = [threading.Thread(target=s.serve_forever, daemon=True)
                       for s in (cls.control, cls.media)]
        for t in cls.threads:
            t.start()
        cls.control_base = f'http://127.0.0.1:{cls.control.server_port}'
        cls.media_base = f'http://127.0.0.1:{cls.media.server_port}'

    @classmethod
    def tearDownClass(cls):
        for s in (cls.control, cls.media, cls.parent):
            s.shutdown()
            s.server_close()

    def tearDown(self):
        # Let relays from this test finish before the next one resets state.
        deadline = time.monotonic() + 8
        while self.core.peer_streams and time.monotonic() < deadline:
            time.sleep(0.05)

    def setUp(self):
        Parent.passwordless = False
        Parent.seen_headers.clear()
        with self.core.lock:
            self.core.grants.clear()
            self.core.last_track.clear()
            self.core.cast_results.clear()
            self.core.generations.clear()
            self.core._auth_cache.clear()
            self.core.arrivals.clear()
            self.core.intents.clear()
            self.core.executed.clear()
            self.core.sessions.clear()
            self.core.snapshots.clear()
            self.core.inflight.clear()
            self.core.streams.clear()
            self.core.peer_streams.clear()

    def request(self, path, *, method='GET', body=None, headers=None, role='control'):
        url = (self.control_base if role == 'control' else self.media_base) + path
        hdr = dict(headers or {})
        data = json.dumps(body).encode('utf8') if body is not None else None
        if data is not None:
            hdr['Content-Type'] = 'application/json'
        req = Request(url, method=method, data=data, headers=hdr)
        try:
            with build_opener(HTTPHandler()).open(req, timeout=10) as res:
                return res.status, dict(res.headers), res.read()
        except HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def test_proxy_serves_unmodified_parent_ui_and_csp(self):
        status, hdr, data = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Parent Velvet UI', data)
        self.assertEqual(hdr.get('Content-Security-Policy'), "default-src 'self'")

    def test_upstream_api_auth_forwarded(self):
        status, _, _ = self.request('/api/tracks/5')
        self.assertEqual(status, 401)
        status, _, data = self.request('/api/tracks/5', headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['title'], '测试 测试')

    def test_zones_local_only_and_authenticated(self):
        status, _, _ = self.request('/api/zones')
        self.assertEqual(status, 401)
        status, _, data = self.request('/api/zones', headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(status, 200)
        obj = json.loads(data)
        self.assertEqual(obj['network']['devices'][0]['name'], 'EXN100 Living Room')
        self.assertEqual(obj['native']['devices'], [])

    def test_admin_gate_prevents_cast_without_parent_session(self):
        status, _, data = self.request('/api/cast/uuid:EXN100-test', method='POST',
                                       body={'track_id': 5}, headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(status, 403)
        # Velvet's managementFetch opens its admin dialog only for this code.
        self.assertEqual(json.loads(data)['code'], 'admin_password_required')

    # ── Review 6 / P1-1: what a browser's own cookie handling sends ──

    def test_admin_cookie_from_a_real_cookie_jar_reaches_cast_and_queue_edits(self):
        self.fake_renderer()
        jar = http.cookiejar.CookieJar()
        browser = build_opener(HTTPHandler(), HTTPCookieProcessor(jar))
        def call(path, body=None):
            req = Request(self.control_base + path, method='GET' if body is None else 'POST',
                          data=None if body is None else json.dumps(body).encode(),
                          headers={'X-API-Key': CREDENTIAL, 'Content-Type': 'application/json',
                                   'Origin': self.control_base})
            try:
                with browser.open(req, timeout=10) as res:
                    return res.status, json.loads(res.read())
            except HTTPError as err:
                return err.code, json.loads(err.read())
        cast = {'track_id': 5, 'bridge_queue': {'ids': [5, 6, 7], 'index': 0, 'repeat': 'none'}}
        self.assertEqual(call(f'/api/cast/{self.UDN}', cast)[0], 403)  # not unlocked yet
        # The login goes through the bridge's proxy; the parent's cookie says Path=/api.
        self.assertEqual(call('/api/security/admin-login', {'password': 'admin-password'}), (200, {'success': True}))
        status, result = call(f'/api/cast/{self.UDN}', cast)
        self.assertEqual((status, result['success']), (200, True))
        status, info = call(f'/api/bridge/session/{self.UDN}')
        self.assertEqual((status, info['repeat']), (200, 'none'))
        status, answer = call(f'/api/bridge/queue/{self.UDN}', {'track_id': 5, 'ids': [5, 7], 'index': 0,
                                                                'repeat': 'all', 'expected_revision': info['revision']})
        self.assertEqual((status, answer['success']), (200, True))
        session = self.core.sessions[self.UDN]
        self.assertEqual((session.ids, session.repeat), ((5, 7), 'all'))
        # Why 0.1.5's /bridge/queue answered 403 in every real browser:
        for path, sent in (('/api/bridge/queue/x', True), ('/bridge/queue/x', False)):
            probe = Request(self.control_base + path)
            jar.add_cookie_header(probe)
            self.assertEqual(probe.has_header('Cookie'), sent, path)

    def test_cast_then_lossless_media_range_head_and_status(self):
        saw = []
        def fake_soap(device, action, values=None):
            saw.append((action, values))
            return b'<ok/>'
        with patch.object(self.core, 'soap', side_effect=fake_soap), \
             patch.object(self.core, 'transport', return_value='PLAYING'), \
             patch.object(self.core, 'position', side_effect=lambda d: (7, 210, self.core.grants[next(iter(self.core.grants))].url(self.core.cfg))):
            status, _, data = self.request('/api/cast/uuid:EXN100-test', method='POST',
                                           body={'track_id': 5, 'request_id': 'req-1'},
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
            self.assertEqual(status, 200, data)
            self.assertEqual(json.loads(data)['transport'], 'playing')
            self.assertEqual([x[0] for x in saw], ['SetAVTransportURI', 'Play'])
            self.assertIn('测试 测试', saw[0][1]['CurrentURIMetaData'])
            grant = next(iter(self.core.grants.values()))
            path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
            status, hdr, data = self.request(path, role='media')
            self.assertEqual(status, 200)
            self.assertEqual(data, AUDIO, 'media relay changed the original FLAC bytes')
            self.assertEqual(hdr['Content-Type'], 'audio/flac')
            status, hdr, data = self.request(path, role='media', method='HEAD')
            self.assertEqual(status, 200)
            self.assertEqual(data, b'')
            self.assertEqual(int(hdr['Content-Length']), len(AUDIO))
            status, hdr, data = self.request(path, role='media', headers={'Range': 'bytes=10-19'})
            self.assertEqual(status, 206)
            self.assertEqual(data, AUDIO[10:20])
            self.assertEqual(hdr['Content-Range'], f'bytes 10-19/{len(AUDIO)}')
            status, _, data = self.request('/api/cast/status/uuid:EXN100-test',
                                           headers={'X-API-Key': CREDENTIAL})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['track_id'], 5)
            status, _, data = self.request('/api/cast/result/uuid:EXN100-test/req-1',
                                           headers={'X-API-Key': CREDENTIAL})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(data)['done'])

    def test_stop_revokes_ticket(self):
        with patch.object(self.core, 'soap', return_value=b'<ok/>'):
            status, _, _ = self.request('/api/cast/uuid:EXN100-test', method='POST',
                                        body={'track_id': 5}, headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
            self.assertEqual(status, 200)
            grant = next(iter(self.core.grants.values()))
            status, _, data = self.request('/api/cast/control/uuid:EXN100-test/Stop', method='POST',
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['success'], True)
            path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
            status, _, _ = self.request(path, role='media')
            self.assertEqual(status, 403)

    def test_stop_revokes_ticket_even_if_renderer_silent(self):
        _, grant = self.cast_ok()
        with patch.object(self.core, 'soap', return_value=None):
            status, _, data = self.request('/api/cast/control/uuid:EXN100-test/Stop', method='POST',
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual((status, json.loads(data)['success']), (200, False))
        self.assertNotIn(grant.token, self.core.grants)

    def test_expired_ticket_and_wrong_track_rejected(self):
        with self.core.lock:
            self.core.grants['A' * 36] = bridge.MediaGrant('A' * 36, 5, '127.0.0.1',
                                                              time.time()-1, CREDENTIAL, 'audio/flac', 'flac')
        status, _, _ = self.request('/media/' + 'A'*36 + '/5.flac', role='media')
        self.assertEqual(status, 403)
        status, _, _ = self.request('/media/' + 'A'*36 + '/6.flac', role='media')
        self.assertEqual(status, 403)
        status, _, _ = self.request('/media/' + 'A'*36 + '/5.flac', method='POST', role='media')
        self.assertEqual(status, 405)

    def test_reject_dsp_and_replaygain_not_silently_transcoded(self):
        for body in ({'track_id': 5, 'dsp_profile_id': 17}, {'track_id': 5, 'replaygain': 'album'}):
            status, _, data = self.request('/api/cast/uuid:EXN100-test', method='POST', body=body,
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
            self.assertEqual(status, 422, data)
        self.assertFalse(self.core.grants)

    def test_cross_origin_cast_rejected(self):
        status, _, _ = self.request('/api/cast/uuid:EXN100-test', method='POST', body={'track_id': 5},
                                    headers={'X-API-Key': CREDENTIAL, 'Origin': 'https://evil.example',
                                             'Cookie': COOKIE})
        self.assertEqual(status, 403)

    def test_invalid_json_returns_400(self):
        status, _, _ = self.request('/api/cast/uuid:EXN100-test', method='POST',
                                    headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual(status, 400)

    def cast_ok(self, **extra):
        with patch.object(self.core, 'soap', return_value=b'<ok/>') as soap:
            status, _, data = self.request('/api/cast/uuid:EXN100-test', method='POST',
                                           body={'track_id': 5, **extra},
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual(status, 200, data)
        return soap, next(iter(self.core.grants.values()))

    def test_passwordless_parent_lists_renderers_without_api_key(self):
        # Through the bridge, main reports passwordless_listening and the UI
        # never obtains a key: zones must still come back.
        Parent.passwordless = True
        status, _, data = self.request('/api/zones')
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)['network']['devices'][0]['udn'], 'uuid:EXN100-test')

    def test_rebinding_host_rejected(self):
        status, _, _ = self.request('/api/zones', headers={'X-API-Key': CREDENTIAL,
                                                           'Host': 'evil.example:8877'})
        self.assertEqual(status, 421)
        status, _, _ = self.request('/', headers={'Host': 'evil.example'})
        self.assertEqual(status, 421)

    def test_didl_advertises_byte_seek_format_and_cover(self):
        soap, grant = self.cast_ok()
        meta = next(c.args[2]['CurrentURIMetaData'] for c in soap.call_args_list
                    if c.args[1] == 'SetAVTransportURI')
        self.assertIn('DLNA.ORG_OP=01', meta)
        self.assertIn('sampleFrequency="96000" bitsPerSample="24"', meta)
        self.assertIn(f'size="{len(AUDIO)}"', meta)
        self.assertIn(f'/media/{grant.token}/cover.jpg</upnp:albumArtURI>', meta)
        status, hdr, data = self.request(f'/media/{grant.token}/cover.jpg', role='media')
        self.assertEqual((status, data, hdr['Content-Type']), (200, COVER, 'image/jpeg'))

    def test_dlna_content_features_request_forwarded(self):
        _, grant = self.cast_ok()
        path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
        status, _, _ = self.request(path, role='media', method='HEAD',
                                    headers={'getcontentFeatures.dlna.org': '1'})
        self.assertEqual(status, 200)
        sent = {k.lower(): v for k, v in Parent.seen_headers[-1].items()}
        self.assertEqual(sent.get('getcontentfeatures.dlna.org'), '1')

    def test_unparseable_range_served_whole(self):
        _, grant = self.cast_ok()
        path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
        status, _, data = self.request(path, role='media', headers={'Range': 'bytes=0-1,4-5'})
        self.assertEqual((status, data), (200, AUDIO))

    def test_single_relay_header_set(self):
        _, grant = self.cast_ok()
        path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
        url = self.media_base + path
        with build_opener(HTTPHandler()).open(Request(url, method='HEAD'), timeout=10) as res:
            self.assertEqual(len(res.headers.get_all('Date')), 1)
            self.assertEqual(len(res.headers.get_all('Server')), 1)

    def test_upstream_drop_mid_stream_never_splices_json_into_audio(self):
        _, grant = self.cast_ok()
        real_open = self.core.open_media
        class Stalling:
            # The parent link times out after the first chunk (Tailscale drop).
            def __init__(self, response):
                self.response, self.reads = response, 0
            def __getattr__(self, name):
                return getattr(self.response, name)
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                self.response.close()
            def read(self, n):
                self.reads += 1
                if self.reads > 1:
                    raise TimeoutError('timed out')
                return self.response.read(100)
            read1 = read
        @contextlib.contextmanager
        def open_stalling(*args):
            with real_open(*args) as response:
                yield Stalling(response)
        stalling = patch.object(self.core, 'open_media', side_effect=open_stalling)
        with stalling:
            sock = socket.create_connection(('127.0.0.1', self.media.server_port), timeout=10)
            with sock:
                sock.sendall(f'GET /media/{grant.token}/{grant.track_id}.{grant.extension} HTTP/1.1\r\n'
                             'Host: x\r\n\r\n'.encode())
                raw = b''
                while chunk := sock.recv(65536):
                    raw += chunk
        head, body = raw.split(b'\r\n\r\n', 1)
        self.assertTrue(head.startswith(b'HTTP/1.0 200'))
        self.assertEqual(body, AUDIO[:100])
        self.assertNotIn(b'HTTP/1.0 ', body)

    def test_grant_renewed_by_media_reads(self):
        _, grant = self.cast_ok()
        with self.core.lock:
            self.core.grants[grant.token] = bridge.replace(grant, expires_at=time.time() + 5)
        path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
        self.assertEqual(self.request(path, role='media', method='HEAD')[0], 200)
        self.assertGreater(self.core.grants[grant.token].expires_at, time.time() + 3600)

    def test_renderer_rejecting_uri_stops_then_retries(self):
        calls = []
        def soap(device, action, values=None):
            calls.append((action, (values or {}).get('CurrentURIMetaData')))
            if action == 'SetAVTransportURI' and 'Stop' not in [c[0] for c in calls]:
                return None
            return b'<ok/>'
        with patch.object(self.core, 'soap', side_effect=soap), patch.object(bridge.time, 'sleep'):
            status, _, data = self.request('/api/cast/uuid:EXN100-test', method='POST', body={'track_id': 5},
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual(status, 200, data)
        self.assertEqual([c[0] for c in calls], ['SetAVTransportURI', 'SetAVTransportURI', 'Stop',
                                                 'SetAVTransportURI', 'Play'])
        self.assertTrue(json.loads(data)['success'])

    def test_renderer_refusal_reported_like_main(self):
        def soap(device, action, values=None):
            return None if action == 'SetAVTransportURI' else b'<ok/>'
        with patch.object(self.core, 'soap', side_effect=soap), patch.object(bridge.time, 'sleep'):
            status, _, data = self.request('/api/cast/uuid:EXN100-test', method='POST',
                                           body={'track_id': 5, 'request_id': 'r-fail'},
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['success'], False)
        self.assertFalse(self.core.grants)
        self.assertTrue(self.core.request_result('uuid:EXN100-test', 'r-fail')['done'])

    # ── Review 2 / P1-2: a Pause sent after a Cast is never undone by it ──

    def cast_racing_pause(self, block_point):
        """Cast blocked at `block_point` ('auth' or 'lookup'); Pause sent and
        finished meanwhile; then the cast resumes. Returns SOAP order, result."""
        actions, entered, release = [], threading.Event(), threading.Event()
        real_json, real_auth = self.core.upstream_json, self.core.authorize_control
        def slow_json(path, *a, **k):
            if block_point == 'lookup' and path.startswith('/api/tracks/'):
                entered.set()
                release.wait(10)
            return real_json(path, *a, **k)
        def slow_auth(*a, **k):
            if block_point == 'auth' and threading.current_thread().name == 'cast-request':
                entered.set()
                release.wait(10)
            return real_auth(*a, **k)
        def soap(device, action, values=None):
            actions.append(action)
            return b'<ok/>'
        result = {}
        def cast():
            result['cast'] = self.request('/api/cast/uuid:EXN100-test', method='POST', body={'track_id': 5},
                                          headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        with patch.object(self.core, 'upstream_json', side_effect=slow_json), \
             patch.object(self.core, 'authorize_control', side_effect=slow_auth), \
             patch.object(self.core, 'soap', side_effect=soap), \
             patch.object(self.core, 'transport', return_value='PLAYING'):
            # The handler thread, not ours, runs the cast: name it via the request.
            real_dispatch = bridge.BridgeHandler.dispatch_control
            def named(handler):
                if handler.path.startswith('/api/cast/uuid'):
                    threading.current_thread().name = 'cast-request'
                return real_dispatch(handler)
            with patch.object(bridge.BridgeHandler, 'dispatch_control', named):
                t = threading.Thread(target=cast)
                t.start()
                self.assertTrue(entered.wait(5), 'cast never reached the slow step')
                status, _, data = self.request('/api/cast/control/uuid:EXN100-test/Pause', method='POST',
                                               headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
                self.assertEqual((status, json.loads(data)['success']), (200, True))
                release.set()
                t.join(10)
        status, _, data = result['cast']
        self.assertEqual(status, 200, data)
        return actions, json.loads(data)

    def test_pause_during_slow_track_lookup_is_not_overridden_by_cast(self):
        actions, res = self.cast_racing_pause('lookup')
        self.assertEqual(actions[0], 'Pause')
        self.assertIn('SetAVTransportURI', actions)
        self.assertNotIn('Play', actions)
        self.assertEqual(res['transport'], 'loaded')

    def test_pause_during_slow_admin_check_is_not_overridden_by_cast(self):
        # Numbered on arrival: the cast arrived first even though its admin
        # check finished after the Pause was handled.
        actions, res = self.cast_racing_pause('auth')
        self.assertNotIn('Play', actions)
        self.assertEqual(res['transport'], 'loaded')

    def test_cast_after_pause_plays(self):
        with patch.object(self.core, 'soap', return_value=b'<ok/>'):
            self.request('/api/cast/control/uuid:EXN100-test/Pause', method='POST',
                         headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        soap, _ = self.cast_ok()
        self.assertIn('Play', [c.args[1] for c in soap.call_args_list])

    # ── Review 2 / P1-5 and P1-6: open relays are revoked and bounded ──

    def endless_stream(self):
        """Patch the parent's stream to never end, as a long FLAC would."""
        class Endless:
            status, code = 200, 200
            headers = {'Content-Type': 'audio/flac'}
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def read(self, n):
                return b'\0' * n
        @contextlib.contextmanager
        def open_media(*args):
            yield Endless()
        return patch.object(self.core, 'open_media', side_effect=open_media)

    @contextlib.contextmanager
    def stalled_parent(self, send_headers: bool):
        """Media requests go, over the real cancellable connection, to a parent
        that accepts and then stalls (before or after its first 1000 bytes)."""
        stall = socket.socket()
        stall.bind(('127.0.0.1', 0))
        stall.listen()
        def serve():
            with contextlib.suppress(OSError):
                conn, _ = stall.accept()
                with conn:
                    conn.recv(4096)
                    if send_headers:
                        conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: audio/flac\r\n'
                                     b'Content-Length: 100000000\r\n\r\n' + b'f' * 1000)
                    time.sleep(20)
        threading.Thread(target=serve, daemon=True).start()
        stalled_cfg = bridge.replace(self.core.cfg, upstream_url=f'http://127.0.0.1:{stall.getsockname()[1]}')
        def open_media(*args):
            return bridge.Bridge.open_media(types.SimpleNamespace(cfg=stalled_cfg), *args)
        try:
            with patch.object(self.core, 'open_media', side_effect=open_media):
                yield
        finally:
            stall.close()

    def stop_and_time_release(self):
        started = time.monotonic()
        with patch.object(self.core, 'soap', return_value=b'<ok/>'):
            self.request(self.CONTROL + 'Stop', method='POST',
                         headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.wait_for(lambda: not self.core.peer_streams, 'relay still blocked on the parent', timeout=3)
        return time.monotonic() - started

    def open_stalled_reader(self, grant):
        """A renderer that reads the headers, then stops reading."""
        sock = socket.create_connection(('127.0.0.1', self.media.server_port), timeout=10)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.sendall(f'GET /media/{grant.token}/{grant.track_id}.{grant.extension} HTTP/1.1\r\n'
                     'Host: x\r\n\r\n'.encode())
        self.assertTrue(sock.recv(1024).startswith(b'HTTP/1.0 200'))
        self.wait_for(lambda: grant.token in self.core.streams, 'relay never registered')
        return sock

    def wait_for(self, condition, message, timeout=8.0):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail(message)
            time.sleep(0.05)

    def drained_to_eof(self, sock, timeout=8.0):
        sock.settimeout(timeout)
        try:
            while sock.recv(1 << 20):
                pass
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        return True

    def test_stop_cuts_an_already_open_stream(self):
        _, grant = self.cast_ok()
        with self.endless_stream():
            sock = self.open_stalled_reader(grant)
            with sock, patch.object(self.core, 'soap', return_value=b'<ok/>'):
                status, _, _ = self.request('/api/cast/control/uuid:EXN100-test/Stop', method='POST',
                                            headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
                self.assertEqual(status, 200)
                self.wait_for(lambda: not self.core.streams and not self.core.peer_streams,
                              'relay kept running after Stop')
                self.assertTrue(self.drained_to_eof(sock))

    def test_next_track_cuts_the_previous_stream(self):
        _, old = self.cast_ok()
        with self.endless_stream():
            sock = self.open_stalled_reader(old)
            with sock:
                _, new = self.cast_ok()
                self.wait_for(lambda: old.token not in self.core.streams, 'old track kept streaming')
                self.assertIn(new.token, self.core.grants)
                self.assertTrue(self.drained_to_eof(sock))

    def test_stalled_reader_released_then_resumes_with_range(self):
        _, grant = self.cast_ok()
        awake = []
        with self.endless_stream(), patch.object(bridge, 'MEDIA_STALL_TIMEOUT', 0.5), \
             patch.object(bridge, 'keep_awake', side_effect=awake.append):
            sock = self.open_stalled_reader(grant)
            with sock:
                self.wait_for(lambda: not self.core.streams and not self.core.peer_streams,
                              'stalled relay held its thread')
        self.assertEqual(awake, [True, False], 'keep-awake not released')
        # Resume after the pause: a fresh Range GET on the same, still valid URL.
        path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
        status, _, data = self.request(path, role='media', headers={'Range': 'bytes=4096-'})
        self.assertEqual((status, data), (206, AUDIO[4096:]))

    def test_open_streams_capped_per_renderer(self):
        _, grant = self.cast_ok()
        with self.endless_stream(), patch.object(bridge, 'MAX_MEDIA_STREAMS_PER_IP', 1):
            sock = self.open_stalled_reader(grant)
            with sock:
                path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
                self.assertEqual(self.request(path, role='media')[0], 503)
                self.core.retire_older_grants('127.0.0.1')
                self.wait_for(lambda: not self.core.peer_streams, 'cap slot not released')

    # ── Review 2 / P1-4: only authorized tailnet devices reach the parent ──

    def test_unauthorized_tailnet_peer_cannot_proxy(self):
        with patch.object(self.core, 'peer_allowed', return_value=False):
            for path in ('/', '/api/tracks/5', '/api/zones', '/bridge/health'):
                status, _, _ = self.request(path, headers={'X-API-Key': CREDENTIAL})
                self.assertEqual(status, 403, path)
        self.assertEqual(self.request('/api/tracks/5', headers={'X-API-Key': CREDENTIAL})[0], 200)

    # ── Review 3 / P1-A: identity headers are claims, not identity ──

    def test_forged_identity_headers_refused_without_verified_serve(self):
        with patch.object(self.core, 'serve_proxies_here', return_value=False):
            for header in ({'Tailscale-User-Login': 'edwyn@example.com'},
                           {'X-Forwarded-For': '100.80.1.10'}, {'Forwarded': 'for=100.80.1.10'}):
                status, _, _ = self.request('/api/tracks/5', headers={'X-API-Key': CREDENTIAL, **header})
                self.assertEqual(status, 403, header)

    def test_verified_serve_identity_is_stripped_before_the_parent(self):
        status_json = {'Self': {'UserID': 1}, 'User': {'1': {'LoginName': 'edwyn@example.com'}}}
        with patch.object(self.core, 'serve_proxies_here', return_value=True), \
             patch.object(self.core, 'tailnet_status', return_value=status_json):
            status, _, _ = self.request('/api/tracks/5', headers={
                'X-API-Key': CREDENTIAL, 'Tailscale-User-Login': 'edwyn@example.com',
                'Tailscale-User-Name': 'Edwyn', 'X-Forwarded-For': '100.80.1.10'})
            self.assertEqual(status, 200)
            forwarded = {k for k in Parent.track_headers if k.startswith('tailscale-') or 'forward' in k}
            self.assertEqual(forwarded, set())
            status, _, _ = self.request('/api/tracks/5', headers={
                'X-API-Key': CREDENTIAL, 'Tailscale-User-Login': 'mum@example.com'})
            self.assertEqual(status, 403)

    # ── Review 3 / P1-B: an older Play/Pause/Stop never undoes a newer one ──

    def first_request_held_in_admin_check(self, first, second, soap_result=b'<ok/>'):
        """Send `first`, hold it inside its admin check, run `second` to the
        end, release `first`. Returns (SOAP actions in order, both answers)."""
        actions, entered, release = [], threading.Event(), threading.Event()
        real_auth, calls = self.core.authorize_control, []
        def held_auth(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(10)
            return real_auth(*a, **k)
        def soap(device, action, values=None):
            actions.append(action)
            return soap_result
        answers = {}
        def send(name, path, body=None):
            answers[name] = self.request(path, method='POST', body=body,
                                         headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        with patch.object(self.core, 'authorize_control', side_effect=held_auth), \
             patch.object(self.core, 'soap', side_effect=soap), \
             patch.object(self.core, 'transport', return_value='PLAYING'):
            t = threading.Thread(target=send, args=('first', *first))
            t.start()
            self.assertTrue(entered.wait(5), 'first request never reached its admin check')
            send('second', *second)
            release.set()
            t.join(10)
        return actions, {k: (v[0], json.loads(v[2])) for k, v in answers.items()}

    CONTROL = '/api/cast/control/uuid:EXN100-test/'

    def loaded_session(self):
        """What a cast leaves: Velvet sends Play/Pause only for a track it cast."""
        self.core.sessions['uuid:EXN100-test'] = bridge.QueueSession(
            ids=(5,), index=0, repeat='none', credential=CREDENTIAL, token='loaded', track_id=5)

    def test_older_pause_does_not_undo_newer_play(self):
        self.loaded_session()
        actions, answers = self.first_request_held_in_admin_check(
            (self.CONTROL + 'Pause',), (self.CONTROL + 'Play',))
        self.assertEqual(actions, ['Play'])  # the renderer ends up playing
        self.assertEqual(answers['first'], (200, {'success': True, 'superseded': True}))
        self.assertEqual(answers['second'], (200, {'success': True}))

    def test_older_play_does_not_undo_newer_pause(self):
        self.loaded_session()
        actions, _ = self.first_request_held_in_admin_check(
            (self.CONTROL + 'Play',), (self.CONTROL + 'Pause',))
        self.assertEqual(actions, ['Pause'])

    def test_older_play_does_not_restart_after_newer_stop(self):
        _, grant = self.cast_ok()
        actions, _ = self.first_request_held_in_admin_check(
            (self.CONTROL + 'Play',), (self.CONTROL + 'Stop',))
        self.assertEqual(actions, ['Stop'])
        self.assertNotIn(grant.token, self.core.grants)

    def test_older_stop_does_not_kill_newer_cast(self):
        actions, answers = self.first_request_held_in_admin_check(
            (self.CONTROL + 'Stop',), ('/api/cast/uuid:EXN100-test', {'track_id': 5}))
        self.assertNotIn('Stop', actions)
        self.assertEqual(actions[-1], 'Play')
        self.assertTrue(answers['second'][1]['success'])
        self.assertEqual(len(self.core.grants), 1, 'the new track lost its ticket')

    def test_controls_in_order_all_run(self):
        # No race: Pause then Play, then Stop, each reaches the renderer.
        actions = []
        self.loaded_session()
        with patch.object(self.core, 'soap', side_effect=lambda d, a, v=None: actions.append(a) or b'<ok/>'):
            for action in ('Pause', 'Play', 'Stop'):
                status, _, data = self.request(self.CONTROL + action, method='POST',
                                               headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
                self.assertEqual((status, json.loads(data)), (200, {'success': True}))
        self.assertEqual(actions, ['Pause', 'Play', 'Stop'])

    # ── Existing behaviour the ordering must keep ──

    def test_pause_then_resume_keeps_the_stream_usable(self):
        _, grant = self.cast_ok()
        with patch.object(self.core, 'soap', return_value=b'<ok/>'):
            for action in ('Pause', 'Play'):
                status, _, data = self.request(self.CONTROL + action, method='POST',
                                               headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
                self.assertEqual(json.loads(data), {'success': True})
        path = f'/media/{grant.token}/{grant.track_id}.{grant.extension}'
        status, _, data = self.request(path, role='media', headers={'Range': 'bytes=100-'})
        self.assertEqual((status, data), (206, AUDIO[100:]))

    def test_status_never_hands_the_ui_a_natural_end(self):
        # Velvet casts its next track itself when a confirmed cast reports
        # STOPPED at position >= duration - 2. The bridge plays the queue on
        # (monitor_once), so a stopped renderer is reported at 0: a page
        # never casts over the bridge's advance or another device's Stop.
        _, grant = self.cast_ok()
        with patch.object(self.core, 'transport', return_value='STOPPED'),              patch.object(self.core, 'position', return_value=(209.5, 210.0, grant.url(self.core.cfg))):
            status, _, data = self.request('/api/cast/status/uuid:EXN100-test',
                                           headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(json.loads(data), {'state': 'STOPPED', 'position': 0.0, 'duration': 210.0,
                                            'track_id': 5, 'is_playing': False})
        with patch.object(self.core, 'transport', return_value='PAUSED_PLAYBACK'),              patch.object(self.core, 'position', return_value=(120.0, 210.0, grant.url(self.core.cfg))):
            _, _, data = self.request('/api/cast/status/uuid:EXN100-test', headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(json.loads(data)['position'], 120.0)  # only a stop hides it

    # ── Review 3 / P2: Stop while the relay waits on a stalled parent ──

    def test_stop_cuts_a_relay_blocked_reading_the_parent(self):
        _, grant = self.cast_ok()
        with self.stalled_parent(send_headers=True):
            sock = socket.create_connection(('127.0.0.1', self.media.server_port), timeout=10)
            with sock:
                sock.sendall(f'GET /media/{grant.token}/{grant.track_id}.{grant.extension} HTTP/1.1\r\n'
                             'Host: x\r\n\r\n'.encode())
                received = b''
                while b'f' * 1000 not in received:
                    received += sock.recv(4096)
                time.sleep(0.3)  # now blocked inside upstream.read1()
                self.assertLess(self.stop_and_time_release(), 3)  # not the 60 s read timeout
                self.assertTrue(self.drained_to_eof(sock, timeout=3))

    def test_stop_cuts_a_relay_waiting_for_parent_headers(self):
        # Review 4 / P2: the parent link hangs before answering at all.
        _, grant = self.cast_ok()
        with self.stalled_parent(send_headers=False):
            sock = socket.create_connection(('127.0.0.1', self.media.server_port), timeout=10)
            with sock:
                sock.sendall(f'GET /media/{grant.token}/{grant.track_id}.{grant.extension} HTTP/1.1\r\n'
                             'Host: x\r\n\r\n'.encode())
                self.wait_for(lambda: grant.token in self.core.streams, 'relay never registered')
                time.sleep(0.3)  # now waiting for the parent's response headers
                self.assertLess(self.stop_and_time_release(), 3)  # not MEDIA_OPEN_TIMEOUT
                self.assertTrue(self.drained_to_eof(sock, timeout=3))

    # ── Review 4 / P1: the bridge plays the queue on with the phone locked ──

    def fake_renderer(self):
        """A renderer state machine; the monitor is then driven tick by tick
        with no client requests at all (Safari suspended)."""
        class FakeRenderer:
            def __init__(self):
                self.uri, self.state, self.position, self.duration = '', 'NO_MEDIA_PRESENT', 0.0, 0.0
                self.actions, self.loaded = [], []
            def soap(self, device, action, values=None):
                self.actions.append(action)
                if action == 'SetAVTransportURI':
                    self.loaded.append(values['CurrentURI'])
                    self.uri, self.state, self.position = values['CurrentURI'], 'STOPPED', 0.0
                elif action == 'Play':
                    self.state = 'PLAYING'
                elif action == 'Pause':
                    self.state = 'PAUSED_PLAYBACK'
                elif action == 'Stop':
                    self.state = 'STOPPED'
                return b'<ok/>'
            def transport(self, device, timeout=None):
                return self.state
            def read_position(self, device, timeout=None):
                return self.position, self.duration, self.uri
            def playing(self, position, duration):
                self.state, self.position, self.duration = 'PLAYING', position, duration
            def finish(self, *, report_zero=False):
                # Some renderers keep RelTime at the end on STOPPED, some reset it.
                self.state, self.position = 'STOPPED', 0.0 if report_zero else self.duration
            def tracks(self):
                """Track ids the renderer was handed, in order."""
                return [int(bridge.MEDIA_RE.fullmatch(bridge.urlparse(u).path).group(2)) for u in self.loaded]
        renderer = FakeRenderer()
        patches = patch.multiple(self.core, soap=renderer.soap, transport=renderer.transport,
                                 position=renderer.read_position)
        patches.start()
        self.addCleanup(patches.stop)
        return renderer

    UDN = 'uuid:EXN100-test'

    def cast_queue(self, ids, index, repeat='none', **extra):
        status, _, data = self.request(f'/api/cast/{self.UDN}', method='POST', body={
            'track_id': ids[index], 'bridge_queue': {'ids': ids, 'index': index, 'repeat': repeat}, **extra},
            headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual(status, 200, data)
        return json.loads(data)

    def play_through(self, renderer, position=100, duration=200, **finish):
        """The renderer plays the current track to its end, unattended."""
        renderer.playing(position, duration)
        self.core.monitor_once(self.UDN)
        renderer.finish(**finish)
        self.core.monitor_once(self.UDN)

    def test_locked_phone_album_plays_on_exactly_once_per_track(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        self.play_through(renderer, duration=210)
        self.assertEqual(renderer.tracks(), [5, 6])
        # More ticks, the renderer still on track 6 (and a stale STOPPED read
        # right after the advance): never a second advance.
        renderer.state = 'STOPPED'
        for _ in range(3):
            self.core.monitor_once(self.UDN)
        renderer.state = 'PLAYING'
        self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5, 6])
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 6, 7])
        self.assertEqual(renderer.state, 'PLAYING')
        # End of the queue: nothing more, and the PC may sleep again.
        self.assertTrue(self.core.wants_awake())
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 6, 7])
        self.assertFalse(self.core.wants_awake())

    def test_ui_catching_up_does_not_restart_the_advanced_track(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        self.play_through(renderer)
        actions = len(renderer.actions)
        # Safari wakes: queue_sync.js (or Velvet's own late auto-next) casts 6.
        result = self.cast_queue([5, 6, 7], 1)
        self.assertEqual((result['success'], result['transport'], result.get('absorbed')), (True, 'playing', True))
        self.assertEqual(len(renderer.actions), actions, 'the track was sent to the renderer again')
        self.assertEqual(renderer.tracks(), [5, 6])
        # A deliberate replay of the same track afterwards does restart it.
        self.cast_queue([5, 6, 7], 1)
        self.assertEqual(renderer.tracks(), [5, 6, 6])

    def test_ui_auto_next_first_then_bridge_does_not_double_advance(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(100, 200)
        self.core.monitor_once(self.UDN)
        renderer.finish()
        self.cast_queue([5, 6, 7], 1)  # the awake page advanced before the monitor looked
        self.core.monitor_once(self.UDN)
        self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5, 6])

    def test_monitor_waits_for_a_users_cast_in_flight(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(100, 200)
        self.core.monitor_once(self.UDN)
        renderer.finish()
        with self.core.in_flight(self.UDN):  # e.g. the user picked track 7, still in its admin check
            self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5])
        self.cast_queue([5, 6, 7], 2)
        self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5, 7])

    def test_stop_ends_the_queue(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(100, 200)
        self.core.monitor_once(self.UDN)
        self.request(f'/api/cast/control/{self.UDN}/Stop', method='POST',
                     headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        renderer.finish()
        self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5])
        self.assertNotIn(self.UDN, self.core.sessions)
        self.assertFalse(self.core.wants_awake())

    def test_long_pause_keeps_awake_and_mid_track_stop_does_not_advance(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(50, 200)
        self.core.monitor_once(self.UDN)
        renderer.state = 'PAUSED_PLAYBACK'  # paused for half an hour
        for _ in range(3):
            self.core.monitor_once(self.UDN)
        self.assertTrue(self.core.wants_awake())
        renderer.state = 'PLAYING'
        self.core.monitor_once(self.UDN)
        renderer.state = 'STOPPED'  # stopped on the EXN100 itself, mid-track
        self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5])
        self.assertFalse(self.core.wants_awake())

    def test_lagging_track_uri_after_an_advance_is_not_counted(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        self.play_through(renderer)
        old_uri, new_uri = renderer.loaded[0], renderer.loaded[1]
        # One read still describes the old stream near its end...
        renderer.uri, renderer.state, renderer.position, renderer.duration = old_uri, 'PLAYING', 199, 200
        self.core.monitor_once(self.UDN)
        # ...then track 6 plays and is stopped on the EXN100 at 0:30.
        renderer.uri = new_uri
        renderer.playing(30, 200)
        self.core.monitor_once(self.UDN)
        renderer.state = 'STOPPED'
        self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5, 6], 'the old position made a mid-track stop look finished')

    def test_manual_jump_then_repeat_all_wraps(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 2, repeat='all')  # the user jumped to the last track
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [7, 5])

    def test_hour_long_track_with_position_reset_on_stop(self):
        renderer = self.fake_renderer()
        self.cast_queue([8, 5], 0)
        self.play_through(renderer, position=3998.5, duration=4000, report_zero=True)
        self.assertEqual(renderer.tracks(), [8, 5])

    def test_another_source_on_the_renderer_ends_the_session(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6], 0)
        renderer.playing(10, 200)
        self.core.monitor_once(self.UDN)
        renderer.uri = 'http://192.168.1.60/radio.mp3'  # StreamMagic took over
        self.core.monitor_once(self.UDN)
        self.assertNotIn(self.UDN, self.core.sessions)

    def post_queue(self, body):
        status, _, data = self.request(f'/api/bridge/queue/{self.UDN}', method='POST', body=body,
                                       headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        return status, json.loads(data)

    def test_queue_updates_and_limits(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        rev = self.core.sessions[self.UDN].revision
        post = lambda body: self.post_queue({'expected_revision': rev, **body})
        status, answer = post({'track_id': 5, 'ids': [5, 7], 'index': 0})
        self.assertEqual((status, answer['success']), (200, True))
        self.assertGreater(answer['revision'], rev)
        self.assertEqual(post({'track_id': 6, 'ids': [6, 7], 'index': 0, 'expected_revision': answer['revision']}),
                         (409, {'success': False, 'conflict': True, 'revision': answer['revision']}))  # another track
        self.assertEqual(post({'track_id': 5, 'ids': [5] * 5001, 'index': 0})[0], 422)
        self.assertEqual(post({'track_id': 5, 'ids': [5, 'x'], 'index': 0})[0], 422)
        self.assertEqual(post({'track_id': 5, 'ids': [5, 6], 'index': 1})[0], 422)  # index is not track 5
        self.assertEqual(self.post_queue({'track_id': 5, 'ids': [5, 6], 'index': 0})[0], 422)  # no revision
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 7])  # the edited queue, not the original

    # ── Review 6 / P1-3: a page's stale edit never overwrites a newer one ──

    def test_stale_queue_edit_is_refused_and_changes_nothing(self):
        self.fake_renderer()
        self.cast_queue([5, 6, 7, 8], 0)
        seen = self.core.sessions[self.UDN].revision
        # Phone A removes track 7 (the current song stays 5)...
        status, answer = self.post_queue({'track_id': 5, 'ids': [5, 6, 8], 'index': 0, 'expected_revision': seen})
        self.assertEqual(status, 200)
        # ...then phone B, which has not seen that, toggles repeat with its old queue.
        status, refused = self.post_queue({'track_id': 5, 'ids': [5, 6, 7, 8], 'index': 0, 'repeat': 'all',
                                           'expected_revision': seen})
        self.assertEqual((status, refused), (409, {'success': False, 'conflict': True, 'revision': answer['revision']}))
        session = self.core.sessions[self.UDN]
        self.assertEqual((session.ids, session.repeat), ((5, 6, 8), 'none'))
        # B merges and retries against the revision it now knows.
        status, _ = self.post_queue({'track_id': 5, 'ids': [5, 6, 8], 'index': 0, 'repeat': 'all',
                                     'expected_revision': answer['revision']})
        self.assertEqual(status, 200)
        self.assertEqual((session.ids, session.repeat), ((5, 6, 8), 'all'))

    def test_ui_catch_up_cast_keeps_the_sessions_newer_queue(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7, 8], 0)
        self.play_through(renderer)  # the bridge advanced to 6 on its own
        session = self.core.sessions[self.UDN]
        self.post_queue({'track_id': 6, 'ids': [5, 6, 8], 'index': 1, 'expected_revision': session.revision})
        # A page that slept through both now catches up with its old queue.
        result = self.cast_queue([5, 6, 7, 8], 1)
        self.assertTrue(result.get('absorbed'))
        self.assertEqual(self.core.sessions[self.UDN].ids, (5, 6, 8))

    # ── Review 6 / P1-4: queue edits during an auto-advance ──

    def advance_with_edit_during_lookup(self, ids, edit, *, held='/api/tracks/6'):
        """Track 5 ends; while the advance looks up `held`, `edit` (a queue
        update of the still-current track 5) is posted and answered; then the
        lookup returns. Returns the fake renderer and the edit's answer."""
        renderer = self.fake_renderer()
        self.cast_queue(ids, 0)
        renderer.playing(100, 200)
        self.core.monitor_once(self.UDN)
        renderer.finish()
        entered, release = threading.Event(), threading.Event()
        real_json = self.core.upstream_json
        def upstream_json(path, *args, **kwargs):
            if path == held and not release.is_set():
                entered.set()
                release.wait(10)
            return real_json(path, *args, **kwargs)
        errors = []
        def monitor():
            try:
                self.core.monitor_once(self.UDN)
            except Exception as exc:  # what used to escape the monitor
                errors.append(exc)
        with patch.object(self.core, 'upstream_json', side_effect=upstream_json):
            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(5), 'the advance never looked the next track up')
            revision = self.core.sessions[self.UDN].revision
            answer = self.post_queue({'track_id': 5, 'expected_revision': revision, **edit})
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        return renderer, answer

    def test_next_track_removed_during_its_lookup_is_not_played(self):
        renderer, answer = self.advance_with_edit_during_lookup([5, 6, 7], {'ids': [5, 7], 'index': 0})
        self.assertEqual(answer[0], 200)
        self.assertEqual(renderer.tracks(), [5, 7])
        self.assertEqual(self.core.sessions[self.UDN].ids, (5, 7))  # the edit stands

    def test_next_track_moved_during_its_lookup_plays_the_new_next(self):
        renderer, _ = self.advance_with_edit_during_lookup([5, 6, 7, 8], {'ids': [5, 8, 7, 6], 'index': 0})
        self.assertEqual(renderer.tracks(), [5, 8])
        self.assertEqual(self.core.sessions[self.UDN].ids, (5, 8, 7, 6))

    def test_queue_cut_to_the_current_track_during_the_lookup_ends_cleanly(self):
        renderer, _ = self.advance_with_edit_during_lookup([5, 6, 7], {'ids': [5], 'index': 0})
        self.assertEqual(renderer.tracks(), [5])
        session = self.core.sessions[self.UDN]
        self.assertEqual((session.ids, session.idle), ((5,), True))
        self.assertFalse(self.core.wants_awake())

    def test_repeat_change_during_the_lookup_is_kept(self):
        renderer, _ = self.advance_with_edit_during_lookup([5, 6], {'ids': [5, 6], 'index': 0, 'repeat': 'all'})
        self.assertEqual(renderer.tracks(), [5, 6])
        self.assertEqual(self.core.sessions[self.UDN].repeat, 'all')
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 6, 5])  # wraps: the edit was not dropped

    def test_edit_while_the_next_track_is_being_loaded_waits_then_conflicts(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(100, 200)
        self.core.monitor_once(self.UDN)
        renderer.finish()
        loading, release = threading.Event(), threading.Event()
        real_soap = renderer.soap
        def soap(device, action, values=None):
            if action == 'SetAVTransportURI' and not release.is_set():
                loading.set()
                release.wait(10)
            return real_soap(device, action, values)
        self.core.soap = soap
        thread = threading.Thread(target=self.core.monitor_once, args=(self.UDN,), daemon=True)
        thread.start()
        self.assertTrue(loading.wait(5))
        revision = self.core.sessions[self.UDN].revision
        answers = []
        poster = threading.Thread(target=lambda: answers.append(self.post_queue(
            {'track_id': 5, 'ids': [5, 7], 'index': 0, 'expected_revision': revision})), daemon=True)
        poster.start()
        poster.join(0.5)
        self.assertTrue(poster.is_alive(), 'the edit landed in the middle of the advance')
        release.set()
        thread.join(10)
        poster.join(10)
        # The renderer moved on to 6 first: the edit (of track 5) is refused, so
        # the page merges it with the new session instead of losing it silently.
        self.assertEqual(renderer.tracks(), [5, 6])
        self.assertEqual(answers[0][0], 409)
        self.assertEqual(self.core.sessions[self.UDN].ids, (5, 6, 7))

    def test_unexpected_error_in_an_advance_does_not_strand_the_queue(self):
        clock = [1000.0]
        self.core.clock = lambda: clock[0]
        self.addCleanup(setattr, self.core, 'clock', time.monotonic)
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        real_play = self.core._play
        calls = [0]
        def play(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('simulated bug')
            return real_play(*args, **kwargs)
        with patch.object(self.core, '_play', side_effect=play):
            renderer.playing(100, 200)
            self.core.monitor_once(self.UDN)
            renderer.finish()
            with self.assertRaises(RuntimeError):
                self.core.monitor_once(self.UDN)
            clock[0] += 3
            self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5, 6])

    def test_cast_with_malformed_queue_still_plays_alone(self):
        renderer = self.fake_renderer()
        result = self.cast_queue([5, 6], 1, bridge_queue={'ids': 'all of them'})
        self.assertTrue(result['success'])
        self.assertEqual(self.core.sessions[self.UDN].ids, (6,))
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [6])  # nothing known to follow it

    def test_session_endpoint_reports_bridge_advances(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        self.play_through(renderer)
        status, _, data = self.request(f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
        info = json.loads(data)
        self.assertEqual((status, info['active'], info['track_id'], info['queue_index'], info['queue_length']),
                         (200, True, 6, 1, 3))
        self.assertGreater(info['advance_seq'], 0)

    def test_velvet_page_gets_the_queue_script(self):
        status, hdr, page = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(bridge.QUEUE_SYNC_TAG + b'</body>', page)
        self.assertEqual(int(hdr['Content-Length']), len(page))
        status, hdr, script = self.request('/bridge/queue-sync.js')
        self.assertEqual((status, hdr['Content-Type']), (200, 'text/javascript; charset=utf-8'))
        self.assertIn(b'bridge_queue', script)
        _, _, data = self.request('/api/tracks/5', headers={'X-API-Key': CREDENTIAL})
        self.assertNotIn(b'queue-sync', data)  # only the page itself

    # ── Review 5 / P1-A: a user's command is never beaten by the auto-advance ──

    def user_command_in_the_monitor_gap(self, path, body=None):
        """Track 5 ends; between the monitor's decision to advance and its
        arrival number, the user sends `path`, which then waits in its admin
        check until the monitor has finished. Returns the fake renderer."""
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(100, 200)
        self.core.monitor_once(self.UDN)
        renderer.finish()
        entered, release = threading.Event(), threading.Event()
        real_auth, real_arrive = self.core.authorize_control, self.core.arrive
        def held_auth(*args, **kwargs):
            entered.set()
            release.wait(10)
            return real_auth(*args, **kwargs)
        user, first = [], [True]
        def send():
            user.append(self.request(path, method='POST', body=body,
                                     headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE}))
        def arrive(udn):
            if first[0]:  # the monitor's arrival: let the user's command in first
                first[0] = False
                threading.Thread(target=send, daemon=True).start()
                # 0.1.4 took this number after releasing the lock, so the user's
                # command arrived (and was numbered) here. Now the monitor still
                # holds the lock: the command cannot arrive until it is done.
                entered.wait(1)
            return real_arrive(udn)
        with patch.object(self.core, 'authorize_control', side_effect=held_auth), \
             patch.object(self.core, 'arrive', side_effect=arrive):
            self.core.monitor_once(self.UDN)
            self.assertTrue(entered.wait(5), 'the user command never reached its admin check')
            release.set()
            self.wait_for(lambda: user, 'the user command never finished')
        self.assertEqual(user[0][0], 200, user[0][2])
        return renderer, json.loads(user[0][2])

    def test_stop_in_the_monitor_gap_wins(self):
        renderer, answer = self.user_command_in_the_monitor_gap(f'/api/cast/control/{self.UDN}/Stop')
        self.assertEqual(answer, {'success': True})  # not {'superseded': True}
        self.assertEqual(renderer.actions[-1], 'Stop')
        self.assertEqual(renderer.state, 'STOPPED')
        self.assertNotIn(self.UDN, self.core.sessions)

    def test_new_track_in_the_monitor_gap_wins(self):
        renderer, answer = self.user_command_in_the_monitor_gap(
            f'/api/cast/{self.UDN}', {'track_id': 7, 'bridge_queue': {'ids': [5, 6, 7], 'index': 2}})
        self.assertTrue(answer['success'])
        self.assertNotIn('superseded', answer)
        self.assertEqual(renderer.tracks()[-1], 7)
        self.assertEqual(self.core.sessions[self.UDN].track_id, 7)

    # ── Review 5 / P2-1: a Stop pressed near the end is not the end ──

    def stop_near_the_end(self, *, stopped_after: float, position_on_stop: float):
        clock = [1000.0]
        self.core.clock = lambda: clock[0]
        self.addCleanup(setattr, self.core, 'clock', time.monotonic)
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(197, 200)  # last read: 3 s left
        self.core.monitor_once(self.UDN)
        clock[0] += stopped_after
        renderer.state, renderer.position = 'STOPPED', position_on_stop
        self.core.monitor_once(self.UDN)
        return renderer

    def test_stop_on_the_exn100_three_seconds_before_the_end_does_not_advance(self):
        for position in (197, 0):  # renderers keep RelTime on Stop, or reset it
            self.setUp()
            renderer = self.stop_near_the_end(stopped_after=1, position_on_stop=position)
            self.assertEqual(renderer.tracks(), [5], position)
            self.assertFalse(self.core.wants_awake())

    def test_natural_end_still_advances(self):
        # Reset RelTime, but the rest of the track has had time to play.
        renderer = self.stop_near_the_end(stopped_after=3, position_on_stop=0)
        self.assertEqual(renderer.tracks(), [5, 6])
        self.setUp()
        # The renderer itself reports the end, however soon it is seen.
        renderer = self.stop_near_the_end(stopped_after=0.2, position_on_stop=200)
        self.assertEqual(renderer.tracks(), [5, 6])

    # ── Review 5 / P2-2: an unplayable track is skipped, not the album ──

    def test_missing_middle_track_is_skipped_and_reported(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 99, 6], 0)  # 99: gone from the parent library (404)
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 6])
        status, _, data = self.request(f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
        info = json.loads(data)
        self.assertEqual([s['track_id'] for s in info['skipped']], [99])
        self.assertIsNone(info['error'])

    def test_unsupported_and_refused_tracks_are_skipped(self):
        renderer = self.fake_renderer()
        real_soap = renderer.soap
        def soap(device, action, values=None):
            if action == 'SetAVTransportURI' and values['CurrentURI'].endswith('/7.flac'):
                return None  # the EXN100 refuses this one
            return real_soap(device, action, values)
        self.core.soap = soap
        self.cast_queue([5, 98, 7, 6], 0)  # 98: DSF, which the bridge does not stream
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 6])
        self.assertEqual([t for t, _ in self.core.sessions[self.UDN].skipped], [98, 7])

    def test_nothing_playable_left_ends_with_an_error(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 99, 98], 0, repeat='all')  # wraps back to 5 once, never loops
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 5])
        self.setUp()
        renderer = self.fake_renderer()
        self.cast_queue([5, 99, 98], 0)
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5])
        session = self.core.sessions[self.UDN]
        self.assertEqual((session.idle, session.error), (True, 'No playable track left in the queue'))

    def test_whole_queue_gone_with_repeat_all_stops_after_one_round(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 99, 98], 0, repeat='all')
        real_json = self.core.upstream_json
        lookups = []
        def upstream_json(path, *args, **kwargs):
            lookups.append(path)
            if path == '/api/tracks/5':  # removed from the library while it played
                raise bridge.HTTPFailure(404, 'Not found in the parent Velvet library')
            return real_json(path, *args, **kwargs)
        with patch.object(self.core, 'upstream_json', side_effect=upstream_json):
            self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5])
        self.assertEqual([p for p in lookups if p.startswith('/api/tracks/')],
                         ['/api/tracks/99', '/api/tracks/98', '/api/tracks/5'])  # once round, no loop
        self.assertEqual(self.core.sessions[self.UDN].error, 'No playable track left in the queue')

    def test_unreachable_parent_retries_then_gives_up_without_skipping(self):
        clock = [1000.0]
        self.core.clock = lambda: clock[0]
        self.addCleanup(setattr, self.core, 'clock', time.monotonic)
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        real_json = self.core.upstream_json
        down = [True]
        def upstream_json(path, *args, **kwargs):
            if down[0] and path == '/api/tracks/6':
                raise bridge.HTTPFailure(502, 'Parent Velvet is unavailable over Tailscale')
            return real_json(path, *args, **kwargs)
        with patch.object(self.core, 'upstream_json', side_effect=upstream_json):
            self.play_through(renderer)  # first try fails
            self.core.monitor_once(self.UDN)  # too soon to retry
            self.assertEqual(self.core.sessions[self.UDN].failures, 1)
            clock[0] += 3
            down[0] = False  # Tailscale is back
            self.core.monitor_once(self.UDN)
        self.assertEqual(renderer.tracks(), [5, 6])  # track 6 was not skipped
        self.setUp()
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        with patch.object(self.core, 'upstream_json', side_effect=upstream_json):
            down[0] = True
            self.play_through(renderer)
            for _ in range(bridge.MAX_ADVANCE_RETRIES):
                clock[0] += 60
                self.core.monitor_once(self.UDN)
        session = self.core.sessions[self.UDN]
        self.assertEqual(renderer.tracks(), [5])
        self.assertTrue(session.idle)
        self.assertIn('unavailable', session.error)

    # ── Review 5 / P1-B: what a reloaded page reattaches to ──

    def test_session_discovery_for_a_reloaded_page(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        self.play_through(renderer)
        self.play_through(renderer)  # two unattended advances
        status, _, data = self.request('/api/bridge/session', headers={'X-API-Key': CREDENTIAL})
        info = json.loads(data)
        self.assertEqual((status, info['active'], info['udn'], info['track_id'], info['queue_index'], info['ids']),
                         (200, True, self.UDN, 7, 2, [5, 6, 7]))
        self.assertEqual((info['name'], info['state']), ('EXN100 Living Room', 'PLAYING'))
        loads = len(renderer.loaded)
        self.request(f'/api/cast/control/{self.UDN}/Stop', method='POST',
                     headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        _, _, data = self.request('/api/bridge/session', headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(json.loads(data), {'active': False})
        self.assertEqual(len(renderer.loaded), loads)  # asking never loads anything

    def test_monitor_thread_holds_keep_awake_for_the_session(self):
        renderer = self.fake_renderer()
        self.core.cfg = bridge.replace(self.core.cfg, stay_awake=False)  # only while playing
        self.addCleanup(setattr, self.core, 'cfg', bridge.replace(self.core.cfg, stay_awake=True))
        calls, stop = [], threading.Event()
        with patch.object(bridge, 'MONITOR_INTERVAL', 0.05), \
             patch.object(bridge, 'keep_awake', side_effect=calls.append):
            thread = threading.Thread(target=self.core.run_monitor, args=(stop,), daemon=True)
            thread.start()
            try:
                self.cast_queue([5, 6], 0)
                renderer.playing(10, 200)
                self.wait_for(lambda: True in calls, 'keep-awake never requested')
                self.request(f'/api/cast/control/{self.UDN}/Stop', method='POST',
                             headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
                self.wait_for(lambda: calls[-1] is False, 'keep-awake never released')
            finally:
                stop.set()
                thread.join(5)


    def test_stay_awake_holds_windows_awake_before_any_playback(self):
        # Review 7 / P2: the first remote cast needs this PC already awake.
        self.assertTrue(self.core.cfg.stay_awake)
        calls, stop = [], threading.Event()
        with patch.object(bridge, 'MONITOR_INTERVAL', 0.05), \
             patch.object(bridge, 'keep_awake', side_effect=calls.append):
            thread = threading.Thread(target=self.core.run_monitor, args=(stop,), daemon=True)
            thread.start()
            try:
                self.wait_for(lambda: True in calls, 'keep-awake not requested with no session')
                self.assertEqual(self.core.sessions, {})
            finally:
                stop.set()
                thread.join(5)
        self.assertEqual(calls[-1], False)  # released when the bridge stops

    # ── Review 7 / P1: a Stop from one device is final for every device ──

    def stop_near_the_end_from_another_device(self):
        """Phone B follows track 5 at 198/200 s; phone A stops the EXN100,
        which keeps RelTime on STOPPED (TrackURI still the revoked stream)."""
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(198, 200)
        self.core.monitor_once(self.UDN)
        status, _, data = self.request(f'/api/cast/control/{self.UDN}/Stop', method='POST',
                                       headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual((status, json.loads(data)), (200, {'success': True}))
        renderer.state, renderer.position = 'STOPPED', 198.0
        return renderer

    def test_stop_near_the_end_gives_other_pages_no_next_track_and_no_session(self):
        renderer = self.stop_near_the_end_from_another_device()
        actions = len(renderer.actions)
        for _ in range(3):  # B's Velvet polls, the monitor ticks
            self.core.monitor_once(self.UDN)
            status, _, data = self.request(f'/api/cast/status/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
            self.assertEqual(status, 200)
            answer = json.loads(data)
            # Velvet's auto-next needs position >= duration - 2: never offered.
            self.assertEqual((answer['state'], answer['position'], answer['track_id']), ('STOPPED', 0.0, None))
        _, _, data = self.request(f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(json.loads(data), {'active': False})  # queue_sync.js releases B
        self.assertEqual(renderer.actions[actions:], [], 'something was sent to the renderer after the Stop')
        self.assertEqual(renderer.tracks(), [5])

    def test_play_after_another_devices_stop_restarts_nothing(self):
        renderer = self.stop_near_the_end_from_another_device()
        actions = len(renderer.actions)
        status, _, data = self.request(f'/api/cast/control/{self.UDN}/Play', method='POST',
                                       headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        answer = json.loads(data)
        self.assertEqual((status, answer['success']), (200, False))
        self.assertIn('choose a track', answer['error'])
        self.assertEqual(renderer.actions[actions:], [])
        # A track chosen afterwards plays as usual.
        self.cast_queue([5, 6, 7], 1)
        self.assertEqual(renderer.tracks(), [5, 6])

    def test_streammagic_taking_the_exn100_mid_song_ends_the_session_for_every_page(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(60, 200)
        self.core.monitor_once(self.UDN)
        actions = len(renderer.actions)
        renderer.uri, renderer.state, renderer.position, renderer.duration = (
            'http://192.168.1.60:9000/disk/song.flac', 'PLAYING', 5.0, 180.0)
        self.core.monitor_once(self.UDN)
        self.assertNotIn(self.UDN, self.core.sessions)
        renderer.state, renderer.position = 'STOPPED', 180.0  # its song ends, RelTime kept
        for _ in range(2):
            self.core.monitor_once(self.UDN)
            _, _, data = self.request(f'/api/cast/status/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
            self.assertEqual(json.loads(data)['position'], 0.0)
        _, _, data = self.request(f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(json.loads(data), {'active': False})
        self.assertEqual(renderer.actions[actions:], [])

    # ── Review 7 / P2: a session read never outlives a Stop or a new cast ──

    def session_read_during(self, change):
        """GET /api/bridge/session while its renderer read is held, `change`
        happening meanwhile. Returns (status, answer)."""
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        renderer.playing(30, 200)
        with self.core.lock:
            self.core.snapshots.clear()  # no fresh monitor read: the lookup asks the renderer
        entered, release = threading.Event(), threading.Event()
        real_transport = renderer.transport
        def transport(device, timeout=None):
            if not release.is_set():
                entered.set()
                release.wait(10)
            return real_transport(device)
        self.core.transport = transport
        answers = []
        reader = threading.Thread(target=lambda: answers.append(self.request(
            f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})), daemon=True)
        reader.start()
        self.assertTrue(entered.wait(5))
        change()
        release.set()
        reader.join(10)
        status, _, data = answers[0]
        return status, json.loads(data)

    def test_session_read_across_a_stop_reports_no_session(self):
        def stop():
            threading.Thread(target=self.request, args=(f'/api/cast/control/{self.UDN}/Stop',),
                             kwargs={'method': 'POST', 'headers': {'X-API-Key': CREDENTIAL, 'Cookie': COOKIE}},
                             daemon=True).start()
            self.wait_for(lambda: self.UDN not in self.core.sessions, 'Stop never ended the session')
        self.assertEqual(self.session_read_during(stop), (200, {'active': False}))

    def test_session_read_across_a_new_cast_reports_the_new_track(self):
        def cast():
            threading.Thread(target=self.cast_queue, args=([5, 6, 7], 2), daemon=True).start()
            self.wait_for(lambda: self.UDN in self.core.sessions and self.core.sessions[self.UDN].track_id == 7,
                          'the new cast never started')
        status, answer = self.session_read_during(cast)
        self.assertEqual((status, answer['active'], answer['track_id'], answer['queue_index']), (200, True, 7, 2))

    # ── Review 8 / P2: a renderer that does not answer cannot hold a session read ──

    def silent_renderer(self, *, answers_transport=False):
        """Track 5 casts, then the renderer stops answering (but for
        GetTransportInfo if `answers_transport`): every other SOAP request
        waits out its whole timeout (bridge.SOAP_TIMEOUT, cut to 1 s here)
        and fails. Returns the timeouts the renderer was given."""
        self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        with self.core.lock:
            self.core.snapshots.clear()  # no fresh monitor read: the lookup asks the renderer
        waits = []
        def silent(timeout):
            waits.append(bridge.SOAP_TIMEOUT if timeout is None else timeout)
            time.sleep(waits[-1])
        def soap(device, action, values=None, timeout=None):
            silent(timeout)
            return None
        def transport(device, timeout=None):
            if answers_transport:
                return 'PLAYING'
            silent(timeout)
            return 'UNKNOWN'
        def position(device, timeout=None):
            silent(timeout)
            return 0.0, 0.0, ''
        for name, fake in (('soap', soap), ('transport', transport), ('position', position)):
            patcher = patch.object(self.core, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (('SOAP_TIMEOUT', 1.0), ('SESSION_READ_BUDGET', 0.8)):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return waits

    def timed_session_read(self):
        started = time.monotonic()
        status, _, data = self.request(f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
        return status, json.loads(data), time.monotonic() - started

    def test_unresponsive_renderer_fails_a_session_read_within_its_budget(self):
        waits = self.silent_renderer()
        status, answer, took = self.timed_session_read()
        self.assertEqual(status, 503)
        self.assertIn('try again', answer['error'])
        # 0.8 s budget; three tries of two 1 s reads used to wait up to 6 s.
        self.assertLess(took, 1.5)
        self.assertLessEqual(sum(waits), 0.8 + 0.05)

    def test_position_read_hanging_after_the_state_answers_stays_within_the_budget(self):
        waits = self.silent_renderer(answers_transport=True)
        status, _, took = self.timed_session_read()
        self.assertEqual(status, 503)
        self.assertLess(took, 1.5)
        self.assertLessEqual(sum(waits), 0.8 + 0.05)

    def test_stop_during_a_stalled_session_read_ends_the_session_for_the_next_read(self):
        self.silent_renderer()
        reads = []
        reader = threading.Thread(target=lambda: reads.append(self.timed_session_read()), daemon=True)
        reader.start()
        time.sleep(0.2)  # the read now waits on the renderer
        status, _, data = self.request(f'/api/cast/control/{self.UDN}/Stop', method='POST',
                                       headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
        self.assertEqual((status, json.loads(data)), (200, {'success': False}))  # the renderer never answered
        self.assertNotIn(self.UDN, self.core.sessions)  # the Stop still ends the session
        self.assertEqual(self.core.grants, {})
        reader.join(5)
        status, answer, took = reads[0]
        self.assertIn((status, answer.get('active')), ((503, None), (200, False)))  # never the stopped session
        self.assertLess(took, 1.5)
        status, answer, took = self.timed_session_read()
        self.assertEqual((status, answer), (200, {'active': False}))
        self.assertLess(took, 0.5)  # nothing left to ask the renderer

    # ── Review 9 / P2: the lookup deadline holds against a renderer that drips its answer ──

    def dripping_renderer(self, gap=0.1):
        """A real HTTP renderer that sends its whole answer, status line and
        headers included, one byte every `gap` seconds (about 8 s): each byte
        well inside any socket timeout, the answer far past it. (A dripped
        body alone would not do: read(amt) returns with the first bytes.)"""
        stop, asked = threading.Event(), []
        class Drip(socketserver.BaseRequestHandler):
            def handle(self):
                asked.append(1)
                self.request.settimeout(5)
                data = b''
                with contextlib.suppress(OSError):
                    while b'\r\n\r\n' not in data:
                        data += self.request.recv(4096)
                    answer = (b'HTTP/1.1 200 OK\r\nContent-Type: text/xml\r\nConnection: close\r\n\r\n'
                              b'<CurrentTransportState>PLAYING</CurrentTransportState>')
                    for byte in answer[:80]:
                        if stop.is_set():
                            break
                        self.request.sendall(bytes([byte]))
                        time.sleep(gap)
        # Not 127.0.0.1: that is this test bridge's own LAN IP, never a renderer.
        server = socketserver.ThreadingTCPServer(('127.0.0.2', 0), Drip)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def close():
            stop.set()
            server.shutdown()
            server.server_close()
        self.addCleanup(close)
        return bridge.Renderer(self.UDN, 'EXN100 Living Room', '127.0.0.2',
                               f'http://127.0.0.2:{server.server_address[1]}/ctl'), asked

    def test_renderer_dripping_its_answer_cannot_outlast_the_session_deadline(self):
        device, asked = self.dripping_renderer()
        with self.core.lock:
            self.core.devices[self.UDN] = device
            self.core.sessions[self.UDN] = bridge.QueueSession(
                ids=(5,), index=0, repeat='none', credential=CREDENTIAL, token='loaded', track_id=5)
        self.addCleanup(self.core.devices.__setitem__, self.UDN, self.renderer)
        with patch.object(bridge, 'SESSION_READ_BUDGET', 0.8):
            for _ in range(2):  # and again while the first abandoned read still drips
                started = time.monotonic()
                status, _, data = self.request(f'/api/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
                took = time.monotonic() - started
                self.assertEqual(status, 503, data)
                # urllib's timeout covers each socket operation: on its own this read ran ~8 s.
                self.assertLess(took, 1.5)
        self.assertGreaterEqual(len(asked), 1, 'the renderer was never asked')

class ReviewP1UnitTests(unittest.TestCase):
    CFG = dict(upstream_url='http://100.100.100.100:8765', control_bind='100.80.1.2', control_port=8877,
               lan_ip='192.168.1.22', media_port=8878, renderer_network=ipaddress.ip_network('192.168.1.0/24'))
    STATUS = {'Self': {'UserID': 1, 'TailscaleIPs': ['100.80.1.2']},
              'Peer': {'a': {'UserID': 1, 'TailscaleIPs': ['100.80.1.10']},   # owner's iPhone
                       'b': {'UserID': 2, 'TailscaleIPs': ['100.80.1.20']},   # parents' tablet
                       'c': {'UserID': 3, 'TailscaleIPs': ['100.80.1.30']}},  # tagged server
              'User': {'1': {'LoginName': 'Edwyn@example.com'}, '2': {'LoginName': 'mum@example.com'},
                       '3': {'LoginName': 'tagged-devices'}}}

    def core(self, **extra):
        core = bridge.Bridge(bridge.BridgeConfig(**self.CFG, **extra))
        core.tailnet_status = lambda: self.STATUS
        return core

    def test_peer_policy_defaults_to_bridge_owner(self):
        core = self.core()
        self.assertTrue(core.peer_allowed('100.80.1.10'))
        self.assertFalse(core.peer_allowed('100.80.1.20'))
        self.assertFalse(core.peer_allowed('100.80.1.30'))
        self.assertFalse(core.peer_allowed('100.80.1.99'))  # not in the tailnet map
        self.assertTrue(core.peer_allowed('127.0.0.1'))
        # Tailscale Serve: loopback peer, identity in the header, counted only
        # while this PC's Serve verifiably proxies to the bridge.
        with patch.object(core, 'serve_proxies_here', return_value=True):
            self.assertTrue(core.peer_allowed('127.0.0.1', 'edwyn@example.com'))
            self.assertFalse(core.peer_allowed('127.0.0.1', 'mum@example.com'))
        with patch.object(core, 'serve_proxies_here', return_value=False):
            self.assertFalse(core.peer_allowed('127.0.0.1', 'edwyn@example.com'))

    def test_direct_peer_cannot_claim_the_owner_login(self):
        # Review 3: the parents' tablet sends the owner's login header itself.
        core = self.core()
        with patch.object(core, 'serve_proxies_here', return_value=True):
            self.assertFalse(core.peer_allowed('100.80.1.20', 'edwyn@example.com'))
            self.assertFalse(core.peer_allowed('100.80.1.20', 'edwyn@example.com', proxied=True))
            self.assertFalse(core.peer_allowed('100.80.1.99', 'edwyn@example.com'))
            self.assertTrue(core.peer_allowed('100.80.1.10', 'mum@example.com'))  # the IP decides

    def test_loopback_forwarded_without_login_refused(self):
        core = self.core()
        with patch.object(core, 'serve_proxies_here', return_value=True):
            self.assertFalse(core.peer_allowed('127.0.0.1', '', proxied=True))

    def test_serve_verification_requires_proxy_here_and_no_funnel(self):
        core = self.core()
        here = {'Web': {'pc.tail.ts.net:443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8877'}}}}}
        cases = [(here, True),
                 ({**here, 'AllowFunnel': {'pc.tail.ts.net:443': True}}, False),
                 ({'Web': {'pc.tail.ts.net:443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:9999'}}}}}, False),
                 ({}, False)]
        for config, expected in cases:
            with patch.object(core, '_tailscale_json', return_value=config):
                self.assertEqual(core.serve_proxies_here(), expected, config)

    def test_peer_policy_allowlist(self):
        core = self.core(allowed_peers=('mum@example.com', '100.80.1.30'))
        self.assertTrue(core.peer_allowed('100.80.1.20'))
        self.assertTrue(core.peer_allowed('100.80.1.30'))
        self.assertFalse(core.peer_allowed('100.80.1.10'))  # the list replaces the owner rule

    def test_peer_policy_fails_closed_without_tailscale(self):
        core = bridge.Bridge(bridge.BridgeConfig(**self.CFG))
        core.tailnet_status = lambda: {}
        self.assertFalse(core.peer_allowed('100.80.1.10'))
        self.assertTrue(core.peer_allowed('127.0.0.1'))

    def test_config_parses_allowed_peers(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.json'
            p.write_text(json.dumps({'upstream_url': 'http://100.100.100.100:8765', 'control_bind': '127.0.0.1',
                                     'lan_ip': '192.168.1.3', 'allowed_peers': ['Mum@Example.com']}))
            self.assertEqual(bridge.BridgeConfig.load(p).allowed_peers, ('mum@example.com',))
            p.write_text(json.dumps({'upstream_url': 'http://100.100.100.100:8765', 'control_bind': '127.0.0.1',
                                     'lan_ip': '192.168.1.3', 'allowed_peers': 'everyone'}))
            with self.assertRaises(ValueError):
                bridge.BridgeConfig.load(p)

    def test_config_stay_awake_defaults_on(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.json'
            base = {'upstream_url': 'http://100.100.100.100:8765', 'control_bind': '127.0.0.1', 'lan_ip': '192.168.1.3'}
            p.write_text(json.dumps(base))
            self.assertTrue(bridge.BridgeConfig.load(p).stay_awake)
            p.write_text(json.dumps({**base, 'stay_awake': False}))
            self.assertFalse(bridge.BridgeConfig.load(p).stay_awake)
            p.write_text(json.dumps({**base, 'stay_awake': 'yes'}))
            with self.assertRaises(ValueError):
                bridge.BridgeConfig.load(p)
        example = json.loads(Path(bridge.__file__).with_name('config.example.json').read_text(encoding='utf-8'))
        self.assertIs(example['stay_awake'], True)

    def test_slow_tv_does_not_hide_exn100_or_overrun_the_budget(self):
        silent = socket.socket()
        silent.bind(('127.0.0.2', 0))
        silent.listen(64)  # accepts connections, never answers
        desc = (b'<root><device><friendlyName>EXN100</friendlyName><UDN>uuid:exn</UDN><serviceList><service>'
                b'<serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>'
                b'<controlURL>/c</controlURL></service></serviceList></device></root>')
        class Desc(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(desc)))
                self.end_headers()
                self.wfile.write(desc)
        exn = ThreadingHTTPServer(('127.0.0.3', 0), Desc)
        threading.Thread(target=exn.serve_forever, daemon=True).start()
        try:
            core = bridge.Bridge(bridge.BridgeConfig('http://100.100.100.100:8765', '127.0.0.1', 8877, '127.0.0.1',
                                                     8878, ipaddress.ip_network('127.0.0.0/8')))
            tv = f'http://127.0.0.2:{silent.getsockname()[1]}'
            locations = {f'{tv}/tv{n}.xml': '127.0.0.2' for n in range(30)}
            locations[f'http://127.0.0.3:{exn.server_port}/d.xml'] = '127.0.0.3'
            started = time.monotonic()
            found = core.describe(locations, started + 1.0)
            self.assertLess(time.monotonic() - started, 1.6)
            self.assertEqual([d.name for d in found.values()], ['EXN100'])
        finally:
            exn.shutdown()
            exn.server_close()
            silent.close()
        # SSDP window plus description fetches stay inside Velvet's 8 s UI call.
        self.assertLess(bridge.SCAN_BUDGET, 8)
        self.assertLess(bridge.SSDP_WINDOW, bridge.SCAN_BUDGET)


class ProxyEnvironmentTests(unittest.TestCase):
    def test_parent_api_key_never_sent_through_environment_proxy(self):
        seen = []
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                seen.append((self.path, self.headers.get('X-API-Key')))
                self.send_response(502)
                self.send_header('Content-Length', '0')
                self.end_headers()
        class Parent(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                body = b'{"tracks": 1}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        servers = [ThreadingHTTPServer(('127.0.0.1', 0), h) for h in (Proxy, Parent)]
        for s in servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        proxy, parent = (f'http://127.0.0.1:{s.server_port}' for s in servers)
        # A fresh interpreter, so the module-level opener is built with the
        # proxy already in the environment (as on a PC with a proxy set).
        code = (
            'import sys, ipaddress, urllib.request; sys.path.insert(0, sys.argv[1]); import bridge\n'
            'try: urllib.request.urlopen(sys.argv[2] + "/control", timeout=5)\n'
            'except Exception: pass\n'
            'cfg = bridge.BridgeConfig(sys.argv[2], "127.0.0.1", 1, "127.0.0.1", 2, ipaddress.ip_network("127.0.0.0/8"))\n'
            'print(bridge.Bridge(cfg).upstream_json("/api/stats", "secret-key"))\n')
        env = {k: v for k, v in os.environ.items() if k.lower() not in ('no_proxy', 'http_proxy', 'https_proxy')}
        env.update(http_proxy=proxy, HTTP_PROXY=proxy)
        try:
            out = subprocess.run([sys.executable, '-c', code, str(Path(bridge.__file__).parent), parent],
                                 env=env, capture_output=True, text=True, timeout=30)
        finally:
            for s in servers:
                s.shutdown()
                s.server_close()
        self.assertIn("{'tracks': 1}", out.stdout, out.stderr)
        # Control: the default urllib opener does go through the proxy...
        self.assertIn((parent + '/control', None), seen)
        # ...the bridge never does, so the proxy never saw the key.
        self.assertFalse([s for s in seen if s[1]], seen)


class ValidationTests(unittest.TestCase):
    def test_hostile_friendly_name_cannot_inject_markup(self):
        cfg = bridge.BridgeConfig('http://100.100.100.100:8765', '127.0.0.1', 8877,
                                  '192.168.1.22', 8878, ipaddress.ip_network('192.168.1.0/24'))
        core = bridge.Bridge(cfg)
        def desc(name, udn='uuid:exn'):
            return (f'<root><device><friendlyName>{name}</friendlyName><UDN>{udn}</UDN><serviceList><service>'
                    '<serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>'
                    '<controlURL>/c</controlURL></service></serviceList></device></root>').encode()
        loc = 'http://192.168.1.45:4321/d.xml'
        dev = core.parse_renderer(desc('EXN100&lt;img src=x onerror=alert(1)&gt;'), loc, '192.168.1.45')
        self.assertNotRegex(dev.name, r'[<>&"\']')
        self.assertIsNone(core.parse_renderer(desc('EXN100', 'uuid:a/../b'), loc, '192.168.1.45'))

    def test_auto_lan_ip_prefers_default_route_over_hyperv_switch(self):
        class Probe:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def connect(self, addr):
                pass
            def getsockname(self):
                return ('192.168.0.12', 50000)
        with patch.object(bridge.socket, 'socket', return_value=Probe()), \
             patch.object(bridge.socket, 'gethostbyname_ex', return_value=('pc', [], ['192.168.48.1', '192.168.0.12'])), \
             patch.object(bridge.socket, 'getaddrinfo', return_value=[]):
            self.assertEqual(bridge.auto_lan_ip(), '192.168.0.12')

    def test_host_allowlist(self):
        cfg = bridge.BridgeConfig('http://100.100.100.100:8765', '100.80.1.2', 8877,
                                  '192.168.1.22', 8878, ipaddress.ip_network('192.168.1.0/24'),
                                  allowed_hosts=('home-bridge',))
        machine = socket.gethostname().lower()
        for ok in ('100.80.1.2:8877', 'localhost:8877', 'home-bridge:8877', f'{machine}:8877',
                   f'{machine}.tail1234.ts.net:8877'):
            self.assertTrue(cfg.host_allowed(ok), ok)
        for bad in ('evil.example:8877', '', f'{machine}.evil.example', '100.80.1.3:8877'):
            self.assertFalse(cfg.host_allowed(bad), bad)

    def test_renderer_rejects_external_location_and_accepts_lan_upnp(self):
        cfg = bridge.BridgeConfig('http://100.100.100.100:8765', '127.0.0.1', 8877,
                                  '192.168.1.22', 8878, ipaddress.ip_network('192.168.1.0/24'))
        core = bridge.Bridge(cfg)
        xml = (b'<root><device><friendlyName>Cambridge Audio EXN100</friendlyName>'
               b'<UDN>uuid:exn</UDN><serviceList><service>'
               b'<serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>'
               b'<controlURL>/upnp/control</controlURL></service></serviceList></device></root>')
        loc = 'http://192.168.1.45:4321/description.xml'
        dev = core.parse_renderer(xml, loc, '192.168.1.45')
        self.assertIsNotNone(dev)
        self.assertEqual(dev.control_url, 'http://192.168.1.45:4321/upnp/control')
        self.assertIsNone(core.parse_renderer(xml, 'http://8.8.8.8:80/desc.xml', '8.8.8.8'))
        self.assertIsNone(core.parse_renderer(xml, 'http://192.168.1.99:80/desc.xml', '192.168.1.45'))
        poisoned = xml.replace(b'/upnp/control', b'http://192.168.1.99/exploit')
        self.assertIsNone(core.parse_renderer(poisoned, loc, '192.168.1.45'))

    def test_real_soap_xml_request_and_fault_detection(self):
        observed = []
        class DeviceHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return
            def do_POST(self):
                n = int(self.headers.get('Content-Length', '0'))
                payload = self.rfile.read(n)
                observed.append((self.headers.get('SOAPACTION'), payload))
                self.send_response(200)
                self.send_header('Content-Type', 'text/xml')
                self.end_headers()
                if 'Reject' in self.headers.get('SOAPACTION', ''):
                    self.wfile.write(b'<s:Envelope xmlns:s="soap"><s:Body><s:Fault>Bad</s:Fault></s:Body></s:Envelope>')
                else:
                    self.wfile.write(b'<s:Envelope xmlns:s="soap"><s:Body><u:PlayResponse xmlns:u="upnp"/></s:Body></s:Envelope>')
        device_server = ThreadingHTTPServer(('127.0.0.1', 0), DeviceHandler)
        thread = threading.Thread(target=device_server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = bridge.BridgeConfig('http://100.100.100.100:8765', '127.0.0.1', 8877,
                                      '127.0.0.2', 8878, ipaddress.ip_network('127.0.0.0/8'))
            core = bridge.Bridge(cfg)
            device = bridge.Renderer('uuid:local', 'EXN100', '127.0.0.1',
                                     f'http://127.0.0.1:{device_server.server_port}/control')
            self.assertIsNotNone(core.soap(device, 'Play', {'Speed': '1'}))
            self.assertIn('AVTransport:1#Play', observed[0][0])
            self.assertIn(b'<Speed>1</Speed>', observed[0][1])
            self.assertIsNone(core.soap(device, 'Reject'))
        finally:
            device_server.shutdown()
            device_server.server_close()

    def test_config_rejects_public_control_and_parent_password_url(self):
        for config in ({'upstream_url': 'http://100.100.100.100:8765', 'control_bind': '0.0.0.0', 'lan_ip': '192.168.1.3'},
                       {'upstream_url': 'http://user:secret@100.100.100.100:8765', 'control_bind': '127.0.0.1', 'lan_ip': '192.168.1.3'}):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'config.json'
                p.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    bridge.BridgeConfig.load(p)

    def test_grants_scoped_to_renderer_ip(self):
        cfg = bridge.BridgeConfig('http://100.100.100.100:8765', '127.0.0.1', 8877,
                                  '192.168.1.2', 8878, ipaddress.ip_network('192.168.1.0/24'))
        core = bridge.Bridge(cfg)
        dev = bridge.Renderer('udn', 'EXN100', '192.168.1.45', 'http://192.168.1.45:80/control')
        token = core._new_grant(5, dev, CREDENTIAL, 'flac')
        with self.assertRaises(bridge.HTTPFailure):
            core.grant_for(token.token, 5, 'flac', '192.168.1.46')
        self.assertEqual(core.grant_for(token.token, 5, 'flac', '192.168.1.45').track_id, 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
