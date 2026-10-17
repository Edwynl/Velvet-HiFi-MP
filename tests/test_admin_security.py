"""Shared listening credentials must never authorize library management."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import admin_security
import security
import server
from db_schema import apply_migrations

ADMIN = "owner-only-admin-password"
LISTENER = "shared-listening-password"


class AdminPasswordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "admin-security.json"
        self.manager = admin_security.AdminSecurity(self.path)

    def test_unconfigured_management_is_locked(self):
        self.assertEqual(self.manager.verify(ADMIN, "127.0.0.1")[1], "admin_setup_required")

    def test_password_is_salted_hashed_and_survives_restart(self):
        self.manager.set_password(ADMIN)
        first = self.path.read_text()
        self.assertNotIn(ADMIN, first)
        self.assertTrue(admin_security.AdminSecurity(self.path).password_matches(ADMIN))
        self.manager.set_password(ADMIN)
        self.assertNotEqual(self.path.read_text(), first)
        self.assertFalse(self.manager.password_matches(LISTENER))

    def test_damaged_configuration_stays_locked(self):
        for body in ("broken", "[]", "{}", '{"version":1,"iterations":1}'):
            with self.subTest(body=body):
                self.path.write_text(body)
                self.assertTrue(self.manager.is_configured())
                self.assertEqual(self.manager.verify(ADMIN, "peer")[1], "admin_password_invalid")

    def test_missing_password_never_authorizes_or_consumes_attempts(self):
        self.manager.set_password(ADMIN)
        for _ in range(10):
            self.assertEqual(self.manager.verify(None, "peer")[1], "admin_password_required")
        self.assertIsNone(self.manager.verify(ADMIN, "peer"))
        # Approval is not a session; the next request still needs a password.
        self.assertEqual(self.manager.verify(None, "peer")[1], "admin_password_required")

    def test_admin_guess_lockout_expires_and_does_not_affect_other_listener(self):
        self.manager.set_password(ADMIN)
        with patch.object(admin_security.time, "monotonic", return_value=1000):
            for _ in range(5):
                self.assertEqual(self.manager.verify("wrong", "peer")[1], "admin_password_invalid")
            self.assertEqual(self.manager.verify(ADMIN, "peer")[0], 429)
            self.assertIsNone(self.manager.verify(ADMIN, "other-peer"))
        with patch.object(admin_security.time, "monotonic", return_value=1301):
            self.assertIsNone(self.manager.verify(ADMIN, "peer"))

    def test_route_policy_preserves_listening_and_guards_alternative_management_paths(self):
        for method, path in [("POST", "/api/stream-ticket"), ("POST", "/api/security/login"),
                             ("POST", "/api/albums/1/favorite"), ("POST", "/api/tracks/1/played"),
                             ("POST", "/api/playback/play"), ("POST", "/api/cast/device"),
                             ("POST", "/api/playlists"), ("GET", "/api/albums/1/radio")]:
            self.assertFalse(admin_security.requires_admin(method, path), (method, path))
        for method, path in [("POST", "/api/albums/1/set-cover/"), ("DELETE", "/api/albums/1"),
                             ("POST", "/api/artists/1/refresh"), ("POST", "/api/scan/refresh-folder"),
                             ("POST", "/api/library/reset"), ("POST", "/api/config/libraries"),
                             ("POST", "/api/security/setup-password"), ("POST", "/api/security/disable"),
                             ("GET", "/api/security/reset-api-key"), ("POST", "/api/security/admin-password")]:
            self.assertTrue(admin_security.requires_admin(method, path), (method, path))


class AdminHttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patches = [patch.object(security, "CONFIG_FILE", self.root / "security.json")]
        self.patches[0].start()
        self.listener = security.SecurityManager()
        self.listener.set_password(LISTENER)
        self.key = self.listener.generate_api_key()
        self.patches.extend([
            patch.object(security, "_security_manager", self.listener),
            patch.object(server, "get_db", side_effect=self.connect),
            patch.object(server._scan_job, "is_running", return_value=False),
            patch.object(server._duplicate_job, "is_running", return_value=False),
        ])
        for item in self.patches[1:]:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.admin = admin_security.get_admin_security()
        self.admin.set_password(ADMIN)
        self.client = TestClient(server.app, base_url="http://localhost:8765",
                                 client=("127.0.0.1", 50100), headers={"X-API-Key": self.key})
        self.addCleanup(self.client.close)
        conn = self.connect()
        try:
            apply_migrations(conn)
            conn.execute("INSERT INTO artists(id,name) VALUES (1,'Test artist')")
            conn.execute("INSERT INTO albums(id,artist_id,title) VALUES (1,1,'Test album')")
            conn.commit()
        finally:
            conn.close()

    def connect(self):
        conn = sqlite3.connect(self.root / "library.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def test_shared_key_or_password_cannot_manage_even_with_local_auth_bypass(self):
        operations = [("POST", "/api/albums/1/fetch-cover"), ("POST", "/api/albums/1/set-cover"),
                      ("POST", "/api/albums/1/cover"), ("POST", "/api/albums/1/open-folder"),
                      ("POST", "/api/albums/1/refresh"), ("DELETE", "/api/albums/1"),
                      ("POST", "/api/library/reset"), ("POST", "/api/artists/1/refresh"),
                      ("POST", "/api/security/disable"), ("POST", "/api/security/generate-api-key"),
                      ("GET", "/api/security/reset-api-key"), ("POST", "/api/config")]
        with patch.dict("os.environ", {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "false"}), \
             patch.object(server, "get_db") as database:
            for method, path in operations:
                with self.subTest(path=path):
                    response = self.client.request(method, path, json={})
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertEqual(response.json()["code"], "admin_password_required")
            database.assert_not_called()
        self.assertTrue(self.listener.is_enabled())

    def test_valid_admin_password_is_required_again_for_the_next_operation(self):
        response = self.client.delete("/api/albums/1", headers={"X-Admin-Password": ADMIN})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.delete("/api/albums/999").status_code, 403)
        self.assertEqual(self.client.get("/api/stats").status_code, 200)
        self.assertEqual(self.client.post("/api/stream-ticket").status_code, 200)

    def test_listening_password_query_cookie_and_media_ticket_cannot_replace_admin_password(self):
        with patch.object(server, "get_db") as database:
            variants = [({"X-Admin-Password": LISTENER}, ""),
                        ({"X-Password": LISTENER}, ""),
                        ({"Cookie": f"velvet_cover_key={self.key}"}, ""),
                        ({}, f"?admin_password={ADMIN}&ticket=invalid")]
            for headers, query in variants:
                with self.subTest(headers=list(headers), query=bool(query)):
                    response = self.client.delete("/api/albums/1" + query, headers=headers)
                    self.assertEqual(response.status_code, 403)
            database.assert_not_called()

    def test_first_setup_is_direct_local_only_and_password_must_be_distinct(self):
        self.admin.path.unlink()
        for headers in ({"Host": "velvet.tail123.ts.net", "X-Forwarded-For": "100.101.102.103"},
                        {"X-Forwarded-For": "127.0.0.1"}, {"Origin": "https://other.example"}):
            with self.subTest(headers=headers):
                response = self.client.post("/api/security/admin-password", headers=headers,
                                            json={"password": ADMIN})
                self.assertEqual(response.status_code, 403)
                self.assertFalse(self.admin.is_configured())
        response = self.client.post("/api/security/admin-password", json={"password": LISTENER})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/security/admin-password", json={"password": ADMIN})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.admin.password_matches(ADMIN))

    def test_admin_password_change_requires_current_admin_and_keeps_listening_config(self):
        original = security.CONFIG_FILE.read_bytes()
        response = self.client.post("/api/security/admin-password", json={"password": "new-admin-password"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/security/admin-password", headers={"X-Admin-Password": ADMIN},
                                    json={"password": "new-admin-password"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.admin.password_matches(ADMIN))
        self.assertTrue(self.admin.password_matches("new-admin-password"))
        self.assertEqual(security.CONFIG_FILE.read_bytes(), original)

    def test_listening_password_cannot_be_changed_to_admin_password(self):
        response = self.client.post("/api/security/setup-password", headers={"X-Admin-Password": ADMIN},
                                    json={"password": ADMIN})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.listener.check_password(LISTENER))

    def test_admin_password_without_normal_listening_auth_is_rejected(self):
        with TestClient(server.app, base_url="http://localhost:8765", client=("203.0.113.9", 50001)) as remote:
            response = remote.delete("/api/albums/1", headers={"X-Admin-Password": ADMIN})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
