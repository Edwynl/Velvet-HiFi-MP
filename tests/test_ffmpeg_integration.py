"""Exercise real codecs, not just command-line mocks; no audio hardware used."""
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
from starlette.responses import StreamingResponse

import server
from audio_streaming import iter_ffmpeg_stdout
from dsp_engine import build_dsp_filter_chain


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
class FFmpegIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_wav_flac_and_mp3_transcodes(self):
        with tempfile.TemporaryDirectory() as folder:
            for rate, depth, extension in [(44100, 16, "flac"), (48000, 24, "flac"),
                                           (44100, 24, "flac"), (48000, 16, "mp3")]:
                source = Path(folder) / f"source-{rate}-{depth}.{extension}"
                command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                           f"sine=frequency=1000:sample_rate={rate}:duration=0.1"]
                if extension == "flac":
                    command += ["-c:a", "flac", "-sample_fmt", "s32" if depth == 24 else "s16"]
                subprocess.run(command + [str(source)], check=True, capture_output=True)
                dsp = build_dsp_filter_chain({"preamp_db": -3, "stereo_width": 20,
                    "filter_chain_json": '[{"type":"peaking","freq":1000,"gain":-2,"q":1}]'})
                for quality in server.UPSAMPLE_QUALITY:
                    with self.subTest(rate=rate, depth=depth, extension=extension, quality=quality):
                        response = await server._stream_upsampled(str(source), str(rate * 2), quality,
                                                                  dsp_filter=dsp)
                        data = b"".join([block async for block in response.body_iterator])
                        self.assertEqual(response.media_type, "audio/wav")
                        self.assertEqual(data[:4], b"RIFF")
                        self.assertEqual(data[8:12], b"WAVE")
                        output = Path(folder) / "output.wav"
                        output.write_bytes(data)
                        probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                                                "-of", "json", str(output)], check=True, capture_output=True)
                        stream = json.loads(probe.stdout)["streams"][0]
                        self.assertEqual(int(stream["sample_rate"]), rate * 2)
                        self.assertEqual(stream["codec_name"], "pcm_s32le")
                response = await server._stream_with_dsp(str(source), dsp)
                data = b"".join([block async for block in response.body_iterator])
                self.assertEqual(response.media_type, "audio/flac")
                self.assertEqual(data[:4], b"fLaC")


class DisconnectIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_soxr_fails_before_http_stream_starts(self):
        with patch.object(server, "ffmpeg_supports_soxr", return_value=False):
            with self.assertRaises(server.HTTPException) as raised:
                await server._stream_upsampled("unused.flac", "96000")
            self.assertEqual(raised.exception.status_code, 503)

    async def test_asgi_disconnect_reaps_live_decoder(self):
        processes = []
        original_spawn = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            processes.append(process)
            return process
        body_sent = asyncio.Event()
        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                body_sent.set()
        async def receive():
            await body_sent.wait()
            return {"type": "http.disconnect"}
        code = "import sys,time;sys.stdout.buffer.write(b'x');sys.stdout.buffer.flush();time.sleep(30)"
        response = StreamingResponse(iter_ffmpeg_stdout([sys.executable, "-c", code], timeout_seconds=40))
        with patch("audio_streaming.asyncio.create_subprocess_exec", side_effect=spawn):
            await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send), 5)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)

    async def test_cleanup_survives_level_cancellation(self):
        class Process:
            returncode = None
            class Stdout:
                async def read(self, size):
                    return b"x"
            stdout = Stdout()
            def terminate(self):
                pass
            async def communicate(self):
                await asyncio.sleep(0.01)
                self.returncode = 0
        process = Process()
        with patch("audio_streaming.asyncio.create_subprocess_exec", return_value=process):
            generator = iter_ffmpeg_stdout(["unused"], timeout_seconds=5)
            await anext(generator)
            with anyio.CancelScope() as scope:
                scope.cancel()
                await generator.aclose()
            self.assertEqual(process.returncode, 0)
