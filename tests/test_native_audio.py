"""Native sounddevice command and float32 sample-format regressions."""
import io
import tempfile
import unittest
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
