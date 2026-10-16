"""Password hashing must not fall back to unsalted SHA-256."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import security


class PasswordHashingTests(unittest.TestCase):
    def test_pbkdf2_fallback_is_salted_and_verifiable(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"), \
             patch.object(security, "BCRYPT_AVAILABLE", False):
            manager = security.SecurityManager()
            self.assertTrue(manager.set_password("a-proper-long-password"))
            stored = manager._config.password_hash
            self.assertTrue(stored.startswith("pbkdf2_sha256$600000$"))
            self.assertNotEqual(stored, hashlib.sha256(b"a-proper-long-password").hexdigest())
            self.assertTrue(manager.check_password("a-proper-long-password"))
            self.assertFalse(manager.check_password("incorrect"))
            reread = security.SecurityManager()
            self.assertTrue(reread.check_password("a-proper-long-password"))

    def test_legacy_sha256_password_is_read_only_compatible(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"), \
             patch.object(security, "BCRYPT_AVAILABLE", False):
            manager = security.SecurityManager()
            manager._config.enabled = True
            manager._config.password_hash = hashlib.sha256(b"old-password").hexdigest()
            self.assertTrue(manager.check_password("old-password"))
            self.assertFalse(manager.check_password("different-password"))

    def test_rejects_short_password(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"):
            manager = security.SecurityManager()
            self.assertFalse(manager.set_password("short"))
            self.assertFalse(manager.is_enabled())

    def test_long_unicode_password_round_trips_without_bcrypt_truncation(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"):
            manager = security.SecurityManager()
            password = "密" * 30
            self.assertTrue(manager.set_password(password))
            self.assertTrue(manager.check_password(password))
            self.assertFalse(manager.check_password(password + "x"))
            self.assertFalse(manager.set_password(["invalid"] * 8))

    def test_login_preserves_existing_clients_until_explicit_rotation(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"):
            manager = security.SecurityManager()
            manager.set_password("a-long-password")
            first = manager.get_or_create_api_key()
            self.assertEqual(first, manager.get_or_create_api_key())
            self.assertTrue(manager.verify_api_key(first))
            self.assertNotEqual(first, manager.generate_api_key())
            self.assertFalse(manager.verify_api_key(first))

    def test_password_change_revokes_previous_api_key(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"):
            manager = security.SecurityManager()
            manager.set_password("old-long-password")
            previous = manager.get_or_create_api_key()
            manager.set_password("new-long-password")
            self.assertFalse(manager.verify_api_key(previous))
            self.assertNotEqual(previous, manager.get_or_create_api_key())

    def test_anonymous_page_loads_do_not_lock_out_first_login(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(security, "CONFIG_FILE", Path(root) / "security.json"):
            manager = security.SecurityManager()
            manager.set_password("a-long-password")
            with patch.object(security, "get_security_manager", return_value=manager):
                for _ in range(10):
                    self.assertFalse(security.verify_auth(ip="192.168.1.5")["allowed"])
                self.assertTrue(security.verify_auth(password="a-long-password", ip="192.168.1.5")["allowed"])
                for _ in range(5):
                    self.assertFalse(security.verify_auth(api_key="wrong-key", ip="192.168.1.5")["allowed"])
                self.assertFalse(security.verify_auth(password="a-long-password", ip="192.168.1.5")["allowed"])


if __name__ == "__main__":
    unittest.main()
