"""Offline unit and integration tests; no EXN100 or Tailscale required."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge

AUDIO = b'fLaC' + bytes(range(256)) * 32
CREDENTIAL = 'credential-for-testing'
COOKIE = 'velvet_admin_session=test-cookie'


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
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
            else:
                self.reply({'tracks': 1})
        elif self.path == '/api/tracks/5':
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
            else:
                self.reply({'id': 5, 'title': '测试 测试', 'artist_name': '徐佳莹',
                            'album_title': 'Album', 'duration': 210, 'format': 'FLAC',
                            'sample_rate': 96000, 'bit_depth': 24, 'file_path': 'D:/Music/t.flac'})
        elif self.path.startswith('/api/stream/5'):
            if self.headers.get('X-API-Key') != CREDENTIAL:
                self.reply({'error': 'Unauthorized'}, 401)
                return
            assert 'dsp=' not in self.path and 'upsample=none' in self.path
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

    def setUp(self):
        with self.core.lock:
            self.core.grants.clear()
            self.core.last_track.clear()
            self.core.cast_results.clear()
            self.core.generations.clear()

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
        self.assertIn('Administrator verification', data.decode())

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


class ValidationTests(unittest.TestCase):
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
        token = core._new_grant(5, dev, CREDENTIAL, 200, 'flac')
        with self.assertRaises(bridge.HTTPFailure):
            core.grant_for(token.token, 5, 'flac', '192.168.1.46')
        self.assertEqual(core.grant_for(token.token, 5, 'flac', '192.168.1.45').track_id, 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
