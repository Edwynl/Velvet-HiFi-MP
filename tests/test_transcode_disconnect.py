"""A transcode slot must be freed promptly when the client hangs up.

A browser track switch drops the old stream and immediately requests the new
one, which waits at most ``TRANSCODE_SLOT_WAIT_SECONDS`` for a free slot. If
the old FFmpeg is only reaped when the garbage collector gets round to the
abandoned body generator, the new track fails with 503 under slot pressure.
"""
import gc
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.routing import Route

import server
from audio_streaming import StreamSlots, TranscodeStreamingResponse, iter_ffmpeg_stdout

# Writes PCM-sized blocks forever, like FFmpeg decoding a long track.
ENDLESS_WRITER = (
    "import sys\n"
    "block = bytes(262144)\n"
    "while True:\n"
    "    sys.stdout.buffer.write(block)\n"
)


class _LiveServer:
    """Real uvicorn on a loopback ephemeral port, so disconnects are real TCP."""

    def __init__(self, app):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()


class SlotReleaseOnDisconnectTests(unittest.TestCase):
    def setUp(self):
        # Keep the cyclic GC from rescuing an orphaned generator by luck:
        # release has to be driven by the response, not by finalizers.
        gc.disable()
        self.addCleanup(gc.enable)
        self.slots = StreamSlots(1)
        self.released = threading.Event()

        async def stream(request):
            lease = self.slots.try_acquire()
            if lease is None:
                return Response(status_code=503)

            def on_close():
                lease.release()
                self.released.set()

            return TranscodeStreamingResponse(
                iter_ffmpeg_stdout([sys.executable, "-c", ENDLESS_WRITER],
                                   timeout_seconds=60, on_close=on_close),
                media_type="audio/wav",
            )

        self.app = Starlette(routes=[Route("/stream", stream)])

        # server.app runs every request through two @app.middleware("http")
        # layers (auth, request id); they change how disconnects propagate.
        async def passthrough(request, call_next):
            return await call_next(request)

        self.app.add_middleware(BaseHTTPMiddleware, dispatch=passthrough)
        self.app.add_middleware(BaseHTTPMiddleware, dispatch=passthrough)

    def _open_stream(self, port, read_bytes):
        client = socket.create_connection(("127.0.0.1", port))
        client.sendall(b"GET /stream HTTP/1.1\r\nHost: test\r\n\r\n")
        received = 0
        while received < read_bytes:
            chunk = client.recv(65536)
            self.assertTrue(chunk, "server closed the stream early")
            received += len(chunk)
        return client

    def _assert_prompt_release(self, read_bytes, pause_before_close):
        with _LiveServer(self.app) as live:
            client = self._open_stream(live.port, read_bytes)
            # Stop reading so the server's send() backs up behind the socket
            # buffer, as with a paused or throttled browser.
            time.sleep(pause_before_close)
            self.assertEqual(self.slots.active, 1)
            closed_at = time.monotonic()
            client.close()
            self.assertTrue(self.released.wait(5), "slot never released after disconnect")
            elapsed = time.monotonic() - closed_at
            self.assertEqual(self.slots.active, 0)
            self.assertLess(elapsed, 1.0, f"slot released {elapsed:.2f}s after disconnect")

    def test_release_when_client_hangs_up_while_send_is_blocked(self):
        self._assert_prompt_release(read_bytes=65536, pause_before_close=1.0)

    def test_release_when_client_hangs_up_right_after_headers(self):
        self._assert_prompt_release(read_bytes=1, pause_before_close=0.0)

    def test_slot_wait_window_covers_reap(self):
        # The reap itself may take up to the 2 s terminate grace; the new
        # track's wait must outlast it.
        self.assertGreater(server.TRANSCODE_SLOT_WAIT_SECONDS, 2.0)


class ServerUsesClosingResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcode_endpoint_uses_closing_response(self):
        slots = StreamSlots(1)
        with patch.object(server, "TRANSCODE_SLOTS", slots):
            response = await server._stream_with_dsp("missing.flac", "volume=1")
            self.assertIsInstance(response, TranscodeStreamingResponse)
            async for _ in response.body_iterator:  # missing input: FFmpeg exits at once
                pass
        self.assertEqual(slots.active, 0)


if __name__ == "__main__":
    unittest.main()
