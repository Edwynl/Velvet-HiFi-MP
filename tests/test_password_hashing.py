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


if __name__ == "__main__":
    unittest.main()
