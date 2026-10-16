"""FFmpeg streaming lifecycle management for browser playback.

All transcoding modes use this generator so disconnects, timeouts and early
client cancellation close the decoder instead of leaving background processes.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

log = logging.getLogger("velvet.audio_streaming")


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
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
