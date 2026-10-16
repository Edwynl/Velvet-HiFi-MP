"""Regression tests for authentication boundaries and audio configuration."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from settings import ScanConstants


class SecurityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

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

    def test_audio_soxr_profiles_use_valid_resampler(self):
        for profile in ScanConstants.UPSAMPLE_QUALITY.values():
            self.assertTrue(profile.startswith("soxr:precision="))


if __name__ == "__main__":
    unittest.main()
