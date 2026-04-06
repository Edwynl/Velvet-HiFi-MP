import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_playback
from db_schema import apply_migrations


class DummyPlayer:
    instances = []

    def __init__(self, device_id=None, latency="low"):
        self.device_id = device_id
        self.latency = latency
        self.play_calls = []
        self.stop_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        DummyPlayer.instances.append(self)

    def _get_device_info(self):
        return {"name": "Dummy DAC", "sample_rate": 48000}

    def play_file(self, file_path, volume=1.0, dsp_filter="", output_sample_rate=None):
        self.play_calls.append(
            {
                "file_path": file_path,
                "volume": volume,
                "dsp_filter": dsp_filter,
                "output_sample_rate": output_sample_rate,
            }
        )
        return True

    def stop(self):
        self.stop_calls += 1

    def pause(self):
        self.pause_calls += 1
        return True

    def resume(self):
        self.resume_calls += 1
        return True


class LocalPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "library.db"
        self.audio_path = self.root / "sample.flac"
        self.audio_path.write_bytes(b"fLaC")

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        apply_migrations(conn)
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Album')")
        conn.execute(
            """
            INSERT INTO tracks (
                id, album_id, artist_id, title, duration, file_path, sample_rate, bit_depth
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 1, 1, "Track", 180, str(self.audio_path), 44100, 16),
        )
        conn.commit()
        conn.close()

        DummyPlayer.instances = []
        local_playback._active_player = None
        local_playback._playback_state.update(
            {
                "playing": False,
                "paused": False,
                "track_id": None,
                "position": 0,
                "device": None,
                "volume": 1.0,
            }
        )

    def tearDown(self):
        local_playback._active_player = None
        DummyPlayer.instances = []
        self.temp_dir.cleanup()

    def test_pause_resume_and_stop_control_the_active_player_instance(self):
        with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
             patch.object(local_playback, "_get_library_db_path", return_value=self.db_path), \
             patch.object(local_playback, "LocalPlayer", DummyPlayer):
            result = local_playback.play_local(
                track_id=1,
                device_id=7,
                latency="medium",
                volume=0.75,
                upsample="2x",
            )

            self.assertTrue(result["success"])
            self.assertEqual(len(DummyPlayer.instances), 1)

            player = DummyPlayer.instances[0]
            self.assertEqual(player.play_calls[0]["output_sample_rate"], 88200)

            pause_result = local_playback.pause_local()
            resume_result = local_playback.resume_local()
            stop_result = local_playback.stop_local()

            self.assertTrue(pause_result["success"])
            self.assertTrue(resume_result["success"])
            self.assertTrue(stop_result["success"])
            self.assertEqual(player.pause_calls, 1)
            self.assertEqual(player.resume_calls, 1)
            self.assertEqual(player.stop_calls, 1)
            self.assertFalse(local_playback._playback_state["playing"])
            self.assertIsNone(local_playback._playback_state["track_id"])

    def test_same_device_reuses_existing_player_instance(self):
        with patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True), \
             patch.object(local_playback, "_get_library_db_path", return_value=self.db_path), \
             patch.object(local_playback, "LocalPlayer", DummyPlayer):
            first = local_playback.play_local(track_id=1, device_id=2, latency="high", volume=1.0)
            second = local_playback.play_local(track_id=1, device_id=2, latency="high", volume=0.5)

            self.assertTrue(first["success"])
            self.assertTrue(second["success"])
            self.assertEqual(len(DummyPlayer.instances), 1)
            self.assertEqual(len(DummyPlayer.instances[0].play_calls), 2)


if __name__ == "__main__":
    unittest.main()
