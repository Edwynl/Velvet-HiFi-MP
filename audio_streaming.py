"""FFmpeg streaming lifecycle management for browser playback.

All transcoding modes use this generator so disconnects, timeouts and early
client cancellation close the decoder instead of leaving background processes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import anyio
import shutil
import subprocess
import threading
from functools import lru_cache
from collections.abc import AsyncIterator, Callable, Sequence

from starlette.responses import StreamingResponse

log = logging.getLogger("velvet.audio_streaming")


@lru_cache(maxsize=1)
def ffmpeg_supports_soxr() -> bool:
    """Probe once per server run; Windows essentials builds omit libsoxr."""
    executable = shutil.which("ffmpeg")
    if not executable:
        return False
    try:
        result = subprocess.run([
            executable, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=stereo", "-t", "0.01", "-af",
            "aresample=resampler=soxr", "-f", "null", "-",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class StreamLease:
    """One occupied transcode slot. Release is idempotent and also runs on GC,
    so a response whose body was never iterated cannot leak its slot."""

    def __init__(self, slots: "StreamSlots"):
        self._slots = slots
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._slots._release()

    def __del__(self):
        self.release()


class StreamSlots:
    """Bound the number of simultaneous FFmpeg transcodes."""

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> StreamLease | None:
        with self._lock:
            if self._active >= self.limit:
                return None
            self._active += 1
        return StreamLease(self)

    def _release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)


# FFmpeg gets this long to exit after terminate(), then again after kill().
# The terminate grace plus the kill itself must fit inside the server's
# TRANSCODE_SLOT_WAIT_SECONDS, so a decoder that ignores terminate() is still
# gone before the next track's request gives up on its slot. KILL_GRACE only
# bounds how long a request waits for a decoder that survives kill(); that
# slot then stays held by the exit monitor below, not by the request.
TERMINATE_GRACE_SECONDS = 1.0
KILL_GRACE_SECONDS = 5.0
# How often the exit monitor re-checks (and re-kills) a decoder that outlived kill().
EXIT_POLL_SECONDS = 1.0
# Strong references for cleanup tasks that must outlive a cancelled request.
_CLEANUP_TASKS: set[asyncio.Future] = set()
# Transcodes whose FFmpeg exit is not confirmed. Holding the guard keeps its
# lease referenced, so StreamLease.__del__ cannot free the slot while the
# decoder may still run; the exit monitor drops it once the exit is confirmed.
_HELD_TRANSCODES: set["TranscodeGuard"] = set()


def _keep_alive(task: asyncio.Future) -> asyncio.Future:
    _CLEANUP_TASKS.add(task)
    task.add_done_callback(_CLEANUP_TASKS.discard)
    return task


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def _os_process_exited(pid) -> bool | None:
    """Ask the OS whether ``pid`` has exited: True, False, or None if unknown."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        # asyncio holds the process handle while we hold the process object,
        # so Windows cannot reuse this PID for another process meanwhile.
        handle = _kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return True if ctypes.get_last_error() == 87 else None  # 87: no such process
        try:
            state = _kernel32.WaitForSingleObject(handle, 0)
        finally:
            _kernel32.CloseHandle(handle)
        return {0: True, 0x102: False}.get(state)  # WAIT_OBJECT_0 / WAIT_TIMEOUT
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return None
    return False  # still running, or a zombie asyncio has not reaped yet


def _send_signal(process, method: str, *, quiet: bool = False) -> None:
    try:
        getattr(process, method)()
    except ProcessLookupError:
        pass  # already gone
    except OSError:
        # e.g. Windows access denied on a process that is just exiting.
        log.log(logging.DEBUG if quiet else logging.WARNING, "FFmpeg %s() failed", method,
                exc_info=True)


async def _drain(process, timeout: float) -> None:
    """Close the pipes and wait for exit, bounded by ``timeout``.

    wait() alone can hang when the child's stdout StreamReader is full.
    """
    try:
        await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        pass
    except Exception:
        log.warning("FFmpeg cleanup failed", exc_info=True)


async def _reap(process) -> bool:
    """Terminate, then kill, FFmpeg; return True once it has really exited."""
    if process.returncode is None:
        _send_signal(process, "terminate")
    await _drain(process, TERMINATE_GRACE_SECONDS)
    if process.returncode is None:
        _send_signal(process, "kill")
        await _drain(process, KILL_GRACE_SECONDS)
    return process.returncode is not None


