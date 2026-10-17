"""FFmpeg streaming lifecycle management for browser playback.

All transcoding modes use this generator so disconnects, timeouts and early
client cancellation close the decoder instead of leaving background processes.
"""
from __future__ import annotations

import asyncio
import logging
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
TERMINATE_GRACE_SECONDS = 2.0
KILL_GRACE_SECONDS = 5.0
# Strong references for cleanup tasks that must outlive a cancelled request.
_CLEANUP_TASKS: set[asyncio.Future] = set()


def _keep_alive(task: asyncio.Future) -> asyncio.Future:
    _CLEANUP_TASKS.add(task)
    task.add_done_callback(_CLEANUP_TASKS.discard)
    return task


def _send_signal(process, method: str) -> None:
    try:
        getattr(process, method)()
    except ProcessLookupError:
        pass  # already gone
    except OSError:
        # e.g. Windows access denied on a process that is just exiting.
        log.warning("FFmpeg %s() failed", method, exc_info=True)


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
    garbage collection. If FFmpeg survives kill() the slot stays held and a
    background task releases it when the process finally exits.
    """

    def __init__(self, on_exit: Callable[[], None] | None = None):
        self._on_exit = on_exit
        self._spawning: asyncio.Future | None = None
        self._finishing: asyncio.Future | None = None
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
        if self._spawning is not None:
            try:
                self.process = await self._spawning
            except Exception:
                self.process = None  # spawn failed: nothing to reap
        process = self.process
        if process is not None and not await _reap(process):
            log.error("FFmpeg pid %s survived kill(); its slot is held until it exits",
                      getattr(process, "pid", "?"))
            try:
                await process.wait()
            except Exception:
                log.exception("Waiting for FFmpeg to exit failed")
            if process.returncode is None:
                return  # never free a slot for a decoder that may still run
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
                aclose = getattr(self.body_iterator, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        log.warning("Closing the transcode body failed", exc_info=True)
                await self._guard.finish()
