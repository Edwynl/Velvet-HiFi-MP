import unittest
import http.client
import socket
import threading
from email.message import Message
from unittest.mock import patch, MagicMock

import upnp_server


class UpnpServerTests(unittest.TestCase):
    def test_invalid_or_unbounded_soap_bodies_are_rejected_before_read(self):
        for headers, status in [({}, 411), ({"Content-Length": "-1"}, 400),
                                ({"Content-Length": "abc"}, 400),
                                ({"Content-Length": "9" * 100}, 400),
                                ({"Content-Length": str(upnp_server.UPNP_MAX_SOAP_BYTES + 1)}, 413),
                                ({"Content-Length": "0", "Transfer-Encoding": "chunked"}, 400)]:
            with self.subTest(headers=headers):
                handler = MagicMock()
                handler.path = "/upnp/control/ConnectionManager"
                handler.headers = headers
                upnp_server.UPnPHandler.do_POST(handler)
                self.assertEqual(handler._send.call_args.args[0], status)
                handler.rfile.read.assert_not_called()
                handler._handle_conn_mgr.assert_not_called()

    def test_soap_body_timeout_and_early_disconnect(self):
        for read, status in [(TimeoutError(), 408), (b"x", 400)]:
            with self.subTest(read=read):
                handler = MagicMock()
                handler.path = "/upnp/control/ConnectionManager"
                handler.headers = {"Content-Length": "10"}
                if isinstance(read, Exception):
                    handler.rfile.read.side_effect = read
                else:
                    handler.rfile.read.return_value = read
                upnp_server.UPnPHandler.do_POST(handler)
                self.assertEqual(handler._send.call_args.args[0], status)
                handler._handle_conn_mgr.assert_not_called()

    def test_duplicate_content_lengths_are_rejected(self):
        handler = MagicMock()
        handler.path = "/upnp/control/ConnectionManager"
        handler.headers = Message()
        handler.headers["Content-Length"] = "1"
        handler.headers["Content-Length"] = "2"
        upnp_server.UPnPHandler.do_POST(handler)
        self.assertEqual(handler._send.call_args.args[0], 400)
        handler.rfile.read.assert_not_called()

    def test_body_at_size_limit_is_dispatched(self):
        handler = MagicMock()
        handler.path = "/upnp/control/ConnectionManager"
        handler.headers = {"Content-Length": str(upnp_server.UPNP_MAX_SOAP_BYTES),
                           "SOAPAction": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"'}
        body = b" " * upnp_server.UPNP_MAX_SOAP_BYTES
        handler.rfile.read.return_value = body
        upnp_server.UPnPHandler.do_POST(handler)
        handler._handle_conn_mgr.assert_called_once()
        action, received = handler._handle_conn_mgr.call_args.args
        self.assertEqual(action, "GetProtocolInfo")
        self.assertEqual(len(received), len(body))

    def test_browse_paging_is_validated_and_bounded(self):
        for start, count, expected in [("-1", "1", None), ("0", "-1", None),
                                        ("bad", "1", None), (str(2**32), "1", None),
                                        ("0", str(2**32), None), ("0", "9" * 100, None),
                                        ("0", "0", (0, upnp_server.UPNP_MAX_BROWSE_COUNT)),
                                        ("0", "50000", (0, upnp_server.UPNP_MAX_BROWSE_COUNT)),
                                        (str(2**32 - 1), "20", (2**32 - 1, 20))]:
            with self.subTest(start=start, count=count):
                handler = MagicMock()
                body = f"<StartingIndex>{start}</StartingIndex><RequestedCount>{count}</RequestedCount>"
                with patch.object(upnp_server, "browse", return_value=("", 0, 0)) as browse:
                    upnp_server.UPnPHandler._handle_content_dir(handler, "Browse", body)
                if expected is None:
                    browse.assert_not_called()
                    self.assertIn("<errorCode>402</errorCode>", handler._send.call_args.args[2])
                else:
                    browse.assert_called_once_with("0", "BrowseDirectChildren", *expected)

    def test_run_http_uses_threaded_server(self):
        with patch.object(upnp_server, "ThreadingHTTPServer") as server:
            upnp_server.run_http()
        server.assert_called_once_with(("0.0.0.0", upnp_server.UPNP_PORT), upnp_server.UPnPHandler)
        server.return_value.serve_forever.assert_called_once()

    def test_slow_post_does_not_block_other_clients_and_times_out(self):
        # Bind only an ephemeral loopback port, never the production UPnP port.
        with patch.object(upnp_server, "UPNP_CONNECTION_TIMEOUT", 0.5):
            server = upnp_server.ThreadingHTTPServer(("127.0.0.1", 0), upnp_server.UPnPHandler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with socket.create_connection(server.server_address, timeout=2) as slow:
                    slow.sendall(b'POST /upnp/control/ConnectionManager HTTP/1.0\r\n'
                                 b'Content-Length: 100\r\n\r\nx')
                    client = http.client.HTTPConnection(*server.server_address, timeout=2)
                    try:
                        client.request("GET", "/upnp/device.xml")
                        response = client.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertIn(b"MediaServer", response.read())
                    finally:
                        client.close()
                    response = http.client.HTTPResponse(slow)
                    response.begin()
                    self.assertEqual(response.status, 408)
                    response.read()
                # Idle connections are bounded even before sending headers.
                with socket.create_connection(server.server_address, timeout=2) as idle:
                    self.assertEqual(idle.recv(1), b"")
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)

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


    def test_get_position_info_returns_safe_velvet_track_identity(self):
        discovered = {"renderer-1": {"control_url": "http://renderer/control"}}
        uris = [
            ("http://192.168.1.10:9876/api/stream/42?ticket=SECRET&amp;dsp=3", 42),
            ("http://192.168.1.10:9876/api/stream/43?ticket=SECRET", 43),
            ("http://evil.example:9876/api/stream/42?ticket=SECRET", None),
            ("http://192.168.1.10:9877/api/stream/42", None),
            ("http://192.168.1.10:9876/api/covers/42", None),
            ("", None),
        ]
        with patch.object(upnp_server, "LOCAL_IP", "192.168.1.10"), \
             patch.object(upnp_server, "MAIN_PORT", 9876), \
             patch.object(upnp_server, "discovered_devices", discovered):
            for uri, expected_track_id in uris:
                with self.subTest(uri=uri):
                    response = (
                        "<s:Envelope><s:Body><u:GetPositionInfoResponse>"
                        "<RelTime>0:00:07</RelTime><TrackDuration>0:02:00</TrackDuration>"
                        f"<TrackURI>{uri}</TrackURI>"
                        "</u:GetPositionInfoResponse></s:Body></s:Envelope>"
                    ).encode("utf-8")
                    with patch.object(upnp_server, "soap_request", return_value=response) as soap:
                        self.assertEqual(upnp_server.get_position_info("renderer-1"), (7, 120))
                        self.assertEqual(
                            upnp_server.get_position_info("renderer-1", include_track_id=True),
                            (7, 120, expected_track_id),
                        )
                        self.assertEqual(soap.call_count, 2)

    def test_get_position_info_handles_missing_device_and_failed_soap(self):
        with patch.object(upnp_server, "discovered_devices", {}):
            self.assertEqual(upnp_server.get_position_info("missing"), (0, 0))
            self.assertEqual(
                upnp_server.get_position_info("missing", include_track_id=True),
                (0, 0, None),
            )
        with patch.object(upnp_server, "discovered_devices",
                          {"r": {"control_url": "http://renderer/control"}}), \
             patch.object(upnp_server, "soap_request", return_value=None):
            self.assertEqual(upnp_server.get_position_info("r", include_track_id=True),
                             (0, 0, None))


if __name__ == "__main__":
    unittest.main()
