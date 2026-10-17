"""Native zone digital gain and frame-based progress semantics."""
import unittest
from unittest.mock import patch

import numpy as np
import local_playback


class NativeControlTests(unittest.TestCase):
    def test_position_counts_output_frames_not_wallclock(self):
        with patch.object(local_playback,"SOUNDDEVICE_AVAILABLE",True):
            player=local_playback.LocalPlayer()
        player.output_sample_rate=48000
        player._played_frames=24000
        self.assertAlmostEqual(player.get_position(),0.5)
        player._played_frames=48000
        self.assertEqual(player.get_position(),1.0)

    def test_volume_is_finite_and_unity_keeps_float32_samples(self):
        with patch.object(local_playback,"SOUNDDEVICE_AVAILABLE",True):
            player=local_playback.LocalPlayer()
        player.stream=object()
        self.assertTrue(player.set_volume(1.0))
        ring=local_playback.PcmRingBuffer(4)
        frames=np.array([[0.5,-0.5],[0.25,-0.25]],dtype="float32")
        ring.write(frames)
        out=np.empty_like(frames)
        ring.read_into(out,player._gain)
        np.testing.assert_array_equal(out,frames)
        self.assertTrue(player.set_volume(0.5))
        ring.write(frames)
        ring.read_into(out,player._gain)
        np.testing.assert_array_equal(out,frames * 0.5)
        for invalid in (-1,1.5,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                player.set_volume(invalid)

    def test_status_reports_position_only_for_active_track(self):
        with patch.object(local_playback,"_active_player") as player:
            player.get_position.return_value=12.5
            with patch.dict(local_playback._playback_state,{"track_id":123,"playing":True}):
                status=local_playback.get_playback_status()
            self.assertEqual(status["position"],12.5)
            with patch.dict(local_playback._playback_state,{"track_id":None,"playing":False}):
                status=local_playback.get_playback_status()
            self.assertEqual(status["position"],0)


if __name__=="__main__":
    unittest.main()
