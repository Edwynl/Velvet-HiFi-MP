"""Exercise real codecs, not just command-line mocks; no audio hardware used."""
import array
import asyncio
import json
import math
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
from dsp_engine import build_dsp_filter_chain, read_replaygain_tags, resolve_replaygain


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
                response = await server._stream_with_dsp(str(source), dsp, start_seconds=0.02, container="wav")
                data = b"".join([block async for block in response.body_iterator])
                self.assertEqual(response.media_type, "audio/wav")
                output = Path(folder) / "compatible.wav"
                output.write_bytes(data)
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)], check=True, capture_output=True)
                stream = json.loads(probe.stdout)["streams"][0]
                self.assertEqual(stream["codec_name"], "pcm_s24le")
                self.assertEqual(int(stream["sample_rate"]), rate)

    async def test_dsp_stream_for_a_renderer_resumes_as_24_bit_flac(self):
        # EQ-3: what a DLNA renderer receives (and its DIDL claims): 24-bit
        # FLAC at the source rate, starting at the re-cast position.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.flac"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=1000:sample_rate=44100:duration=2",
                            "-c:a", "flac", "-sample_fmt", "s16", str(source)],
                           check=True, capture_output=True)
            dsp = build_dsp_filter_chain({"preamp_db": -3, "filter_chain_json":
                '[{"type":"peaking","freq":1000,"gain":-2,"q":1}]'}, 44100)
            response = await server._stream_with_dsp(str(source), dsp, start_seconds=1.5)
            output = Path(folder) / "output.flac"
            output.write_bytes(b"".join([block async for block in response.body_iterator]))
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                                    "-of", "json", str(output)], check=True, capture_output=True)
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(int(stream["sample_rate"]), 44100)
            self.assertEqual(int(stream["bits_per_raw_sample"]),
                             server.upnp_server.TRANSCODED_BITS_PER_SAMPLE)
            # A piped FLAC header carries no length: count the decoded samples.
            pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "s16le",
                                  "-ac", "1", "pipe:1"], check=True, capture_output=True).stdout
            self.assertAlmostEqual(len(pcm) / 2 / 44100, 0.5, delta=0.02)

    async def test_dsp_stream_resumes_where_the_first_decoded_frame_is_tiny(self):
        # An FFmpeg-made FLAC has 1024-sample blocks; seeking to 15 s lands
        # 4 samples before a block edge. The FLAC encoder must not take its
        # block size from that first frame ("invalid block size: 4", empty 200).
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.flac"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=44100:duration=16",
                            "-ac", "2", "-c:a", "flac", "-frame_size", "1024", str(source)],
                           check=True, capture_output=True)
            dsp = build_dsp_filter_chain({"preamp_db": -1}, 44100)
            response = await server._stream_with_dsp(str(source), dsp, start_seconds=15)
            output = Path(folder) / "output.flac"
            output.write_bytes(b"".join([block async for block in response.body_iterator]))
            self.assertEqual(output.read_bytes()[:4], b"fLaC")
            pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "s16le",
                                  "-ac", "1", "pipe:1"], check=True, capture_output=True).stdout
            self.assertEqual(len(pcm) // 2, 44100)

    async def test_replaygain_transcode_applies_the_gain_once_and_drops_gain_tags(self):
        # The gain goes into the samples; the source's ReplayGain/R128 tags
        # must not reach the output, or a renderer could apply them again.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "tagged.flac"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=1000:sample_rate=44100:duration=2", "-ac", "2",
                            "-metadata", "REPLAYGAIN_TRACK_GAIN=-6.00 dB",
                            "-metadata", "REPLAYGAIN_TRACK_PEAK=0.5",
                            "-metadata", "R128_TRACK_GAIN=-1536", str(source)],
                           check=True, capture_output=True)
            replaygain = resolve_replaygain(read_replaygain_tags(source), "track")
            self.assertEqual(replaygain["applied_db"], -6.0)
            dsp = build_dsp_filter_chain(None, 44100, replaygain["applied_db"])
            response = await server._stream_with_dsp(str(source), dsp)
            output = Path(folder) / "output.flac"
            output.write_bytes(b"".join([block async for block in response.body_iterator]))
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags",
                                    "-of", "json", str(output)], check=True, capture_output=True)
            tags = json.loads(probe.stdout)
            names = {key.upper() for section in [tags.get("format", {}), *tags.get("streams", [])]
                     for key in section.get("tags", {})}
            self.assertFalse({name for name in names if "REPLAYGAIN" in name or "R128" in name}, names)

            def rms(path):
                pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f64le",
                                      "-ac", "1", "pipe:1"], check=True, capture_output=True).stdout
                samples = array.array("d", pcm)
                return math.sqrt(sum(s * s for s in samples) / len(samples))

            self.assertAlmostEqual(20 * math.log10(rms(output) / rms(source)), -6.0, delta=0.01)


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
