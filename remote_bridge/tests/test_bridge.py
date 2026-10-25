"""Offline unit and integration tests; no EXN100 or Tailscale required."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import io
import json
import re
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPHandler

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
        elif re.fullmatch(r'/api/tracks/(6|7|8)', self.path):
            # The rest of the album, for queue tests (track 8 lasts over an hour).
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
                return
            tid = int(self.path.rsplit('/', 1)[1])
            self.reply({'id': tid, 'title': f'Track {tid}', 'artist_name': '徐佳莹', 'album_title': 'Album',
                        'album_id': 9, 'duration': 4000 if tid == 8 else 200, 'format': 'FLAC',
                        'sample_rate': 44100, 'bit_depth': 16, 'file_path': f'D:/Music/{tid}.flac'})
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
            self.reply({'success': payload.get('password') == 'admin-password'},
                       200 if payload.get('password') == 'admin-password' else 403)
        else:
            self.reply({'detail': 'Not found'}, 404)

    def reply(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Type', 'application/json')
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

    def test_older_pause_does_not_undo_newer_play(self):
        actions, answers = self.first_request_held_in_admin_check(
            (self.CONTROL + 'Pause',), (self.CONTROL + 'Play',))
        self.assertEqual(actions, ['Play'])  # the renderer ends up playing
        self.assertEqual(answers['first'], (200, {'success': True, 'superseded': True}))
        self.assertEqual(answers['second'], (200, {'success': True}))

    def test_older_play_does_not_undo_newer_pause(self):
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

    def test_status_at_natural_end_lets_the_ui_auto_advance(self):
        # Velvet plays the next track when a confirmed cast reports STOPPED
        # with position >= duration - 2 and its own track_id.
        _, grant = self.cast_ok()
        with patch.object(self.core, 'transport', return_value='STOPPED'), \
             patch.object(self.core, 'position', return_value=(209.5, 210.0, grant.url(self.core.cfg))):
            status, _, data = self.request('/api/cast/status/uuid:EXN100-test',
                                           headers={'X-API-Key': CREDENTIAL})
        self.assertEqual(json.loads(data), {'state': 'STOPPED', 'position': 209.5, 'duration': 210.0,
                                            'track_id': 5, 'is_playing': False})
        soap, _ = self.cast_ok()  # the UI's next cast after that end still plays
        self.assertIn('Play', [c.args[1] for c in soap.call_args_list])

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
            def transport(self, device):
                return self.state
            def read_position(self, device):
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

    def test_queue_updates_and_limits(self):
        renderer = self.fake_renderer()
        self.cast_queue([5, 6, 7], 0)
        def post(body):
            status, _, data = self.request(f'/bridge/queue/{self.UDN}', method='POST', body=body,
                                           headers={'X-API-Key': CREDENTIAL, 'Cookie': COOKIE})
            return status, json.loads(data)
        self.assertEqual(post({'track_id': 5, 'ids': [5, 7], 'index': 0}), (200, {'success': True}))
        self.assertEqual(post({'track_id': 6, 'ids': [6, 7], 'index': 0}), (200, {'success': False, 'stale': True}))
        self.assertEqual(post({'track_id': 5, 'ids': [5] * 5001, 'index': 0})[0], 422)
        self.assertEqual(post({'track_id': 5, 'ids': [5, 'x'], 'index': 0})[0], 422)
        self.assertEqual(post({'track_id': 5, 'ids': [5, 6], 'index': 1})[0], 422)  # index is not track 5
        self.play_through(renderer)
        self.assertEqual(renderer.tracks(), [5, 7])  # the edited queue, not the original

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
        status, _, data = self.request(f'/bridge/session/{self.UDN}', headers={'X-API-Key': CREDENTIAL})
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

    def test_monitor_thread_holds_keep_awake_for_the_session(self):
        renderer = self.fake_renderer()
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