class TranscodeGuard:
    """Owns one transcode: its FFmpeg process and the slot it occupies.

    ``finish()`` reaps FFmpeg and calls ``on_exit`` (the slot release) exactly
    once, and only after the process has exited or when none was started.
    The body generator and the response both call it, so release depends
    neither on which side ends first, on ``aclose()`` succeeding, nor on
    garbage collection.

    ``finish()`` is bounded (spawn, terminate and kill graces), so a request
    never hangs on a decoder that will not die. When the exit cannot be
    confirmed, the guard moves to ``_HELD_TRANSCODES`` and an exit monitor
    keeps re-killing and re-checking with the OS; the slot is released only
    once the exit is confirmed, however many references the request dropped.
    """

    def __init__(self, on_exit: Callable[[], None] | None = None):
        self._on_exit = on_exit
        self._spawning: asyncio.Future | None = None
        self._finishing: asyncio.Future | None = None
        self._released = False
        self.process = None

    @property
    def closing(self) -> bool:
        return self._finishing is not None

    async def spawn(self, command: Sequence[str]):
        # Its own task, so a request cancelled mid-spawn cannot orphan a
        # process that finish() never sees.
        self._spawning = _keep_alive(asyncio.ensure_future(asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )))
        self.process = await asyncio.shield(self._spawning)
        return self.process

    async def finish(self) -> None:
        if self._finishing is None:
            self._finishing = _keep_alive(asyncio.ensure_future(self._finish()))
        with anyio.CancelScope(shield=True):
            await asyncio.shield(self._finishing)

    async def _finish(self) -> None:
        if self._spawning is not None and not self._spawning.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._spawning), KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                self._hold("still starting")
                return
            except Exception:
                pass  # spawn failed: the exit check below sees no decoder
        # Already gone (or never started), reaped now, or confirmed by the OS.
        if self._exit_confirmed() or await _reap(self.process) or self._exit_confirmed():
            self._release()
        else:
            self._hold("survived kill()")

    def _exit_confirmed(self) -> bool:
        spawning = self._spawning
        if spawning is not None:
            if not spawning.done():
                return False
            if spawning.cancelled() or spawning.exception() is not None:
                return True  # no decoder was ever started
            self.process = spawning.result()
        process = self.process
        if process is None:
            return True
        if process.returncode is not None:
            return True
        return _os_process_exited(getattr(process, "pid", None)) is True

    def _hold(self, reason: str) -> None:
        log.error("FFmpeg pid %s %s; its transcode slot stays held until it exits",
                  getattr(self.process, "pid", "?"), reason)
        _HELD_TRANSCODES.add(self)
        _keep_alive(asyncio.ensure_future(self._monitor_exit()))

    async def _monitor_exit(self) -> None:
        while not self._released:
            await asyncio.sleep(EXIT_POLL_SECONDS)
            try:
                if self._exit_confirmed():
                    log.warning("Held FFmpeg pid %s has exited; releasing its slot",
                                getattr(self.process, "pid", "?"))
                    self._release()
                elif self.process is not None:
                    _send_signal(self.process, "kill", quiet=True)
            except Exception:
                log.exception("FFmpeg exit check failed; its slot stays held")

    def _release(self) -> None:
        _HELD_TRANSCODES.discard(self)
        if self._released:
            return
        self._released = True
        if self._on_exit is not None:
            self._on_exit()


async def iter_ffmpeg_stdout(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    chunk_size: int = 262144,
    idle_timeout_seconds: float = 120,
    on_close: Callable[[], None] | None = None,
    guard: TranscodeGuard | None = None,
) -> AsyncIterator[bytes]:
    """Yield encoded audio bytes; reap the FFmpeg subprocess on every exit path.

    ``timeout_seconds`` is an absolute wall-clock cap on the whole response:
    time a paused or slow client spends not reading DOES count toward it.
    ``idle_timeout_seconds`` separately ends a decoder that produces no output;
    only that idle check ignores time spent waiting for the client.
    ``on_close`` (or ``guard``'s ``on_exit``) runs once the process has exited.

    Raises on cancellation so Starlette can release response resources.
    On a transcode failure the HTTP response may have already started, so the
    non-zero exit status is logged for diagnosis rather than raising mid-body.
    """
    if timeout_seconds <= 0 or idle_timeout_seconds <= 0:
        raise ValueError("timeouts must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if guard is None:
        guard = TranscodeGuard(on_close)

    try:
        if guard.closing:
            return  # the response already ended; never start a decoder
        process = await guard.spawn(command)
        # Enforce the deadline per read. A timeout context spanning `yield`
        # would deliver its cancellation to whatever the consumer is awaiting
        # (e.g. Starlette's send()) while the generator is suspended.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        # Returning lets the finally block terminate the decoder.
        while True:
            wait = min(idle_timeout_seconds, deadline - loop.time())
            if wait <= 0:
                log.warning("FFmpeg audio stream exceeded its time limit")
                return
            try:
                block = await asyncio.wait_for(process.stdout.read(chunk_size), wait)
            except asyncio.TimeoutError:
                if wait < idle_timeout_seconds:
                    log.warning("FFmpeg audio stream exceeded its time limit")
                else:
                    log.warning("FFmpeg produced no audio for %ss", idle_timeout_seconds)
                return
            if not block:
                break
            yield block
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            return
        if process.returncode:
            log.warning("FFmpeg exited with code %s", process.returncode)
    except asyncio.CancelledError:
        # A browser disconnect typically cancels a StreamingResponse task.
        raise
    except Exception:
        log.exception("FFmpeg audio stream failed")
    finally:
        # Starlette disconnects cancel an AnyIO task group; finish() shields
        # the reap from repeated cancellation during cleanup.
        await guard.finish()


class TranscodeStreamingResponse(StreamingResponse):
    """StreamingResponse that reaps its transcode as soon as it ends.

    On a client disconnect Starlette cancels the send loop while the body
    generator is parked at ``yield``, and leaves the generator for the garbage
    collector to finalize. Behind ``@app.middleware("http")`` layers that can
    take seconds, holding FFmpeg and its transcode slot long enough for the
    next track's request to 503. Closing the body here reaps the decoder at
    once; ``guard.finish()`` then releases the slot even if ``aclose()``
    raised or the body was never started (no decoder).
    """

    def __init__(self, content, *args, guard: TranscodeGuard, **kwargs):
        super().__init__(content, *args, **kwargs)
        self._guard = guard

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    aclose = getattr(self.body_iterator, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception:
                            log.warning("Closing the transcode body failed", exc_info=True)
                finally:
                    # Also when aclose() raised CancelledError (not an
                    # Exception): reap first, then let the cancellation propagate.
                    await self._guard.finish()
