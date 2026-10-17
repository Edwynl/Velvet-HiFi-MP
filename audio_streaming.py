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


async def iter_ffmpeg_stdout(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    chunk_size: int = 262144,
    idle_timeout_seconds: float = 120,
    on_close: Callable[[], None] | None = None,
) -> AsyncIterator[bytes]:
    """Yield encoded audio bytes; reap the FFmpeg subprocess on every exit path.

    ``timeout_seconds`` is an absolute wall-clock cap on the whole response:
    time a paused or slow client spends not reading DOES count toward it.
    ``idle_timeout_seconds`` separately ends a decoder that produces no output;
    only that idle check ignores time spent waiting for the client.
    ``on_close`` runs once the process has been reaped.

    Raises on cancellation so Starlette can release response resources.
    On a transcode failure the HTTP response may have already started, so the
    non-zero exit status is logged for diagnosis rather than raising mid-body.
    """
    if timeout_seconds <= 0 or idle_timeout_seconds <= 0:
        raise ValueError("timeouts must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
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
        if process is not None:
            # Starlette disconnects cancel an AnyIO task group. Shield the
            # reap operation from repeated cancellation during cleanup.
            with anyio.CancelScope(shield=True):
                if process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    # Drain the pipe as well: wait() alone can hang when a
                    # terminated child's stdout StreamReader is full.
                    await asyncio.wait_for(process.communicate(), timeout=2)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.communicate()
        if on_close is not None:
            on_close()


class TranscodeStreamingResponse(StreamingResponse):
    """StreamingResponse that closes its body generator as soon as it ends.

    On a client disconnect Starlette cancels the send loop while the body
    generator is parked at ``yield``, and leaves the generator for the garbage
    collector to finalize. Behind ``@app.middleware("http")`` layers that can
    take seconds, holding FFmpeg and its transcode slot long enough for the
    next track's request to 503. Closing it here reaps the decoder at once.
    ``on_close`` then runs even if the body was never started (no decoder).
    """

    def __init__(self, content, *args, on_close: Callable[[], None] | None = None, **kwargs):
        super().__init__(content, *args, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                aclose = getattr(self.body_iterator, "aclose", None)
                if aclose is not None:
                    await aclose()
            if self._on_close is not None:
                self._on_close()
