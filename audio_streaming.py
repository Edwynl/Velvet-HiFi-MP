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
from functools import lru_cache
from collections.abc import AsyncIterator, Sequence

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


async def iter_ffmpeg_stdout(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    chunk_size: int = 262144,
) -> AsyncIterator[bytes]:
    """Yield encoded audio bytes; reap the FFmpeg subprocess on every exit path.

    Raises on cancellation so Starlette can release response resources.
    On a transcode failure the HTTP response may have already started, so the
    non-zero exit status is logged for diagnosis rather than raising mid-body.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(timeout_seconds):
            while True:
                block = await process.stdout.read(chunk_size)
                if not block:
                    break
                yield block
            await process.wait()
        if process.returncode:
            log.warning("FFmpeg exited with code %s", process.returncode)
    except asyncio.TimeoutError:
        log.warning("FFmpeg audio stream exceeded its time limit")
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
