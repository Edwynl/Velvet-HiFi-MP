"""Rapid track switches while every transcode slot is taken must not 503.

Only ``server`` internals that also exist before the fix are used, so this
file runs unchanged against the pre-fix code (red) and the fix (green):

    slot limit 2: a slow remote client holds one slot without reading, and the
    browser switches tracks five times in a row on the other.

Each switch drops the previous stream and immediately requests the next one,
which waits up to ``TRANSCODE_SLOT_WAIT_SECONDS`` for the dropped stream's
slot. FFmpeg is replaced by a Python process that writes PCM-sized blocks
forever, so no media files or FFmpeg build are needed.
"""
import asyncio
import ctypes
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route

import server
from audio_streaming import StreamSlots

ENDLESS_WRITER = (
    "import sys\n"
    "block = bytes(262144)\n"
    "while True:\n"
    "    sys.stdout.buffer.write(block)\n"
)
SWITCHES = 5


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return False


class RapidTrackSwitchUnderFullSlotsTests(unittest.TestCase):
    def setUp(self):
        self.pids = []
        real_spawn = asyncio.create_subprocess_exec

        async def fake_ffmpeg(*command, **kwargs):
            process = await real_spawn(sys.executable, "-c", ENDLESS_WRITER, **kwargs)
            self.pids.append(process.pid)
            return process

        for target in (patch.object(asyncio, "create_subprocess_exec", side_effect=fake_ffmpeg),
                       patch.object(server, "TRANSCODE_SLOTS", StreamSlots(2))):
            target.start()
            self.addCleanup(target.stop)

        async def stream(request):
            # The production DSP transcode path, slot handling included.
            return await server._stream_with_dsp("track.flac", "volume=1", 600)

        async def passthrough(request, call_next):
            return await call_next(request)

        app = Starlette(routes=[Route("/stream", stream)])
        # server.app has two @app.middleware("http") layers (auth, request id).
        app.add_middleware(BaseHTTPMiddleware, dispatch=passthrough)
        app.add_middleware(BaseHTTPMiddleware, dispatch=passthrough)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self.uvicorn = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=self.uvicorn.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not self.uvicorn.started:
            self.assertLess(time.monotonic(), deadline, "uvicorn did not start")
            time.sleep(0.02)

        def stop():
            self.uvicorn.should_exit = True
            thread.join(10)
            sock.close()

        self.addCleanup(stop)

    def _open(self):
        """Request a track; return (status, socket) once the body has started."""
        client = socket.create_connection(("127.0.0.1", self.port))
        client.settimeout(15)
        client.sendall(b"GET /stream HTTP/1.1\r\nHost: test\r\n\r\n")
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
        status = int(data.split(b" ", 2)[1]) if data else 0
        if status == 200:
            body = data.split(b"\r\n\r\n", 1)[1]
            while len(body) < 65536:  # FFmpeg is running and streaming
                body += client.recv(65536)
        return status, client

    def test_every_switch_gets_its_stream(self):
        remote_status, remote = self._open()  # slow remote client: holds a slot, stops reading
        self.assertEqual(remote_status, 200)
        status, browser = self._open()
        self.assertEqual(status, 200)
        statuses = []
        for _ in range(SWITCHES):
            browser.close()  # track switch: drop the old stream...
            status, browser = self._open()  # ...and request the next one at once
            statuses.append(status)
        browser.close()
        remote.close()
        self.assertEqual(statuses, [200] * SWITCHES, "track switch hit 503 under full slots")

        # Every decoder is gone and every slot is free once the clients leave.
        deadline = time.monotonic() + 5
        while (server.TRANSCODE_SLOTS.active or any(map(pid_alive, self.pids))) \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(server.TRANSCODE_SLOTS.active, 0)
        self.assertEqual([pid for pid in self.pids if pid_alive(pid)], [], "orphaned FFmpeg")


if __name__ == "__main__":
    unittest.main()
