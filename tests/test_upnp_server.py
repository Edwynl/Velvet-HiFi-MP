import unittest
from unittest.mock import patch

import upnp_server


class UpnpServerTests(unittest.TestCase):
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

        self.assertTrue(success)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "SetAVTransportURI")
        self.assertIn("&lt;DIDL-Lite", calls[0][1])
        self.assertEqual(calls[-1][0], "Play")


if __name__ == "__main__":
    unittest.main()
