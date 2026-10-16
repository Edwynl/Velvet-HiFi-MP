"""Native sounddevice command and float32 sample-format regressions."""
import io
import tempfile
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy

import local_playback


class _FakeProcess:
    def __init__(self):
        self.stdout = io.BytesIO(numpy.asarray([[0.25, -0.25]], dtype="<f4").tobytes())
    def poll(self):
        return 0


class _FakeStream:
    callback_result = None

    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.samplerate = kwargs["samplerate"]
        self.dtype = kwargs["dtype"]

    def start(self):
        output = numpy.zeros((1, 2), dtype="float32")
        self.callback(output, 1, None, None)
        _FakeStream.callback_result = output

    def stop(self):
        pass

    def close(self):
        pass


class NativeAudioTests(unittest.TestCase):
    def test_shared_device_rate_is_negotiated_before_decoder_starts(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.flac"
            source.write_bytes(b"test")
            with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
                 patch.object(local_playback, "sd", create=True) as sd, \
                 patch.object(local_playback.subprocess, "Popen", return_value=_FakeProcess()) as spawn:
                sd.PortAudioError = RuntimeError
                sd.check_output_settings.side_effect = [RuntimeError("Invalid sample rate"), None]
                sd.OutputStream.side_effect = _FakeStream
                player = local_playback.LocalPlayer()
                with patch.object(player, "_get_device_info", return_value={"name": "DAC", "sample_rate": 192000}):
                    self.assertTrue(player.play_file(str(source), output_sample_rate=44100))
                self.assertEqual(player.output_sample_rate, 192000)
                cmd = spawn.call_args.args[0]
                self.assertEqual(cmd[cmd.index("-ar") + 1], "192000")
                player.stop()

    def test_explicit_upsampling_does_not_silently_use_another_rate(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.flac"
            source.write_bytes(b"test")
            with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
                 patch.object(local_playback, "sd", create=True) as sd, \
                 patch.object(local_playback.subprocess, "Popen") as spawn:
                sd.PortAudioError = RuntimeError
                sd.check_output_settings.side_effect = RuntimeError("Invalid sample rate")
                player = local_playback.LocalPlayer()
                self.assertFalse(player.play_file(str(source), output_sample_rate=96000,
                                                  allow_device_rate_fallback=False))
                spawn.assert_not_called()

    def test_native_rates_are_bounded_like_browser_rates(self):
        self.assertEqual(local_playback._resolve_output_sample_rate(48000, "2x", 44100), 96000)
        for rate, multiplier in [(96000, "16x"), (48000, "32x")]:
            with self.assertRaises(ValueError):
                local_playback._resolve_output_sample_rate(rate, multiplier, 48000)

    def test_rapid_native_play_requests_cannot_overlap_player_start(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "source.flac"
            audio.write_bytes(b"test")
            active = 0
            peak = 0
            counter_lock = threading.Lock()
            from unittest.mock import MagicMock
            player = MagicMock(device_id=None, latency="medium")
            player._get_device_info.return_value = {"name": "DAC", "sample_rate": 48000}
            def play(*args, **kwargs):
                nonlocal active, peak
                with counter_lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                with counter_lock:
                    active -= 1
                return True
            player.play_file.side_effect = play
            with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
                 patch.object(local_playback, "_active_player", player), \
                 patch.object(local_playback, "_playback_state", dict(local_playback._playback_state)), \
                 patch("sqlite3.connect") as connect:
                connect.return_value.execute.return_value.fetchone.return_value = {
                    "file_path": str(audio), "sample_rate": 48000}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(local_playback.play_local, [1, 2]))
                self.assertTrue(all(result["success"] for result in results))
                self.assertEqual(peak, 1)

    def test_stop_unblocks_decoder_before_waiting_for_audio_callback(self):
        with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True):
            player = local_playback.LocalPlayer()
            events = []
            with patch.object(player, "_terminate_process", side_effect=lambda: events.append("decoder")), \
                 patch.object(player, "stream") as stream:
                stream.stop.side_effect = lambda: events.append("stream")
                player.stop()
            self.assertEqual(events, ["decoder", "stream"])

    def test_dsp_placed_after_input_with_float32_decoder(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "source.flac"
            audio.write_bytes(b"test")
            commands = []
            def spawn(cmd, **kwargs):
                commands.append(cmd)
                return _FakeProcess()

            player = None
            with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
                 patch.object(local_playback, "sd", create=True) as sd, \
                 patch.object(local_playback.subprocess, "Popen", side_effect=spawn):
                sd.OutputStream.side_effect = _FakeStream
                player = local_playback.LocalPlayer(device_id=None)
                with patch.object(player, "_get_device_info",
                                  return_value={"name": "DAC", "sample_rate": 48000}):
                    ok = player.play_file(
                        str(audio), volume=0.5, dsp_filter="volume=-2dB",
                        output_sample_rate=96000,
                    )
                self.assertTrue(ok)
                command = commands[0]
                self.assertLess(command.index(str(audio)), command.index("-af"))
                self.assertEqual(command[command.index("-af") + 1], "volume=-2dB")
                self.assertEqual(command[command.index("-acodec") + 1], "pcm_f32le")
                self.assertEqual(command[command.index("-ar") + 1], "96000")
                self.assertEqual(command[-1], "pipe:1")
                self.assertTrue(numpy.allclose(
                    _FakeStream.callback_result, [[0.125, -0.125]], atol=1e-6
                ))
                player.stop()


if __name__ == "__main__":
    unittest.main()
