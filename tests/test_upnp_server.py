import unittest
import http.client
import socket
import threading
import time
import sqlite3
import tempfile
import concurrent.futures
import xml.etree.ElementTree as ET
from pathlib import Path
from contextlib import contextmanager, closing
from email.message import Message
from unittest.mock import patch, MagicMock

import upnp_server
from db_schema import apply_migrations


@contextmanager
def loopback_server(handler=None):
    factory = getattr(upnp_server, "BoundedUPnPServer", upnp_server.ThreadingHTTPServer)
    server = factory(("127.0.0.1", 0), handler or upnp_server.UPnPHandler)
    errors = []
    server.handle_error = lambda *args: errors.append(args)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    worker.start()
    try:
        yield server, errors
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def description(address):
    client = http.client.HTTPConnection(*address, timeout=2)
    try:
        client.request("GET", "/upnp/device.xml")
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


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
                                        ("0", "0", (0, 0)),
                                        ("0", "50000", (0, 50000)),
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
        with patch.object(upnp_server, "BoundedUPnPServer") as server:
            upnp_server.run_http()
        server.assert_called_once_with(("0.0.0.0", upnp_server.UPNP_PORT), upnp_server.UPnPHandler)
        server.return_value.serve_forever.assert_called_once()

    def test_slow_drip_body_and_headers_obey_total_deadline(self):
        for stage in ("body", "headers"):
            with self.subTest(stage=stage), \
                 patch.object(upnp_server, "UPNP_CONNECTION_TIMEOUT", 0.3), \
                 patch.object(upnp_server, "UPNP_REQUEST_TIMEOUT", 0.45, create=True), \
                 loopback_server() as (server, errors):
                with socket.create_connection(server.server_address, timeout=2) as peer:
                    peer.settimeout(0.04)
                    prefix = b"POST /upnp/control/ConnectionManager HTTP/1.0\r\n"
                    prefix += b"Content-Length: 1000\r\n\r\n" if stage == "body" else b"X-Slow: "
                    peer.sendall(prefix)
                    started = time.monotonic()
                    closed = False
                    delivered = 0
                    while time.monotonic() - started < 1.4:
                        try:
                            peer.sendall(b"x")
                            delivered += 1
                            if peer.recv(4096) == b"":
                                closed = True
                                break
                        except socket.timeout:
                            continue
                        except ConnectionError:
                            closed = True
                            break
                    elapsed = time.monotonic() - started
                    self.assertTrue(closed, "trickling bytes must not extend the total deadline")
                    self.assertLess(elapsed, 1.1)
                    self.assertGreater(delivered, 5, "exercise a live drip, not an idle timeout")
                self.assertEqual(description(server.server_address)[0], 200)
                self.assertEqual(errors, [])

    def test_connection_cap_refuses_overload_and_recovers_slots(self):
        state = threading.Condition()
        counts = {"active": 0, "peak": 0}

        class ObservedHandler(upnp_server.UPnPHandler):
            def setup(self):
                super().setup()
                with state:
                    counts["active"] += 1
                    counts["peak"] = max(counts["peak"], counts["active"])
                    state.notify_all()

            def finish(self):
                try:
                    super().finish()
                finally:
                    with state:
                        counts["active"] -= 1
                        state.notify_all()

        with patch.object(upnp_server, "UPNP_MAX_CONNECTIONS", 2, create=True), \
             loopback_server(ObservedHandler) as (server, errors):
            peers = []
            try:
                peers.append(socket.create_connection(server.server_address, timeout=2))
                peers[0].sendall(b"POST /upnp/control/ConnectionManager HTTP/1.0\r\nContent-Length: 1000\r\n\r\nx")
                with state:
                    self.assertTrue(state.wait_for(lambda: counts["active"] == 1, timeout=2))
                self.assertEqual(description(server.server_address)[0], 200)
                with state:
                    self.assertTrue(state.wait_for(lambda: counts["active"] == 1, timeout=2))
                peers.append(socket.create_connection(server.server_address, timeout=2))
                with state:
                    self.assertTrue(state.wait_for(lambda: counts["active"] == 2, timeout=2))
                for _ in range(8):
                    with socket.create_connection(server.server_address, timeout=0.5) as refused:
                        try:
                            reply = refused.recv(1)
                        except socket.timeout:
                            self.fail("overload opened another worker instead of refusing the connection")
                        self.assertEqual(reply, b"", "overload must refuse without another worker")
                self.assertEqual(counts["peak"], 2)
                peers.pop().close()
                with state:
                    self.assertTrue(state.wait_for(lambda: counts["active"] == 1, timeout=2))
                self.assertEqual(description(server.server_address)[0], 200)
            finally:
                for peer in peers:
                    peer.close()
            with state:
                self.assertTrue(state.wait_for(lambda: counts["active"] == 0, timeout=2))
            self.assertEqual(errors, [])

    def test_disconnected_response_and_worker_start_failure_release_slots(self):
        handler = object.__new__(upnp_server.UPnPHandler)
        handler.wfile = MagicMock()
        handler.wfile.write.side_effect = BrokenPipeError()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._send(200, "text/plain", "ok")
        self.assertTrue(handler.close_connection)
        with patch.object(upnp_server, "UPNP_MAX_CONNECTIONS", 1):
            server = upnp_server.BoundedUPnPServer(("127.0.0.1", 0), upnp_server.UPnPHandler)
            try:
                with patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start worker")):
                    with self.assertRaises(RuntimeError):
                        server.process_request(MagicMock(), ("127.0.0.1", 1))
                self.assertEqual(server._connections, {})
                self.assertTrue(server._slots.acquire(blocking=False))
                server._slots.release()
            finally:
                server.server_close()

    def test_normal_latency_soap_and_error_paths_restore_capacity(self):
        with patch.object(upnp_server, "UPNP_MAX_CONNECTIONS", 1), \
             patch.object(upnp_server, "UPNP_CONNECTION_TIMEOUT", 0.2), \
             patch.object(upnp_server, "UPNP_REQUEST_TIMEOUT", 1), \
             loopback_server() as (server, errors):
            body = b"<Envelope><Body><GetProtocolInfo/></Body></Envelope>"
            with socket.create_connection(server.server_address, timeout=2) as peer:
                peer.sendall(b"POST /upnp/control/ConnectionManager HTTP/1.0\r\n"
                             b'SOAPACTION: "urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"\r\n'
                             + f"Content-Length: {len(body)}\r\n\r\n".encode())
                for offset in range(0, len(body), 10):
                    peer.sendall(body[offset:offset + 10])
                    time.sleep(0.03)
                response = http.client.HTTPResponse(peer)
                response.begin()
                self.assertEqual(response.status, 200)
                self.assertIn(b"GetProtocolInfoResponse", response.read())

            def wait_for_capacity():
                until = time.monotonic() + 1
                while time.monotonic() < until:
                    with server._connections_lock:
                        if not server._connections:
                            return
                    time.sleep(0.01)
                self.fail("connection slot was leaked")

            wait_for_capacity()
            for payload, status in [
                (b"POST /upnp/control/ConnectionManager HTTP/1.0\r\nContent-Length: 10\r\n\r\nx", 408),
                (b"POST /upnp/control/ConnectionManager HTTP/1.0\r\nContent-Length: -1\r\n\r\n", 400),
                (b"POST /upnp/control/ConnectionManager HTTP/1.0\r\nContent-Length: 0\r\n\r\n", 501),
            ]:
                with socket.create_connection(server.server_address, timeout=2) as peer:
                    peer.sendall(payload)
                    response = http.client.HTTPResponse(peer)
                    response.begin()
                    self.assertEqual(response.status, status)
                    response.read()
                wait_for_capacity()
            with socket.create_connection(server.server_address, timeout=2) as peer:
                peer.sendall(b"POST /upnp/control/ConnectionManager HTTP/1.0\r\nContent-Length: 100\r\n\r\nx")
                peer.shutdown(socket.SHUT_WR)
                while peer.recv(4096):
                    pass
            wait_for_capacity()
            self.assertEqual(description(server.server_address)[0], 200)
            self.assertEqual(errors, [])

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


class LargeCatalogBrowseTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.db_path = Path(folder) / "catalog.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            apply_migrations(db)
            db.execute("INSERT INTO artists(id, name, sort_name) VALUES(1, 'Artist', 'Artist')")
            db.executemany("INSERT INTO albums(id, artist_id, title, year) VALUES(?, 1, ?, 2000)",
                           [(i, f"Album {i:04}") for i in range(1, 1506)])
            db.executemany("INSERT INTO tracks(id, album_id, artist_id, title, file_path, format, track_number) "
                           "VALUES(?, ?, 1, ?, ?, 'FLAC', ?)",
                           [(i, i, f"Track {i:04}", f"/isolated/music/{i}.flac", i) for i in range(1, 1506)])
            db.commit()
        self.enterContext(patch.object(upnp_server, "DB_PATH", self.db_path))
        # Keep real SQL, serialization and HTTP, but don't mint persisted tickets.
        self.enterContext(patch.object(upnp_server, "media_urls",
                                     side_effect=lambda track, *args: (f"http://localhost/audio/{track['id']}", "")))
        self.enterContext(patch.object(upnp_server, "content_directory_allowed", return_value=True))
        self.server, self.errors = self.enterContext(loopback_server())

    def browse(self, oid, start=0, count=0, flag="BrowseDirectChildren"):
        body = (f"<ObjectID>{oid}</ObjectID><StartingIndex>{start}</StartingIndex>"
                f"<RequestedCount>{count}</RequestedCount><BrowseFlag>{flag}</BrowseFlag>")
        client = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            client.request("POST", "/upnp/control/ContentDirectory", body,
                           {"SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"'})
            response = client.getresponse()
            return response.status, ET.fromstring(response.read())
        finally:
            client.close()

    def assert_page(self, oid, start, count, expected, total=1505, flag="BrowseDirectChildren"):
        status, soap = self.browse(oid, start, count, flag)
        self.assertEqual(status, 200)
        self.assertEqual(int(soap.findtext(".//NumberReturned")), expected)
        self.assertEqual(int(soap.findtext(".//TotalMatches")), total)
        didl = ET.fromstring(soap.findtext(".//Result"))
        self.assertEqual(len(didl), expected)
        return [element.attrib["id"] for element in didl]

    def test_zero_and_large_counts_never_silently_truncate_large_catalog(self):
        for oid, prefix in (("tracks", "t"), ("albums", "al_"), ("ar_1", "al_")):
            for count in (0, 1001, 1505, 2**32 - 1):
                with self.subTest(oid=oid, count=count):
                    expected = min(count, 1505) if count else 1505
                    ids = self.assert_page(oid, 0, count, expected)
                    self.assertEqual(ids, [f"{prefix}{i}" for i in range(1, expected + 1)])
            self.assert_page(oid, 1000, 0, 505)
            self.assert_page(oid, 1000, 2000, 505)
            self.assert_page(oid, 1505, 0, 0)
            self.assert_page(oid, 2**32 - 1, 20, 0)

    def test_sequential_pages_return_every_track_and_album_once(self):
        for oid, prefix in (("tracks", "t"), ("albums", "al_"), ("ar_1", "al_")):
            ids = []
            for start in range(0, 1505, 700):
                ids.extend(self.assert_page(oid, start, 700, min(700, 1505 - start)))
            self.assertEqual(ids, [f"{prefix}{i}" for i in range(1, 1506)])

    def test_metadata_and_album_children_keep_browse_semantics(self):
        self.assert_page("tracks", 0, 0, 1, total=1, flag="BrowseMetadata")
        self.assert_page("al_1", 0, 0, 1, total=1)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE tracks SET album_id=1")
            db.commit()
        self.assert_page("al_1", 0, 0, 1505)
        self.assert_page("al_1", 0, 1505, 1505)
        self.assert_page("al_1", 1000, 0, 505)

    def test_large_browse_uses_batched_reads_instead_of_fetchall(self):
        sizes = []
        opened, closed = [], []
        connect = sqlite3.connect

        class ObservedCursor(sqlite3.Cursor):
            def fetchmany(self, size):
                sizes.append(size)
                return super().fetchmany(size)

            def fetchall(self):
                if self.description and len(self.description) > 1:
                    raise AssertionError("Browse rows must be fetched in batches")
                return super().fetchall()

        class ObservedConnection(sqlite3.Connection):
            def execute(self, sql, params=()):
                return self.cursor(factory=ObservedCursor).execute(sql, params)

            def close(self):
                closed.append(self)
                super().close()

        def observed_connect(*args, **kwargs):
            conn = connect(*args, factory=ObservedConnection, **kwargs)
            opened.append(conn)
            return conn

        with patch.object(upnp_server.sqlite3, "connect", side_effect=observed_connect):
            self.assert_page("tracks", 0, 0, 1505)
        self.assertGreater(len(sizes), 1)
        self.assertEqual(set(sizes), {32})
        self.assertEqual(opened, closed)

    def test_actual_oversized_soap_faults_before_materializing_whole_result(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE tracks SET title=?", ("樂<&" * 600,))
            db.commit()
        for count in (0, 1505):
            with self.subTest(count=count), \
                 patch.object(upnp_server, "track_xml", wraps=upnp_server.track_xml) as render:
                status, soap = self.browse("tracks", count=count)
                self.assertEqual(status, 500)
                self.assertEqual(soap.findtext(".//{*}errorCode"), "720")
                self.assertIsNone(soap.find(".//NumberReturned"))
                self.assertLess(render.call_count, 1505)
                self.assertGreater(render.call_count, 1)
        # The fault released its DB cursor/connection; subsequent writes and
        # normal-sized pages still work, with no partial-success response.
        with closing(sqlite3.connect(self.db_path, timeout=0.2)) as db:
            db.execute("UPDATE tracks SET title='Small title'")
            db.commit()
        self.assert_page("tracks", 0, 1505, 1505)

    def test_byte_budget_includes_utf8_escaping_and_envelope_exactly(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE tracks SET title=?", ('歌<&>"',))
            db.commit()
        status, soap = self.browse("tracks", count=10)
        self.assertEqual(status, 200)
        result = soap.findtext(".//Result")
        # Independently serialize the existing SOAP template, including both
        # levels of XML escaping; the exact boundary must succeed.
        escaped = result.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        response = upnp_server._soap_response(
            "service:ContentDirectory:1", "Browse",
            f"<Result>{escaped}</Result><NumberReturned>10</NumberReturned>"
            "<TotalMatches>1505</TotalMatches><UpdateID>1</UpdateID>")
        size = len(response.encode("utf-8"))
        with patch.object(upnp_server, "UPNP_MAX_BROWSE_RESPONSE_BYTES", size):
            self.assert_page("tracks", 0, 10, 10)
        with patch.object(upnp_server, "UPNP_MAX_BROWSE_RESPONSE_BYTES", size - 1):
            status, fault = self.browse("tracks", count=10)
        self.assertEqual(status, 500)
        self.assertEqual(fault.findtext(".//{*}errorCode"), "720")

    def test_duplicate_sort_fields_keep_id_tiebreakers_across_pages(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE albums SET title='Same', year=2000")
            db.execute("UPDATE tracks SET track_number=1, disc_number=1")
            db.executemany("INSERT INTO artists(id, name, sort_name) VALUES(?, ?, 'Artist')",
                           [(i, f"Artist {i}") for i in range(2, 6)])
            db.commit()
        self.assertEqual(self.assert_page("artists", 0, 0, 5, total=5),
                         [f"ar_{i}" for i in range(1, 6)])
        for oid, prefix in (("tracks", "t"), ("albums", "al_"), ("ar_1", "al_")):
            ids = []
            for start in range(0, 1505, 700):
                ids.extend(self.assert_page(oid, start, 700, min(700, 1505 - start)))
            self.assertEqual(ids, [f"{prefix}{i}" for i in range(1, 1506)])
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE tracks SET album_id=1")
            db.commit()
        self.assertEqual(self.assert_page("al_1", 700, 700, 700),
                         [f"t{i}" for i in range(701, 1401)])

    def test_concurrent_browse_and_response_byte_limit(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda start: self.assert_page("tracks", start, 50, 50),
                                    range(0, 400, 50)))
        self.assertEqual(len({item for page in results for item in page}), 400)
        with patch.object(upnp_server, "UPNP_MAX_BROWSE_RESPONSE_BYTES", 1000, create=True):
            status, soap = self.browse("tracks", count=10)
        self.assertEqual(status, 500)
        self.assertEqual(soap.findtext(".//{*}errorCode"), "720")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
