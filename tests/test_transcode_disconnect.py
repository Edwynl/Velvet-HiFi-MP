"""A transcode slot must be freed promptly, exactly once, and only after FFmpeg exits.

A browser track switch drops the old stream and immediately requests the new
one, which waits at most ``TRANSCODE_SLOT_WAIT_SECONDS`` for a free slot. If
the old FFmpeg is only reaped when the garbage collector gets round to the
abandoned body generator, the new track fails with 503 under slot pressure.
"""
import asyncio
import ctypes
import gc
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
from starlette.requests import ClientDisconnect
from starlette.responses import Response
from starlette.routing import Route

import audio_streaming
import server
from audio_streaming import StreamSlots, TranscodeGuard, TranscodeStreamingResponse, iter_ffmpeg_stdout

# Writes PCM-sized blocks forever, like FFmpeg decoding a long track.
ENDLESS_WRITER = (
    "import sys\n"
    "block = bytes(262144)\n"
    "while True:\n"
    "    sys.stdout.buffer.write(block)\n"
)
# A short track: a few blocks, then a normal exit.
FINITE_WRITER = "import sys\nsys.stdout.buffer.write(bytes(262144 * 4))\n"


def pid_alive(pid: int) -> bool:
    """Ask the OS directly; asyncio's returncode is not proof the PID is gone."""
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers signal 0; reaped children do not.
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return False


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
    """Real subprocess, real TCP, the same middleware depth as server.app."""

    def setUp(self):
        self.slots = StreamSlots(1)
        self.released = threading.Event()
        self.releases = []  # (pid, pid still alive at release time)
        # Strong references to every guard and body generator: neither
        # refcount nor the cyclic GC can finalize them, so a release can only
        # come from the response path under test (gc.disable() would not stop
        # refcount finalization).
        self.kept = []
        self.writer = ENDLESS_WRITER

        async def stream(request):
            lease = self.slots.try_acquire()
            if lease is None:
                return Response(status_code=503)
            guard = None

            def on_exit():
                pid = guard.process.pid
                self.releases.append((pid, pid_alive(pid)))
                lease.release()
                self.released.set()

            guard = TranscodeGuard(on_exit)
            body = iter_ffmpeg_stdout([sys.executable, "-c", self.writer],
                                      timeout_seconds=60, guard=guard)
            self.kept.append((guard, body, lease))
            return TranscodeStreamingResponse(body, guard=guard, media_type="audio/wav")

        self.app = Starlette(routes=[Route("/stream", stream)])

        # server.app runs every request through two @app.middleware("http")
        # layers (auth, request id); they change how disconnects propagate.
        async def passthrough(request, call_next):
            return await call_next(request)

        self.app.add_middleware(BaseHTTPMiddleware, dispatch=passthrough)
        self.app.add_middleware(BaseHTTPMiddleware, dispatch=passthrough)

    def _open_stream(self, port, read_bytes):
        client = socket.create_connection(("127.0.0.1", port))
        client.sendall(b"GET /stream HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n")
        received = 0
        while received < read_bytes:
            chunk = client.recv(65536)
            self.assertTrue(chunk, "server closed the stream early")
            received += len(chunk)
        return client

    def _assert_released_once_after_exit(self):
        self.assertEqual(self.slots.active, 0)
        self.assertEqual(len(self.releases), 1, "slot released more than once")
        pid, alive_at_release = self.releases[0]
        self.assertFalse(alive_at_release, f"slot released while FFmpeg pid {pid} was running")
        self.assertFalse(pid_alive(pid))

    def _assert_prompt_release(self, read_bytes, pause_before_close):
        with _LiveServer(self.app) as live:
            client = self._open_stream(live.port, read_bytes)
            # Stop reading so the server's send() backs up behind the socket
            # buffer, as with a paused or throttled browser.
            time.sleep(pause_before_close)
            self.assertEqual(self.slots.active, 1)
            guard = self.kept[0][0]
            deadline = time.monotonic() + 5  # headers go out before FFmpeg is spawned
            while guard.process is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pid_alive(guard.process.pid))
            closed_at = time.monotonic()
            client.close()
            self.assertTrue(self.released.wait(5), "slot never released after disconnect")
            elapsed = time.monotonic() - closed_at
            self._assert_released_once_after_exit()
            self.assertLess(elapsed, 1.0, f"slot released {elapsed:.2f}s after disconnect")

    def test_release_when_client_hangs_up_while_send_is_blocked(self):
        self._assert_prompt_release(read_bytes=65536, pause_before_close=1.0)

    def test_release_when_client_hangs_up_right_after_headers(self):
        self._assert_prompt_release(read_bytes=1, pause_before_close=0.0)

    def test_complete_response_lifecycle_releases_once_after_exit(self):
        self.writer = FINITE_WRITER
        with _LiveServer(self.app) as live:
            client = self._open_stream(live.port, 1)
            while client.recv(65536):  # read the whole track to EOF
                pass
            client.close()
            self.assertTrue(self.released.wait(5))
            self._assert_released_once_after_exit()

    def test_slot_wait_window_covers_reap(self):
        # The reap itself may take up to the terminate grace; the new
        # track's wait must outlast it.
        self.assertGreater(server.TRANSCODE_SLOT_WAIT_SECONDS, audio_streaming.TERMINATE_GRACE_SECONDS)


