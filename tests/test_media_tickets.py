"""Scoped media tickets, DLNA browse windows and transcode resource limits."""
import asyncio
import gc
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import security
import upnp_server
from audio_streaming import StreamSlots, iter_ffmpeg_stdout


class IsolatedSecurityMixin:
    """A fresh SecurityManager backed by a temporary security.json."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [patch.object(security, "CONFIG_FILE", Path(self._tmp.name) / "security.json")]
        self._patches[0].start()
        self.manager = security.SecurityManager()
        self._patches.append(patch.object(security, "_security_manager", self.manager))
        self._patches[1].start()

    def tearDown(self):
        for active in reversed(self._patches):
            active.stop()
        self._tmp.cleanup()


class MediaTicketTests(IsolatedSecurityMixin, unittest.TestCase):
    def test_ticket_is_bound_to_scope_and_resource(self):
        ticket, expires_at = security.issue_media_ticket("stream", 7, 600)
        self.assertAlmostEqual(expires_at, time.time() + 600, delta=2)
        self.assertTrue(security.verify_media_ticket(ticket, "stream", 7))
        self.assertTrue(security.verify_media_ticket(ticket, "stream", "7"))
        self.assertFalse(security.verify_media_ticket(ticket, "stream", 8))
        self.assertFalse(security.verify_media_ticket(ticket, "cover", 7))
        self.assertFalse(security.verify_media_ticket(ticket, "admin", 7))
        with self.assertRaises(ValueError):
            security.issue_media_ticket("admin", 7, 600)

    def test_tampered_malformed_and_expired_tickets_are_rejected(self):
        ticket, expires_at = security.issue_media_ticket("stream", 7, 600)
        signature = ticket.split(".", 1)[1]
        for bad in ["", "garbage", f"{expires_at + 3600}.{signature}", f"{expires_at}.{signature[:-2]}AA",
                    f"abc.{signature}", None]:
            with self.subTest(ticket=bad):
                self.assertFalse(security.verify_media_ticket(bad, "stream", 7))
        with patch.object(security.time, "time", return_value=expires_at + 1):
            self.assertFalse(security.verify_media_ticket(ticket, "stream", 7))

    def test_ticket_never_contains_the_api_key(self):
        api_key = self.manager.generate_api_key()
        ticket, _ = security.issue_media_ticket("stream", 1, 600)
        self.assertNotIn(api_key, ticket)

    def test_credential_changes_revoke_outstanding_tickets(self):
        for revoke in (lambda: self.manager.set_password("a-new-password"),
                       self.manager.generate_api_key,
                       self.manager.reset_api_key,
                       self.manager.disable):
            ticket, _ = security.issue_media_ticket("stream", 1, 600)
            self.assertTrue(security.verify_media_ticket(ticket, "stream", 1))
            revoke()
            self.assertFalse(security.verify_media_ticket(ticket, "stream", 1))

    def test_signing_secret_persists_across_restart(self):
        ticket, _ = security.issue_media_ticket("cover", 3, 600)
        restarted = security.SecurityManager()
        self.assertTrue(restarted.verify_media_ticket(ticket, "cover", 3))

    def test_ttl_follows_track_length_within_bounds(self):
        self.assertEqual(security.media_ticket_ttl(None), security.MEDIA_TICKET_GRACE_SECONDS)
        self.assertEqual(security.media_ticket_ttl("bad"), security.MEDIA_TICKET_GRACE_SECONDS)
        self.assertEqual(security.media_ticket_ttl(240), 240 + security.MEDIA_TICKET_GRACE_SECONDS)
        five_hour_opera = 5 * 3600
        self.assertEqual(security.media_ticket_ttl(five_hour_opera),
                         five_hour_opera + security.MEDIA_TICKET_GRACE_SECONDS)
        self.assertEqual(security.media_ticket_ttl(48 * 3600), security.MEDIA_TICKET_MAX_SECONDS)
        _, expires_at = security.issue_media_ticket("stream", 1, 10 ** 9)
        self.assertLessEqual(expires_at, time.time() + security.MEDIA_TICKET_MAX_SECONDS + 1)


class DlnaBrowseAuthorizationTests(IsolatedSecurityMixin, unittest.TestCase):
    def _allowed(self, ip, *, enabled=True, authenticated=False):
        with patch.object(security, "is_auth_enabled", return_value=enabled), \
             patch.object(security, "verify_auth", return_value={"allowed": authenticated}):
            return upnp_server.content_directory_allowed(ip, {})

    def test_control_points_need_an_open_window_and_a_private_address(self):
        self.assertFalse(self._allowed("192.168.1.40"))
        self.manager.grant_dlna_browse(30)
        for ip, expected in [
            ("192.168.1.40", True), ("10.0.0.8", True), ("fe80::1%eth0", True),
            ("8.8.8.8", False),              # public address
            ("100.100.1.2", False),          # Tailscale/CGNAT peers may be remote
            ("::ffff:8.8.8.8", False),       # mapped public IPv4
            ("not-an-ip", False),
        ]:
            with self.subTest(ip=ip):
                self.assertEqual(self._allowed(ip), expected)
        self.manager.revoke_dlna_browse()
        self.assertFalse(self._allowed("192.168.1.40"))

    def test_window_expires_and_credentials_still_work(self):
        self.manager.grant_dlna_browse(1)
        with patch.object(security.time, "time", return_value=time.time() + 61):
            self.assertFalse(self._allowed("192.168.1.40"))
        self.assertTrue(self._allowed("8.8.8.8", authenticated=True))
        self.assertTrue(self._allowed("127.0.0.1", enabled=False))

    def test_grant_is_bounded(self):
        expires_at = self.manager.grant_dlna_browse(10 ** 6)
        self.assertLessEqual(expires_at, time.time() + security.DLNA_BROWSE_MAX_MINUTES * 60 + 1)

    def test_browsed_tracks_get_scoped_tickets_not_api_keys(self):
        api_key = self.manager.generate_api_key()
        self.manager.grant_dlna_browse(120)
        track = {"id": 12, "album_id": 4, "title": "Act I", "duration": 3 * 3600, "format": "FLAC"}
        xml = upnp_server.track_xml(track, "al_4", upnp_server._browse_ticket_extra_seconds())
        self.assertNotIn(api_key, xml)
        self.assertNotIn("api_key", xml)
        stream_ticket = xml.split("/api/stream/12?ticket=", 1)[1].split("<", 1)[0]
        cover_ticket = xml.split("/thumb?size=medium&amp;ticket=", 1)[1].split("<", 1)[0]
        self.assertTrue(security.verify_media_ticket(stream_ticket, "stream", 12))
        self.assertFalse(security.verify_media_ticket(stream_ticket, "stream", 13))
        self.assertTrue(security.verify_media_ticket(cover_ticket, "cover", 4))
        # A three-hour work listed in a two-hour window stays playable to the end.
        expires_at = int(stream_ticket.split(".", 1)[0])
        self.assertGreaterEqual(expires_at, time.time() + 2 * 3600 + 3 * 3600)


class StreamSlotTests(unittest.TestCase):
    def test_limit_and_idempotent_release(self):
        slots = StreamSlots(2)
        first, second = slots.try_acquire(), slots.try_acquire()
        self.assertIsNone(slots.try_acquire())
        first.release()
        first.release()
        self.assertEqual(slots.active, 1)
        self.assertIsNotNone(slots.try_acquire())
        second.release()

    def test_unreleased_lease_is_returned_when_collected(self):
        slots = StreamSlots(1)
        slots.try_acquire()  # e.g. a response whose body was never iterated
        gc.collect()
        self.assertEqual(slots.active, 0)


class TranscodeLifecycleLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_close_runs_after_normal_completion(self):
        closed = []
        chunks = [chunk async for chunk in iter_ffmpeg_stdout(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok')"],
            timeout_seconds=10, on_close=lambda: closed.append(True))]
        self.assertEqual(b"".join(chunks), b"ok")
        self.assertEqual(closed, [True])

    async def test_stalled_decoder_is_ended_and_its_slot_released(self):
        spawned = []
        original = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            spawned.append(await original(*args, **kwargs))
            return spawned[-1]

        closed = []
        started = time.monotonic()
        with patch.object(asyncio, "create_subprocess_exec", side_effect=spawn):
            chunks = [chunk async for chunk in iter_ffmpeg_stdout(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_seconds=60, idle_timeout_seconds=0.5,
                on_close=lambda: closed.append(True))]
        self.assertEqual(chunks, [])
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(spawned[0].returncode)
        self.assertEqual(closed, [True])

    async def test_paused_client_does_not_count_as_a_stall(self):
        gen = iter_ffmpeg_stdout(
            [sys.executable, "-c",
             "import sys,time; sys.stdout.buffer.write(b'a'); sys.stdout.flush(); "
             "time.sleep(0.2); sys.stdout.buffer.write(b'b')"],
            timeout_seconds=10, idle_timeout_seconds=0.5, chunk_size=1)
        first = await gen.__anext__()
        await asyncio.sleep(1.0)  # consumer paused longer than the idle timeout
        rest = b"".join([chunk async for chunk in gen])
        self.assertEqual(first + rest, b"ab")


if __name__ == "__main__":
    unittest.main()
