import unittest
from unittest.mock import patch, MagicMock

import upnp_server


class UpnpServerTests(unittest.TestCase):
    def test_separate_content_directory_cannot_bypass_library_auth(self):
        for enabled, client_ip, authenticated, expected in [
            (False, "192.168.1.5", False, 403),
            (True, "192.168.1.5", False, 403),
            (True, "127.0.0.1", False, 403),
            (True, "192.168.1.5", True, None),
            (False, "127.0.0.1", False, None),
        ]:
            with self.subTest(enabled=enabled, ip=client_ip, authenticated=authenticated):
                handler = MagicMock()
                handler.path = "/upnp/control/ContentDirectory"
                handler.client_address = (client_ip, 1234)
                handler.headers = {"Content-Length": "0"}
                handler.rfile.read.return_value = b""
                with patch.object(upnp_server.security, "is_auth_enabled", return_value=enabled), \
                     patch.object(upnp_server.security, "verify_auth", return_value={"allowed": authenticated}):
                    upnp_server.UPnPHandler.do_POST(handler)
                if expected:
                    self.assertEqual(handler._send.call_args.args[0], expected)
                    handler._handle_content_dir.assert_not_called()
                    handler.rfile.read.assert_not_called()
                else:
                    handler._handle_content_dir.assert_called_once()

    def test_cast_track_prefers_full_metadata_before_plain_uri_fallback(self):
        discovered = {
            "renderer-1": {
                "control_url": "http://renderer/control",
                "name": "Living Room Renderer",
            }
        }
        calls = []

        def fake_soap_request(ctrl_url, svc, action, args):
            calls.append((action, args))
            if action == "SetAVTransportURI" and "CurrentURIMetaData" in args and "&lt;DIDL-Lite" in args:
                return {"ok": True}
            if action == "Play":
                return {"ok": True}
            return None

        with patch.object(upnp_server, "discovered_devices", discovered), \
             patch.object(upnp_server, "soap_request", side_effect=fake_soap_request):
            success = upnp_server.cast_track(
                "renderer-1",
                "http://server/api/stream/42",
                '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item /></DIDL-Lite>',
            )

        self.assertEqual(success, "playing")
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "SetAVTransportURI")
        self.assertIn("&lt;DIDL-Lite", calls[0][1])
        self.assertEqual(calls[-1][0], "Play")

    def test_cast_track_distinguishes_loaded_play_refused_and_kept_paused(self):
        discovered = {"renderer-1": {"control_url": "http://renderer/control", "name": "R"}}
        for play_response, play, expected, actions in (
                (None, True, "play_failed", ["SetAVTransportURI", "Play"]),
                ({"ok": True}, False, "loaded", ["SetAVTransportURI"])):
            calls = []

            def fake_soap_request(ctrl_url, svc, action, args):
                calls.append(action)
                return play_response if action == "Play" else {"ok": True}

            with self.subTest(expected=expected), \
                    patch.object(upnp_server, "discovered_devices", discovered), \
                    patch.object(upnp_server, "soap_request", side_effect=fake_soap_request), \
                    patch("time.sleep"):
                self.assertEqual(upnp_server.cast_track("renderer-1", "http://server/a", "", play=play), expected)
            self.assertEqual(calls, actions)


if __name__ == "__main__":
    unittest.main()