def _fresh(error):
    # A new exception each time, like a real call: one stored instance would
    # keep every frame it was raised through (and the guard) alive.
    return type(error)(*error.args)


class FakeProcess:
    """Deterministic stand-in for an asyncio subprocess."""

    def __init__(self, *, exits_on_terminate=True, exits_on_kill=True, terminate_error=None,
                 kill_error=None, communicate_errors=(), communicate_hangs=False, wait_error=None):
        self.pid = 4242
        self.returncode = None
        self.calls = []
        self.exits_on_terminate = exits_on_terminate
        self.exits_on_kill = exits_on_kill
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.communicate_errors = list(communicate_errors)
        self.communicate_hangs = communicate_hangs
        self.wait_error = wait_error
        self._exited = asyncio.Event()
        self.stdout = self

    async def read(self, size):
        if self.returncode is not None:
            return b""
        await asyncio.sleep(0)
        return b"\0" * 16

    def exit(self, code):
        self.returncode = code
        self._exited.set()

    def terminate(self):
        self.calls.append("terminate")
        if self.terminate_error:
            raise _fresh(self.terminate_error)
        if self.exits_on_terminate:
            self.exit(-15)

    def kill(self):
        self.calls.append("kill")
        if self.kill_error:
            raise _fresh(self.kill_error)
        if self.exits_on_kill:
            self.exit(-9)

    async def communicate(self):
        self.calls.append("communicate")
        if self.communicate_errors:
            raise _fresh(self.communicate_errors.pop(0))
        if self.communicate_hangs:
            await asyncio.Event().wait()  # a stuck pipe, even after the kill
        await self._exited.wait()
        return b"", None

    async def wait(self):
        if self.wait_error:
            raise _fresh(self.wait_error)
        await self._exited.wait()
        return self.returncode


class _Body:
    """A response body whose aclose() fails, as seen with Starlette 0.50."""

    def __init__(self, error):
        self.error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        raise _fresh(self.error)


SCOPE = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "method": "GET",
         "path": "/stream", "headers": [], "query_string": b""}


async def _receive():
    await asyncio.Event().wait()


async def _send(message):
    pass


async def _receive_disconnect():
    await asyncio.sleep(0.01)
    return {"type": "http.disconnect"}


class _GuardStateIsolation(unittest.IsolatedAsyncioTestCase):
    """Held transcodes and exit monitors are module state: isolate each test."""

    async def asyncSetUp(self):
        audio_streaming._HELD_TRANSCODES.clear()

    async def asyncTearDown(self):
        for task in list(audio_streaming._CLEANUP_TASKS):
            task.cancel()
        await asyncio.gather(*audio_streaming._CLEANUP_TASKS, return_exceptions=True)
        audio_streaming._HELD_TRANSCODES.clear()

    async def wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "condition not reached")
            await asyncio.sleep(0.01)


