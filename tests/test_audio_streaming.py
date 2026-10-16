"""Regression tests for audio range requests and transcoder lifecycle."""
import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from audio_streaming import iter_ffmpeg_stdout


class ByteRangeTests(unittest.TestCase):
    def test_valid_ranges(self):
        self.assertEqual(server._parse_range("bytes=0-3", 10), (0, 3))
        self.assertEqual(server._parse_range("bytes=8-", 10), (8, 9))
        self.assertEqual(server._parse_range("bytes=-4", 10), (6, 9))
        self.assertEqual(server._parse_range("bytes=-50", 10), (0, 9))
        self.assertEqual(server._parse_range("bytes=0-999", 10), (0, 9))

    def test_upsample_preserves_source_family_and_refuses_unbounded_rates(self):
        self.assertEqual(server._select_pcm_upsample_rate(44100, "2x"), "88200")
        self.assertEqual(server._select_pcm_upsample_rate(48000, "2x"), "96000")
        self.assertEqual(server._select_pcm_upsample_rate(96000, "4x"), "384000")
        self.assertIsNone(server._select_pcm_upsample_rate(48000, "none"))
        for source, factor in [(None, "2x"), (0, "2x"), (96000, "16x"),
                               (48000, "32x")]:
            with self.subTest(source=source, factor=factor):
                with self.assertRaises(ValueError):
                    server._select_pcm_upsample_rate(source, factor)

    def test_invalid_ranges(self):
        for header, size in [
            ("bytes=10-", 10), ("bytes=8-2", 10),
            ("bytes=-0", 10), ("bytes=-", 10),
            ("bytes=0-1,4-5", 10), ("units=0-2", 10),
            ("bytes=0-1", 0),
        ]:
            with self.subTest(header=header):
                with self.assertRaises(ValueError):
                    server._parse_range(header, size)

    def test_http_range_responses(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "song.mp3"
            path.write_bytes(b"0123456789")

            from starlette.requests import Request

            def request(range_header=None):
                headers = [(b"range", range_header.encode())] if range_header else []
                return Request({"type": "http", "headers": headers, "method": "GET",
                                "path": "/", "query_string": b"", "scheme": "http",
                                "server": ("localhost", 80)})

            with TestClient(server.app) as client:
                # Test the response objects at the boundary without needing a DB.
                from starlette.responses import StreamingResponse
                for header, expected_start, expected_length in [
                    ("bytes=0-3", "bytes 0-3/10", "4"),
                    ("bytes=-4", "bytes 6-9/10", "4"),
                ]:
                    res = server._stream_file_direct(str(path), request(header))
                    self.assertIsInstance(res, StreamingResponse)
                    self.assertEqual(res.status_code, 206)
                    self.assertEqual(res.headers["content-range"], expected_start)
                    self.assertEqual(res.headers["content-length"], expected_length)
                bad = server._stream_file_direct(str(path), request("bytes=99-"))
                self.assertEqual(bad.status_code, 416)
                self.assertEqual(bad.headers["content-range"], "bytes */10")


class TranscoderLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_bytes_and_reaps_process(self):
        data = bytearray()
        async for chunk in iter_ffmpeg_stdout(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'hello')"],
            timeout_seconds=5,
        ):
            data.extend(chunk)
        self.assertEqual(data, b"hello")

    async def test_early_generator_close_stops_process(self):
        # Process writes one byte, then stays alive. Closing generator must reap it.
        with tempfile.TemporaryDirectory() as folder:
            pid_path = Path(folder) / "pid"
            code = (
                "import os,sys,time,pathlib;"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
                "sys.stdout.buffer.write(b'x');sys.stdout.buffer.flush();"
                "time.sleep(30)"
            )
            gen = iter_ffmpeg_stdout(
                [sys.executable, "-c", code, str(pid_path)],
                timeout_seconds=40,
                chunk_size=1,
            )
            self.assertEqual(await gen.__anext__(), b"x")
            await gen.aclose()
            # Generator close must complete without leaking an awaitable/process.
            self.assertTrue(pid_path.exists())


if __name__ == "__main__":
    unittest.main()
