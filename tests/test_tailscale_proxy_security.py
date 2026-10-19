"""Real local HTTP checks of the Tailscale Serve authentication boundary.

No Tailnet configuration is changed. Child servers use disposable databases
and credentials, with the same Uvicorn proxy-header defaults as server.py.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = "pr10-disposable-proxy-test-key"
SERVE_HEADERS = {
    "Host": "velvet.tail1234.ts.net",
    "X-Forwarded-For": "100.101.102.103",
    "X-Forwarded-Host": "velvet.tail1234.ts.net",
    "X-Forwarded-Proto": "https",
    "Tailscale-User-Login": "test@example.invalid",
}
CHILD = """
import sys
from pathlib import Path
import security
security.CONFIG_FILE = Path(sys.argv[1]) / "security.json"
security._security_manager = security.SecurityManager()
if sys.argv[3] == "enabled":
    security._security_manager.set_password("disposable-test-password")
    security._security_manager._config.api_key = sys.argv[4]
import server
import uvicorn
uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[2]),
            lifespan="off", log_level="warning")
"""


@contextmanager
def isolated_http_server(*, enabled=False, enforce_all=True):
    with tempfile.TemporaryDirectory() as folder:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "VELVET_DATA_DIR": folder,
               "VELVET_MUSIC_DIR": folder, "VELVET_PORT": str(port),
               "VELVET_AUTH_ENFORCE_ALL_NETWORKS": str(enforce_all).lower(),
               "PYTHONUTF8": "1"}
        # A file avoids filling a pipe if startup fails or an assertion logs.
        with (Path(folder) / "server-output.txt").open("w+", encoding="utf-8") as output:
            proc = subprocess.Popen(
                [sys.executable, "-c", CHILD, folder, str(port),
                 "enabled" if enabled else "disabled", TEST_KEY],
                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                                  timeout=5) as client:
                    # The child imports the whole server module, so a cold start
                    # is measured in tens of seconds once the machine is busy
                    # running the rest of the suite. 20s made this fixture fail
                    # at random under full-suite load while passing on its own.
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        if proc.poll() is not None:
                            output.seek(0)
                            raise AssertionError("Proxy fixture exited: " + output.read())
                        try:
                            if client.get("/api/security/status").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        output.seek(0)
                        raise AssertionError(
                            "Proxy fixture did not become ready within 90s; child output:\n"
                            + output.read())
                    yield client
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


class TailscaleProxyHttpTests(unittest.TestCase):
    def test_unconfigured_proxy_cannot_browse_mutate_or_claim_initial_password(self):
        with isolated_http_server() as client:
            self.assertEqual(client.get("/api/stats").status_code, 200)
            variants = [SERVE_HEADERS, {"Host": SERVE_HEADERS["Host"]},
                        {"X-Forwarded-For": "127.0.0.1"},
                        {"Forwarded": "for=127.0.0.1;proto=https"},
                        {"X-Real-IP": "127.0.0.1"},
                        {"Tailscale-User-Login": "test@example.invalid"}]
            for headers in variants:
                with self.subTest(headers=headers):
                    self.assertEqual(client.get("/api/stats", headers=headers).status_code, 403)
                    self.assertEqual(client.delete("/api/albums/1?delete_files=true",
                                                   headers=headers).status_code, 403)
                    self.assertEqual(client.post("/api/security/setup-password", headers=headers,
                        json={"password": "attacker-test-password"}).status_code, 403)
            self.assertFalse(client.get("/api/security/status").json()["enabled"])

    def _check_protected_proxy(self, *, enforce_all):
        with isolated_http_server(enabled=True, enforce_all=enforce_all) as client:
            for headers in (SERVE_HEADERS, {**SERVE_HEADERS, "X-Forwarded-For": "127.0.0.1"},
                            {**SERVE_HEADERS, "X-Forwarded-For": "::1"}):
                with self.subTest(enforce_all=enforce_all, headers=headers):
                    self.assertEqual(client.get("/api/stats", headers=headers).status_code, 401)
                    self.assertEqual(client.post("/api/security/disable", headers=headers).status_code, 401)
                    self.assertEqual(client.get("/api/stats", headers={**headers,
                        "X-API-Key": "invalid-test-key"}).status_code, 401)
            authorized = {**SERVE_HEADERS, "X-API-Key": TEST_KEY}
            self.assertEqual(client.get("/api/stats", headers=authorized).status_code, 200)
            # Public app shell remains usable for the login screen.
            self.assertEqual(client.get("/", headers=SERVE_HEADERS).status_code, 200)
            self.assertTrue(client.get("/api/security/status").json()["enabled"])

    def test_configured_proxy_requires_credentials_with_enforcement_enabled(self):
        self._check_protected_proxy(enforce_all=True)

    def test_configured_proxy_requires_credentials_with_local_network_bypass_enabled(self):
        self._check_protected_proxy(enforce_all=False)


if __name__ == "__main__":
    unittest.main()
