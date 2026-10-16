"""Native sounddevice command and float32 sample-format regressions."""
import io
import sys
import tempfile
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy

import local_playback
from path_compat import filesystem_path


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
                # Input is passed in extended-length form (Windows long paths).
                self.assertLess(command.index(filesystem_path(audio)), command.index("-af"))
                self.assertEqual(command[command.index("-af") + 1], "volume=-2dB")
                self.assertEqual(command[command.index("-acodec") + 1], "pcm_f32le")
                self.assertEqual(command[command.index("-ar") + 1], "96000")
                self.assertEqual(command[-1], "pipe:1")
                self.assertTrue(numpy.allclose(
                    _FakeStream.callback_result, [[0.125, -0.125]], atol=1e-6
                ))
                player.stop()


class _CallbackStop(Exception):
    pass


class _RecordingProcess:
    """FFmpeg stand-in that records which thread reads or terminates it."""

    def __init__(self, frames=0, endless=False):
        self.read_threads, self.terminate_threads, self.wait_threads = [], [], []
        self._payload = io.BytesIO(numpy.full((frames, 2), 0.5, dtype="<f4").tobytes())
        self.endless = endless
        self.closed = False
        self.returncode = None
        self.stdout = self

    def read(self, size):
        self.read_threads.append(threading.current_thread().name)
        if self.endless:
            time.sleep(0.001)
            return b"" if self.closed else b"\0" * size
        return self._payload.read(size)

    def close(self):
        self.closed = True

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_threads.append(threading.current_thread().name)
        self.closed = True
        self.returncode = -15

    kill = terminate

    def wait(self, timeout=None):
        self.wait_threads.append(threading.current_thread().name)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class _CallbackThreadStream:
    """Runs the audio callback on its own thread, like PortAudio."""

    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.running = False
        self.thread = None

    def start(self):
        self.running = True

        def loop():
            while self.running:
                out = numpy.zeros((256, 2), dtype="float32")
                try:
                    self.callback(out, 256, None, None)
                except _CallbackStop:
                    self.running = False
                time.sleep(0.001)

        self.thread = threading.Thread(target=loop, name="portaudio-callback", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(1)

    def close(self):
        pass


class PcmRingBufferTests(unittest.TestCase):
    def test_wraps_around_and_applies_gain(self):
        ring = local_playback.PcmRingBuffer(4)
        ring.write(numpy.array([[1, -1], [2, -2], [3, -3]], dtype="float32"))
        out = numpy.zeros((2, 2), dtype="float32")
        self.assertEqual(ring.read_into(out), 2)
        ring.write(numpy.array([[4, -4], [5, -5], [6, -6]], dtype="float32"))  # wraps
        out = numpy.zeros((4, 2), dtype="float32")
        self.assertEqual(ring.read_into(out, gain=0.5), 4)
        self.assertTrue(numpy.allclose(out[:, 0], [1.5, 2, 2.5, 3]))

    def test_read_never_waits_for_the_producer(self):
        ring = local_playback.PcmRingBuffer(8)
        out = numpy.ones((4, 2), dtype="float32")
        started = time.monotonic()
        self.assertEqual(ring.read_into(out), 0)
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertFalse(ring.drained)
        ring.mark_eof()
        self.assertTrue(ring.drained)

    def test_writer_is_bounded_and_close_releases_it(self):
        ring = local_playback.PcmRingBuffer(4)
        result = []
        writer = threading.Thread(target=lambda: result.append(
            ring.write(numpy.zeros((10, 2), dtype="float32"))))
        writer.start()
        time.sleep(0.1)
        self.assertTrue(writer.is_alive())       # blocked: memory stays bounded
        self.assertEqual(ring.available, 4)
        ring.read_into(numpy.zeros((4, 2), dtype="float32"))
        time.sleep(0.1)
        self.assertEqual(ring.available, 4)      # producer resumed after space appeared
        ring.close()
        writer.join(1)
        self.assertFalse(writer.is_alive())
        self.assertEqual(result, [False])


class DecoderWorkerTests(unittest.TestCase):
    def _play(self, process, state=None):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.flac"
            source.write_bytes(b"test")
            patches = [
                patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True),
                patch.object(local_playback, "sd", create=True),
                patch.object(local_playback.subprocess, "Popen", return_value=process),
                patch.object(local_playback, "_playback_state", state if state is not None else {}),
            ]
            for active in patches:
                active.start()
            self.addCleanup(lambda: [active.stop() for active in reversed(patches)])
            sd = local_playback.sd
            sd.CallbackStop = _CallbackStop
            sd.PortAudioError = RuntimeError
            sd.OutputStream.side_effect = _CallbackThreadStream
            player = local_playback.LocalPlayer()
            self.addCleanup(player.stop)
            with patch.object(player, "_get_device_info", return_value={"name": "DAC", "sample_rate": 48000}):
                self.assertTrue(player.play_file(str(source), output_sample_rate=48000))
            return player

    def test_callback_never_reads_the_pipe_or_controls_the_process(self):
        process = _RecordingProcess(endless=True)
        player = self._play(process)
        time.sleep(0.2)
        player.stop()
        self.assertTrue(process.read_threads)
        self.assertEqual(set(process.read_threads), {"velvet-ffmpeg-decoder"})
        self.assertNotIn("portaudio-callback", process.terminate_threads + process.wait_threads)

    def test_natural_end_is_finished_off_the_audio_thread(self):
        state = {"playing": True, "paused": False, "track_id": 5, "position": 0}
        process = _RecordingProcess(frames=2000)
        self._play(process, state)
        deadline = time.monotonic() + 3
        while state["track_id"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(state["track_id"])
        self.assertFalse(state["playing"])
        self.assertEqual(process.wait_threads, ["velvet-ffmpeg-decoder"])

    def test_stop_returns_promptly_with_a_decoder_blocked_on_a_full_buffer(self):
        process = _RecordingProcess(endless=True)
        player = self._play(process)
        decoder = player._decoder
        time.sleep(0.2)  # ring fills; producer waits for space
        player.stream.stop()  # paused: callback no longer drains
        started = time.monotonic()
        player.stop()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(decoder.is_alive())
        self.assertTrue(process.terminate_threads)

    def test_explicit_stop_does_not_clear_a_newer_tracks_state(self):
        state = {"playing": True, "paused": False, "track_id": 9, "position": 0}
        player = self._play(_RecordingProcess(endless=True), state)
        player.stop()
        time.sleep(0.2)
        self.assertEqual(state["track_id"], 9)


class _CapturingStream:
    """Records the OutputStream arguments; the test drives the callback."""
    created = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _CapturingStream.created.append(self)

    def start(self):
        pass

    stop = close = start


class _BlockingProcess:
    """FFmpeg stand-in whose stdout stays silent until it is closed."""

    def __init__(self):
        self._closed = threading.Event()
        self.stdout = self
        self.returncode = None

    def read(self, size):
        self._closed.wait(5)
        return b""

    def close(self):
        self._closed.set()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.close()
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


class NativeStreamSettingsTests(unittest.TestCase):
    def setUp(self):
        _CapturingStream.created = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / "source.flac"
        self.source.write_bytes(b"test")
        patches = [
            patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True),
            patch.object(local_playback, "sd", create=True),
            patch.object(local_playback, "PREBUFFER_TIMEOUT_SECONDS", 0),
        ]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        self.sd = local_playback.sd
        self.sd.CallbackStop = _CallbackStop
        self.sd.OutputStream.side_effect = _CapturingStream
        self.sd.query_devices.return_value = {
            "name": "Speakers (Realtek)", "max_output_channels": 2,
            "default_samplerate": 48000, "default_low_output_latency": 0.01, "hostapi": 0,
        }

    def _play(self, player, process=None):
        with patch.object(local_playback.subprocess, "Popen", return_value=process or _FakeProcess()):
            self.assertTrue(player.play_file(str(self.source)))
        return _CapturingStream.created[-1]

    def test_low_latency_uses_medium_on_mme_and_logs_once(self):
        self.sd.query_hostapis.return_value = {"name": "MME"}
        player = local_playback.LocalPlayer(latency="low")
        self.addCleanup(player.stop)
        with self.assertLogs("velvet.playback", level="INFO") as logs:
            first = self._play(player)
            second = self._play(player)
        self.sd.query_hostapis.assert_called_with(0)
        self.assertEqual(first.kwargs["latency"], player.latency_map["medium"])
        self.assertEqual(second.kwargs["latency"], player.latency_map["medium"])
        self.assertEqual(sum("MME" in line for line in logs.output), 1)

    def test_low_latency_is_kept_on_wasapi_and_asio(self):
        for host_api in ("Windows WASAPI", "ASIO"):
            with self.subTest(host_api=host_api):
                self.sd.query_hostapis.return_value = {"name": host_api}
                player = local_playback.LocalPlayer(latency="low")
                self.addCleanup(player.stop)
                self.assertEqual(self._play(player).kwargs["latency"], 0.005)

    def test_xrun_flags_and_starvation_are_counted_separately(self):
        self.sd.query_hostapis.return_value = {"name": "Windows WASAPI"}
        player = local_playback.LocalPlayer(latency="medium")
        self.addCleanup(player.stop)
        callback = self._play(player, _BlockingProcess()).kwargs["callback"]
        out = numpy.ones((64, 2), dtype="float32")
        callback(out, 64, None, None)  # decoder has produced nothing yet
        self.assertEqual((player.xrun_flags, player.starvations), (0, 1))
        self.assertFalse(out.any())    # starved frames are silence
        player._ring.write(numpy.full((64, 2), 0.5, dtype="float32"))
        callback(out, 64, None, "output underflow")
        self.assertEqual((player.xrun_flags, player.starvations), (1, 1))

    def test_end_of_track_tail_is_not_counted_as_starvation(self):
        self.sd.query_hostapis.return_value = {"name": "Windows WASAPI"}
        player = local_playback.LocalPlayer(latency="medium")
        self.addCleanup(player.stop)
        callback = self._play(player).kwargs["callback"]  # one frame, then EOF
        deadline = time.monotonic() + 2
        while not player._ring.eof and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.assertRaises(_CallbackStop):
            callback(numpy.zeros((64, 2), dtype="float32"), 64, None, None)
        self.assertEqual(player.starvations, 0)