class TranscodeGuardTests(_GuardStateIsolation):
    """Exception, cancellation and race paths, without real processes."""

    def setUp(self):
        for name, value in (("TERMINATE_GRACE_SECONDS", 0.05), ("KILL_GRACE_SECONDS", 0.05),
                            ("EXIT_POLL_SECONDS", 0.02)):
            patcher = patch.object(audio_streaming, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Fake PID 4242: the OS cannot vouch for it either way, so only the
        # fake's returncode decides whether it exited.
        patcher = patch.object(audio_streaming, "_os_process_exited", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.slots = StreamSlots(1)
        self.lease = self.slots.try_acquire()
        self.releases = []
        self.guard = TranscodeGuard(self._on_exit)

    def _on_exit(self):
        process = self.guard.process
        self.releases.append(None if process is None else process.returncode)
        self.lease.release()

    def assertReleasedOnceAfterExit(self):
        self.assertEqual(len(self.releases), 1, "slot released more than once")
        self.assertIsNotNone(self.releases[0], "slot released while FFmpeg was running")
        self.assertEqual(self.slots.active, 0)

    def _spawned(self, process):
        """The generator spawned ``process`` but its own cleanup never ran."""
        self.guard.process = process
        return process

    async def test_response_that_never_started_releases_without_a_decoder(self):
        async def send_fails(message):
            raise OSError("client gone before the response started")

        with patch.object(asyncio, "create_subprocess_exec", side_effect=AssertionError("no decoder")):
            body = iter_ffmpeg_stdout(["ffmpeg"], timeout_seconds=60, guard=self.guard)
            response = TranscodeStreamingResponse(body, guard=self.guard)
            with self.assertRaises((OSError, ClientDisconnect)):
                await response(SCOPE, _receive, send_fails)
        self.assertEqual(self.releases, [None])  # nothing was ever spawned
        self.assertEqual(self.slots.active, 0)

    async def test_aclose_failure_still_reaps_ffmpeg_then_releases(self):
        for error in (RuntimeError("aclose(): asynchronous generator is already running"),
                      OSError("pipe closed")):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                process = self._spawned(FakeProcess())
                response = TranscodeStreamingResponse(_Body(error), guard=self.guard)
                with self.assertLogs("velvet.audio_streaming", "WARNING"):
                    await response(SCOPE, _receive, _send)
                self.assertIn("terminate", process.calls)
                self.assertReleasedOnceAfterExit()

    async def test_cleanup_errors_fall_through_to_kill(self):
        for error in (OSError("access denied"), RuntimeError("transport closed")):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                process = self._spawned(FakeProcess(
                    exits_on_terminate=False, terminate_error=OSError("access denied"),
                    communicate_errors=[error]))
                await self.guard.finish()
                self.assertEqual(process.calls[:3], ["terminate", "communicate", "kill"])
                self.assertReleasedOnceAfterExit()

    async def test_communicate_after_kill_has_its_own_timeout(self):
        process = self._spawned(FakeProcess(exits_on_terminate=False, communicate_hangs=True))
        started = time.monotonic()
        await asyncio.wait_for(self.guard.finish(), 2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(process.calls, ["terminate", "communicate", "kill", "communicate"])
        self.assertReleasedOnceAfterExit()

    async def test_slot_is_held_while_ffmpeg_survives_kill(self):
        process = self._spawned(FakeProcess(exits_on_terminate=False, exits_on_kill=False,
                                            kill_error=PermissionError("denied")))
        with self.assertLogs("velvet.audio_streaming", "ERROR"):
            # Bounded: the request returns although FFmpeg will not die.
            await asyncio.wait_for(self.guard.finish(), 1)
        await asyncio.sleep(0.2)  # several exit-monitor polls
        self.assertEqual(self.releases, [], "slot released while FFmpeg was still running")
        self.assertEqual(self.slots.active, 1)
        self.assertIn(self.guard, audio_streaming._HELD_TRANSCODES)
        process.exit(1)
        await self.wait_until(lambda: self.releases)
        self.assertReleasedOnceAfterExit()
        self.assertNotIn(self.guard, audio_streaming._HELD_TRANSCODES)

    async def test_cancelled_cleanup_and_a_second_finish_release_once(self):
        process = self._spawned(FakeProcess(exits_on_terminate=False, exits_on_kill=False))
        first = asyncio.ensure_future(self.guard.finish())
        await asyncio.sleep(0.01)
        first.cancel()  # e.g. the request task is cancelled again mid-cleanup
        second = asyncio.ensure_future(self.guard.finish())
        with self.assertLogs("velvet.audio_streaming", "ERROR"):
            await asyncio.wait_for(second, 1)  # bounded, shares the first cleanup
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual(self.releases, [])
        process.exit(-9)
        await self.wait_until(lambda: self.releases)
        await self.guard.finish()  # later callers see the finished cleanup
        self.assertReleasedOnceAfterExit()

    async def test_finish_waits_for_a_spawn_in_flight(self):
        spawned = asyncio.Event()
        process = FakeProcess()

        async def slow_spawn(*args, **kwargs):
            await spawned.wait()
            return process

        with patch.object(asyncio, "create_subprocess_exec", side_effect=slow_spawn):
            body = iter_ffmpeg_stdout(["ffmpeg"], timeout_seconds=60, guard=self.guard)
            reading = asyncio.ensure_future(body.__anext__())
            await asyncio.sleep(0.01)
            # The response ends (client gone) while FFmpeg is still starting,
            # longer than the spawn grace: finish() returns, the slot is held.
            with self.assertLogs("velvet.audio_streaming", "ERROR"):
                await asyncio.wait_for(self.guard.finish(), 1)
            await asyncio.sleep(0.1)
            self.assertEqual(self.releases, [], "released before the spawned FFmpeg was reaped")
            spawned.set()
            await self.wait_until(lambda: self.releases)  # the monitor kills it, then releases
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
            await body.aclose()
        self.assertIn("kill", process.calls)
        self.assertReleasedOnceAfterExit()

    async def test_finish_waits_for_a_quick_spawn(self):
        spawned = asyncio.Event()
        process = FakeProcess()

        async def quick_spawn(*args, **kwargs):
            await spawned.wait()
            return process

        with patch.object(asyncio, "create_subprocess_exec", side_effect=quick_spawn):
            body = iter_ffmpeg_stdout(["ffmpeg"], timeout_seconds=60, guard=self.guard)
            reading = asyncio.ensure_future(body.__anext__())
            await asyncio.sleep(0.01)
            finishing = asyncio.ensure_future(self.guard.finish())
            await asyncio.sleep(0.01)
            spawned.set()  # within the spawn grace
            await asyncio.wait_for(finishing, 1)
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
            await body.aclose()
        self.assertIn("terminate", process.calls)
        self.assertReleasedOnceAfterExit()

    async def test_generator_and_response_cleanup_release_once(self):
        process = FakeProcess()
        with patch.object(asyncio, "create_subprocess_exec", return_value=process):
            body = iter_ffmpeg_stdout(["ffmpeg"], timeout_seconds=60, guard=self.guard)
            self.assertEqual(await body.__anext__(), b"\0" * 16)
            await body.aclose()  # the generator's own finally
            await self.guard.finish()  # the response's finally
            await self.guard.finish()
        self.assertReleasedOnceAfterExit()

    async def test_spawn_failure_releases(self):
        with patch.object(asyncio, "create_subprocess_exec", side_effect=FileNotFoundError("ffmpeg")):
            with self.assertLogs("velvet.audio_streaming", "ERROR"):
                async for _ in iter_ffmpeg_stdout(["ffmpeg"], timeout_seconds=60, guard=self.guard):
                    pass
        self.assertEqual(self.releases, [None])
        self.assertEqual(self.slots.active, 0)


class UnconfirmedExitTests(_GuardStateIsolation):
    """A slot whose FFmpeg exit cannot be confirmed must never be freed by GC."""

    def setUp(self):
        for name, value in (("TERMINATE_GRACE_SECONDS", 0.05), ("KILL_GRACE_SECONDS", 0.05),
                            ("EXIT_POLL_SECONDS", 0.02)):
            patcher = patch.object(audio_streaming, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Fake PID 4242 may exist on the test machine: report "still running"
        # unless a test says otherwise.
        self.os_exited = False
        patcher = patch.object(audio_streaming, "_os_process_exited", create=True,
                               side_effect=lambda pid: self.os_exited)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.slots = StreamSlots(1)

    def _unkillable(self):
        return FakeProcess(exits_on_terminate=False, exits_on_kill=False,
                           terminate_error=OSError("terminate failed"),
                           kill_error=OSError("kill failed"),
                           wait_error=RuntimeError("wait failed"))

    async def _finish_dropping_references(self, process):
        """Finish a transcode whose guard and lease nobody else references."""
        guard = TranscodeGuard(self.slots.try_acquire().release)
        guard.process = process
        # Not assertLogs: its captured tracebacks would keep the guard alive
        # and hide exactly the GC release this test is about.
        with patch.object(audio_streaming.log, "disabled", True):
            await asyncio.wait_for(guard.finish(), 2)  # bounded: the request does not hang
        del guard
        gc.collect()

    async def test_unconfirmed_exit_keeps_the_slot_even_when_references_drop(self):
        process = self._unkillable()
        await self._finish_dropping_references(process)
        await asyncio.sleep(0.2)  # several monitor polls
        gc.collect()
        self.assertEqual(self.slots.active, 1, "slot freed while FFmpeg exit was unconfirmed")

    async def test_monitor_releases_once_the_os_confirms_exit(self):
        process = self._unkillable()
        await self._finish_dropping_references(process)
        self.assertEqual(self.slots.active, 1)
        self.os_exited = True  # wait() keeps failing; the OS says the PID is gone
        for _ in range(100):
            if not self.slots.active:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.slots.active, 0)
        self.assertEqual(audio_streaming._HELD_TRANSCODES, set(), "released guard still held")

    async def test_monitor_retries_kill_until_ffmpeg_exits(self):
        process = FakeProcess(exits_on_terminate=False, kill_error=OSError("busy"))
        await self._finish_dropping_references(process)
        self.assertEqual(self.slots.active, 1)
        process.kill_error = None  # the next kill() attempt succeeds
        for _ in range(100):
            if not self.slots.active:
                break
            await asyncio.sleep(0.02)
        self.assertGreater(process.calls.count("kill"), 1)
        self.assertEqual(self.slots.active, 0)

    async def test_cancelled_aclose_still_reaps_and_releases(self):
        for spec in ("2.3", "2.4"):
            with self.subTest(spec_version=spec):
                lease = self.slots.try_acquire()
                guard = TranscodeGuard(lease.release)
                process = FakeProcess()
                guard.process = process
                response = TranscodeStreamingResponse(_Body(asyncio.CancelledError()), guard=guard)
                scope = {**SCOPE, "asgi": {"version": "3.0", "spec_version": spec}}
                with self.assertRaises(asyncio.CancelledError):  # cancellation still propagates
                    await response(scope, _receive_disconnect, _send)
                self.assertIn("terminate", process.calls)
                self.assertEqual(self.slots.active, 0)

    def test_slot_wait_window_covers_terminate_then_kill(self):
        # A decoder that ignores terminate() is killed after TERMINATE_GRACE;
        # the next track's slot wait must outlast that plus the kill itself.
        self.assertGreaterEqual(server.TRANSCODE_SLOT_WAIT_SECONDS,
                                audio_streaming.TERMINATE_GRACE_SECONDS + 1.0)


class OsExitCheckTests(unittest.TestCase):
    """The exit monitor's OS-level check, against a real child process."""

    def test_reports_running_then_exited(self):
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertIs(audio_streaming._os_process_exited(child.pid), False)
        finally:
            child.kill()
            child.wait(10)
        self.assertIs(audio_streaming._os_process_exited(child.pid), True)

    def test_unknown_pid_is_never_reported_running(self):
        self.assertIsNone(audio_streaming._os_process_exited(None))
        self.assertIsNone(audio_streaming._os_process_exited(-1))


class ServerUsesClosingResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcode_endpoints_use_the_guarded_response(self):
        for make in (lambda: server._stream_with_dsp("missing.flac", "volume=1"),
                     lambda: server._stream_upsampled("missing.flac", "96000")):
            slots = StreamSlots(1)
            with self.subTest(make=make), patch.object(server, "TRANSCODE_SLOTS", slots), \
                 patch.object(server, "ffmpeg_supports_soxr", return_value=True):
                response = await make()
                self.assertIsInstance(response, TranscodeStreamingResponse)
                self.assertEqual(slots.active, 1)
                async for _ in response.body_iterator:  # missing input: FFmpeg exits at once
                    pass
                self.assertEqual(slots.active, 0)


if __name__ == "__main__":
    unittest.main()
