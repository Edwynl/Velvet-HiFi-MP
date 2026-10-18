"""Regression tests for authentication boundaries and audio configuration."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from settings import ScanConstants


class SecurityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url="http://localhost:8765")

    def tearDown(self):
        self.client.close()

    def test_security_management_requires_auth_even_when_status_is_public(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": False, "error": "denied"}), \
             patch.object(server, "get_client_ip", return_value="203.0.113.1"):
            self.assertEqual(self.client.get("/api/security/status").status_code, 200)
            for method, path in [
                ("POST", "/api/security/disable"),
                ("POST", "/api/security/generate-api-key"),
                ("GET", "/api/security/reset-api-key"),
                ("POST", "/api/security/setup-password"),
                ("POST", "/api/health/recycle-pool"),
            ]:
                with self.subTest(path=path):
                    self.assertEqual(self.client.request(method, path, json={}).status_code, 401)

    def test_first_password_bootstrap_requires_loopback(self):
        with patch.object(server.security, "is_auth_enabled", return_value=False), \
             patch.object(server, "get_client_ip", return_value="192.168.1.23"):
            response = self.client.post("/api/security/setup-password", json={"password": "long-password"})
            self.assertEqual(response.status_code, 403)

        with patch.object(server.security, "is_auth_enabled", return_value=False), \
             patch.object(server.security, "setup_password", return_value={"success": True}), \
             patch.object(server, "get_client_ip", return_value="127.0.0.1"):
            response = self.client.post("/api/security/setup-password", json={"password": "long-password"})
            self.assertEqual(response.status_code, 200)

    def test_forwarded_header_not_trusted_for_client_identity(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": False, "error": "denied"}), \
             patch.dict("os.environ", {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "false"}):
            response = self.client.post(
                "/api/security/disable",
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
            self.assertEqual(response.status_code, 401)

    def test_local_reverse_proxy_never_inherits_loopback_trust(self):
        # `tailscale serve --bg 8765` connects from 127.0.0.1 for remote phones.
        tailnet = {"Host": "velvet.tail1234.ts.net", "X-Forwarded-For": "100.101.102.103",
                   "X-Forwarded-Host": "velvet.tail1234.ts.net", "X-Forwarded-Proto": "https"}
        with TestClient(server.app, base_url="http://localhost:8765",
                        client=("127.0.0.1", 50123)) as loopback, \
             patch.object(server.security, "is_auth_enabled", return_value=False):
            self.assertEqual(loopback.get("/api/stats", headers=tailnet).status_code, 200)
            self.assertEqual(loopback.post("/api/config", headers=tailnet, json={}).status_code, 403)
            self.assertEqual(loopback.post("/api/config", json={},
                                           headers={"Host": tailnet["Host"]}).status_code, 403)
            self.assertEqual(loopback.post("/api/security/setup-password", headers=tailnet,
                                           json={"password": "long-password"}).status_code, 403)

        verify = patch.object(server.security, "verify_auth",
                              return_value={"allowed": False, "error": "denied"})
        with TestClient(server.app, base_url="http://localhost:8765",
                        client=("127.0.0.1", 50124)) as loopback, \
             patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.dict("os.environ", {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "false"}), \
             verify as verify_auth:
            self.assertEqual(loopback.get("/api/stats", headers=tailnet).status_code, 200)
            self.assertEqual(loopback.post("/api/config", headers=tailnet, json={}).status_code, 401)
            self.assertEqual(verify_auth.call_args.kwargs["ip"], "proxied:100.101.102.103")

    def test_forwarded_headers_never_raise_trust_of_remote_peer(self):
        with TestClient(server.app, base_url="http://localhost:8765",
                        client=("203.0.113.7", 50125)) as remote, \
             patch.object(server.security, "is_auth_enabled", return_value=False):
            response = remote.post("/api/config", json={}, headers={"X-Forwarded-For": "127.0.0.1"})
            self.assertEqual(response.status_code, 403)

    def test_audio_soxr_profiles_use_valid_resampler(self):
        for profile in ScanConstants.UPSAMPLE_QUALITY.values():
            self.assertTrue(profile.startswith("soxr:precision="))

    def test_public_pwa_assets_remain_available_when_auth_is_enabled(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True):
            for path in ["/sw.js", "/static/index.html", "/static/manifest.json", "/static/icon.png"]:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)

    def test_configured_cors_preflight_reaches_cors_middleware(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True):
            response = self.client.options("/api/dsp/profiles/1", headers={
                "Origin": "http://localhost:8765",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "X-API-Key,Content-Type",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:8765")

    def test_bootstrap_rejects_public_proxy_host_and_cross_origin(self):
        with patch.object(server.security, "is_auth_enabled", return_value=False), \
             patch.object(server, "get_client_ip", return_value="127.0.0.1"):
            for headers in [{"Host": "public.example"}, {"Origin": "https://evil.example"}]:
                self.assertEqual(self.client.post("/api/security/setup-password",
                    headers=headers, json={"password": "long-password"}).status_code, 403)

    def test_cookies_never_authorize_admin_routes(self):
        calls = []
        def verify(**kwargs):
            calls.append(kwargs)
            return {"allowed": bool(kwargs.get("api_key") == "cover-secret"), "error": "denied"}
        self.client.cookies.set("velvet_cover_key", "cover-secret")
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", side_effect=verify):
            # Covers are public: a missing cover may return 404, never 401.
            with patch.object(server, "get_db") as db:
                db.return_value.execute.return_value.fetchone.return_value = None
                self.assertNotEqual(self.client.get("/api/covers/99999").status_code, 401)
            self.assertEqual(calls, [])
            self.assertEqual(self.client.post("/api/security/disable").status_code, 401)
            self.assertIsNone(calls[-1]["api_key"])

    def test_credential_responses_deliver_a_working_stream_ticket(self):
        # The browser must hold a stream ticket as soon as it holds a key, so
        # tap-to-play never fetches one before audio.play().
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": True}), \
             patch.object(server.security, "get_security_manager") as manager, \
             patch.object(server.security, "issue_media_ticket",
                          return_value=("123.sig", 123)) as issue:  # via issue_browser_stream_ticket
            manager.return_value.get_or_create_api_key.return_value = "test-key"
            body = self.client.post("/api/security/login", headers={"X-Password": "pw"}).json()
        issue.assert_called_once_with("stream-any", 0, server.security.BROWSER_STREAM_TICKET_SECONDS)
        self.assertEqual(body["stream_ticket"], "123.sig")
        self.assertEqual(body["stream_ticket_expires_at"], 123)
        self.assertNotIn("test-key", body["stream_ticket"])

    def test_login_response_is_no_store_and_sets_no_cookie(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": True}), \
             patch.object(server.security, "get_security_manager") as manager, \
             patch.object(server.security, "issue_media_ticket", return_value=("1.sig", 1)):
            manager.return_value.get_or_create_api_key.return_value = "test-key"
            response = self.client.post("/api/security/login", headers={"X-Password": "password"})
            self.assertEqual(response.json()["api_key"], "test-key")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("set-cookie", response.headers)


if __name__ == "__main__":
    unittest.main()