class ZeroLengthTrackStateTests(unittest.TestCase):
    def test_track_that_drains_before_play_local_returns_does_not_stay_playing(self):
        finished = threading.Event()
        real_finish = local_playback.LocalPlayer._finish_playback

        def finish(player, process, stop_event):
            real_finish(player, process, stop_event)
            finished.set()

        test = self

        class DrainOnStartStream:
            """The callback drains the empty ring and the decoder finishes the
            track before play_file returns to play_local."""

            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def start(self):
                with test.assertRaises(_CallbackStop):
                    self.callback(numpy.zeros((256, 2), dtype="float32"), 256, None, None)
                test.assertTrue(finished.wait(2))

            def stop(self):
                pass

            close = stop

        state = {"playing": False, "paused": False, "track_id": None, "position": 0,
                 "device": None, "volume": 1.0}
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "empty.flac"
            audio.write_bytes(b"test")
            with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
                 patch.object(local_playback, "sd", create=True) as sd, \
                 patch.object(local_playback, "_active_player", None), \
                 patch.object(local_playback, "_playback_state", state), \
                 patch.object(local_playback.LocalPlayer, "_finish_playback", finish), \
                 patch.object(local_playback.subprocess, "Popen",
                              return_value=_RecordingProcess(frames=0)), \
                 patch("sqlite3.connect") as connect:
                sd.CallbackStop = _CallbackStop
                sd.OutputStream.side_effect = DrainOnStartStream
                sd.query_devices.return_value = {
                    "name": "DAC", "max_output_channels": 2, "default_samplerate": 48000,
                    "hostapi": 0}
                sd.query_hostapis.return_value = {"name": "Windows WASAPI"}
                connect.return_value.execute.return_value.fetchone.return_value = {
                    "file_path": str(audio), "sample_rate": 48000}
                result = local_playback.play_local(track_id=3)
                local_playback._active_player.stop()
        self.assertTrue(result["success"])
        self.assertFalse(state["playing"])
        self.assertIsNone(state["track_id"])


