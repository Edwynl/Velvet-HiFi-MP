import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request
import admin_security
import listener_access
import security
import server


class AdminSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = patch.object(security, 'CONFIG_FILE', self.root/'security.json')
        self.config.start()
        self.manager = security.SecurityManager()
        self.manager.set_password('admin-test-password')
        self.manager.get_or_create_api_key()
        self.security_manager = patch.object(security, '_security_manager', self.manager)
        self.security_manager.start()
        self.admin = admin_security.get_admin_security()

    def tearDown(self):
        self.security_manager.stop()
        self.config.stop()
        self.tmp.cleanup()

    def test_session_uses_current_password_and_revokes_on_password_change(self):
        self.assertEqual(self.admin.unlock('wrong', 'peer')[0], None)
        token, error = self.admin.unlock('admin-test-password', 'peer')
        self.assertIsNone(error)
        self.assertTrue(self.admin.session_valid(token))
        self.assertFalse(self.admin.session_valid(self.manager.get_api_key()))
        with patch.object(admin_security.time, 'monotonic', return_value=10**12):
            self.assertFalse(self.admin.session_valid(token))
        self.manager.set_password('changed-admin-password')
        self.assertFalse(self.admin.session_valid(token))

    def test_disabled_listening_auth_does_not_make_every_password_admin(self):
        self.manager.disable()
        self.assertTrue(self.admin.is_configured())
        self.assertFalse(self.admin.password_matches('wrong'))
        self.assertTrue(self.admin.password_matches('admin-test-password'))

    def test_failed_admin_attempts_are_separate_and_limited(self):
        for _ in range(5): self.admin.unlock('wrong', 'peer')
        self.assertIn('Too many', self.admin.unlock('admin-test-password', 'peer')[1])
        self.assertTrue(self.manager.check_rate_limit('peer'))
        self.assertTrue(self.admin.unlock('admin-test-password', 'different-peer')[0])

    def test_corrupt_verifier_stays_locked(self):
        Path(security.CONFIG_FILE).write_text('invalid json', encoding='utf-8')
        self.assertTrue(self.admin.is_configured())
        self.assertFalse(self.admin.password_matches('anything'))

    def test_editing_a_playlist_is_not_an_administrator_action(self):
        """Renaming and removing a track are listening, not library surgery.

        Deleting the whole playlist stays behind the admin step on purpose, so
        that is the one that must still answer admin_password_required.
        """
        free = [
            ('POST', '/api/playlists'),
            ('PUT', '/api/playlists/7'),
            ('POST', '/api/playlists/7/tracks'),
            ('DELETE', '/api/playlists/7/tracks/42'),
        ]
        for method, path in free:
            with self.subTest(method=method, path=path):
                self.assertFalse(admin_security.requires_admin(method, path))
        self.assertTrue(admin_security.requires_admin('DELETE', '/api/playlists/7'))
        # Anything else under playlists is still administrator territory.
        self.assertTrue(admin_security.requires_admin('PUT', '/api/playlists/7/other'))

    def test_tailnet_browsing_and_sensitive_endpoints_need_separate_cookie(self):
        client = TestClient(server.app, raise_server_exceptions=False)
        with patch.object(listener_access, 'is_passwordless_listener', return_value=True), \
                patch.object(server, 'get_client_ip', return_value='100.70.0.2'), \
                patch.object(server, 'get_config', return_value={'private': 'configuration'}):
            self.assertEqual(client.get('/api/security/status').json()['passwordless_listening'], True)
            self.assertEqual(client.get('/api/config').status_code, 403)
            self.assertEqual(client.get('/api/config', headers={'X-API-Key':self.manager.get_api_key()}).status_code, 403)
            self.assertEqual(client.delete('/api/albums/999').json()['code'], 'admin_password_required')
            self.assertEqual(client.post('/api/albums/999/cover').status_code, 403)
            self.assertEqual(client.post('/api/cast/device', json={'track_id':1}).status_code, 403)
            self.assertEqual(client.post('/api/playback/play', json={'track_id':1}).status_code, 403)
            response = client.post('/api/security/admin-login', json={'password':'admin-test-password'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('api_key', response.json())
            cookie = response.headers['set-cookie'].lower()
            self.assertIn('httponly', cookie)
            self.assertIn('samesite=strict', cookie)
            self.assertNotIn('max-age', cookie)
            self.assertEqual(client.get('/api/config').status_code, 200)
            self.assertEqual(client.get('/api/config').status_code, 200)
            self.assertEqual(client.post('/api/health/recycle-pool', headers={'Origin':'https://evil.example'}).status_code, 403)


def request(peer, host='localhost:8765', headers=()):
    return Request({'type':'http','method':'GET','path':'/api/stats','scheme':'http',
                    'server':('127.0.0.1',8765),'client':(peer,1234),
                    'headers':[(b'host',host.encode()),*headers],'query_string':b''})


class TailnetListenerTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'ips':{'100.70.0.2','fd7a:115c:a1e0::2'},'dns':'server.example.ts.net',
                         'web':{'server.example.ts.net:443':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:8765'}}}},
                         'funnel':False}

    def test_only_known_peers_and_verified_local_serve_get_free_listening(self):
        with patch.object(listener_access, '_tailnet_snapshot', return_value=self.snapshot):
            self.assertTrue(listener_access.is_passwordless_listener(request('100.70.0.2'), '100.70.0.2'))
            self.assertFalse(listener_access.is_passwordless_listener(request('100.70.0.99'), '100.70.0.99'))
            self.assertTrue(listener_access.is_passwordless_listener(request('fd7a:115c:a1e0::2'), 'fd7a:115c:a1e0::2'))
            self.assertTrue(listener_access.is_passwordless_listener(request('127.0.0.1'), '127.0.0.1'))
            proxy=request('127.0.0.1','server.example.ts.net',[(b'x-forwarded-for',b'100.70.0.2')])
            self.assertTrue(listener_access.is_passwordless_listener(proxy,'proxied:100.70.0.2'))
            https_proxy=request('127.0.0.1','server.example.ts.net',[(b'x-forwarded-for',b'100.70.0.2'),(b'x-forwarded-proto',b'https')])
            self.assertEqual(listener_access.request_origin(https_proxy), 'https://server.example.ts.net')
            self.snapshot['funnel']=True
            self.assertFalse(listener_access.is_passwordless_listener(proxy,'proxied:100.70.0.2'))

    def test_spoofed_forwarded_headers_cannot_grant_access(self):
        with patch.object(listener_access, '_tailnet_snapshot', return_value=self.snapshot):
            for peer,host in [('8.8.8.8','server.example.ts.net'),('127.0.0.1','evil.example')]:
                req=request(peer,host,[(b'x-forwarded-for',b'100.70.0.2'),(b'tailscale-user-login',b'owner')])
                self.assertFalse(listener_access.is_passwordless_listener(req,'proxied:100.70.0.2'))
            rewritten=request('100.70.0.2','server.example.ts.net',[(b'x-forwarded-for',b'100.70.0.2')])
            self.assertFalse(listener_access.is_passwordless_listener(rewritten,'100.70.0.2'))

    def test_proxy_must_target_the_current_velvet_port(self):
        proxy=request('127.0.0.1','server.example.ts.net',[(b'x-forwarded-for',b'100.70.0.2')])
        with patch.object(listener_access, '_tailnet_snapshot', return_value=self.snapshot):
            for port in (8771, 8001, 8765):
                with self.subTest(port=port):
                    self.snapshot['web']['server.example.ts.net:443']['Handlers']['/']['Proxy'] = f'http://localhost:{port}'
                    self.assertEqual(listener_access.is_passwordless_listener(proxy,'proxied:100.70.0.2'), port == 8765)

    def test_sensitive_policy_covers_config_uploads_deletions_and_legacy_get(self):
        for method,path in [('GET','/api/config'),('POST','/api/albums/1/cover'),
                            ('GET','/api/security/reset-api-key'),('POST','/api/albums/1/open-folder'),
                            ('POST','/api/cast/device'),('POST','/api/playback/play'),
                            ('DELETE','/api/playlists/1')]:
            self.assertTrue(admin_security.requires_admin(method,path))
        # Adding a track to a playlist and taking it back out are the same kind
        # of act, so neither costs an administrator password; renaming the list
        # is editing, not a library change.
        for method,path in [('GET','/api/artists/1/albums'),('POST','/api/artists/1/enrich-bio'),
                            ('GET','/api/zones'),('GET','/api/cast/status/device'),
                            ('POST','/api/playlists/1/tracks'),('DELETE','/api/playlists/1/tracks/1'),
                            ('PUT','/api/playlists/1')]:
            self.assertFalse(admin_security.requires_admin(method,path))
