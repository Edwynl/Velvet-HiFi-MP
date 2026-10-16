"""Secure Tailnet browser origin detection does not modify Tailscale state."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from remote_access import TailscaleManager


def result(text,code=0):
    return SimpleNamespace(returncode=code,stdout=text)


class SecureTailnetTests(unittest.TestCase):
    def _status(self, serve_output):
        manager=TailscaleManager()
        cmds=[]
        def run(argv, **kwargs):
            cmds.append(tuple(argv))
            if argv[:2]==["tailscale","status"]:
                return result(json.dumps({"Self":{
                    "HostName":"velvet-desktop",
                    "DNSName":"velvet-desktop.tailnet-name.ts.net.",
                }}))
            if argv[:2]==["tailscale","ip"]:
                return result("100.101.1.2" if "-4" in argv else "fd7a::1")
            if argv[:3]==["tailscale","serve","status"]:
                return result(serve_output)
            raise AssertionError(argv)
        with patch("remote_access.shutil.which",return_value="tailscale"), \
             patch("remote_access.subprocess.run",side_effect=run):
            status=manager.get_status()
        return status,cmds

    def test_https_is_shown_only_after_serve_is_verified(self):
        status,cmds=self._status(
            "https://velvet-desktop.tailnet-name.ts.net\n"
            "|-- proxy http://127.0.0.1:8765\n"
        )
        self.assertTrue(status["https_ready"])
        self.assertEqual(status["secure_url"],"https://velvet-desktop.tailnet-name.ts.net")
        self.assertEqual(status["url"],"http://100.101.1.2:8765")
        self.assertFalse(any(command[1] in ("funnel","up","down") for command in cmds))
        self.assertIn(("tailscale","serve","status"),cmds)

    def test_dns_name_alone_does_not_claim_https_works(self):
        status,_=self._status("No serve config")
        self.assertFalse(status["https_ready"])
        self.assertIsNone(status["secure_url"])
        self.assertEqual(status["https_setup_command"],"tailscale serve --bg 8765")


if __name__=="__main__":
    unittest.main()