class NativeAudioInstallTests(unittest.TestCase):
    def test_requirements_install_sounddevice_on_windows(self):
        from packaging.requirements import Requirement

        root = Path(__file__).resolve().parents[1]
        lines = [line.split("#", 1)[0].strip()
                 for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()]
        matches = [Requirement(line) for line in lines if line.startswith("sounddevice")]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].marker.evaluate({"sys_platform": "win32"}))

    def test_installer_fails_on_pip_errors_and_verifies_native_audio(self):
        script = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")
        install = script.index("-r requirements.txt")
        self.assertIn("$LASTEXITCODE", script[install:install + 200])
        self.assertIn("SOUNDDEVICE_AVAILABLE", script)

    def test_missing_portaudio_library_is_reported_not_fatal(self):
        import builtins
        import importlib
        import sys

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise OSError("PortAudio library not found")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules):
            sys.modules.pop("local_playback", None)
            sys.modules.pop("sounddevice", None)
            with patch("builtins.__import__", side_effect=fake_import):
                module = importlib.import_module("local_playback")
        self.assertFalse(module.SOUNDDEVICE_AVAILABLE)
        self.assertIn("PortAudio library not found", module.SOUNDDEVICE_ERROR)

    @unittest.skipUnless(sys.platform == "win32", "Windows native audio path")
    def test_windows_install_provides_working_sounddevice(self):
        import sounddevice

        self.assertTrue(local_playback.SOUNDDEVICE_AVAILABLE, local_playback.SOUNDDEVICE_ERROR)
        self.assertTrue(sounddevice.get_portaudio_version()[1])

    def test_zones_report_why_native_audio_is_unavailable(self):
        from fastapi.testclient import TestClient
        import server

        with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", False), \
             patch.object(local_playback, "SOUNDDEVICE_ERROR", "OSError: PortAudio library not found"), \
             patch.object(server.security, "is_auth_enabled", return_value=False), \
             patch.object(server, "get_client_ip", return_value="127.0.0.1"), \
             patch.object(server.upnp_server, "get_cached_renderers", return_value=[{"udn": "x"}]):
            with TestClient(server.app) as client:
                native = client.get("/api/zones").json()["native"]
        self.assertFalse(native["available"])
        self.assertEqual(native["error"], "OSError: PortAudio library not found")


if __name__ == "__main__":
    unittest.main()
