import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

if importlib.util.find_spec("pydantic_settings") is None:
    from pydantic import BaseModel

    stub = types.ModuleType("pydantic_settings")

    class BaseSettings(BaseModel):
        pass

    def SettingsConfigDict(**kwargs):
        return kwargs

    stub.BaseSettings = BaseSettings
    stub.SettingsConfigDict = SettingsConfigDict
    sys.modules["pydantic_settings"] = stub

import server
import local_playback
from db_schema import apply_migrations


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "library.db"
        self.original_music_dir = server.MUSIC_DIR
        self.original_data_dir = server.DATA_DIR
        self.original_port = server.PORT

        with self._connect() as conn:
            apply_migrations(conn)
            self._seed(conn)

        self.get_db_patcher = patch.object(server, "get_db", side_effect=self._open_connection)
        self.get_db_patcher.start()
        server.MUSIC_DIR = "C:/TestMusic"
        server.DATA_DIR = self.root / "data"
        server.PORT = 9876
        server._scan_job.update(
            running=True,
            progress=3,
            total=10,
            current_file="demo.flac",
            tracks_added=2,
            tracks_skipped=1,
            error=None,
            music_dir="C:/TestMusic",
        )
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        self.get_db_patcher.stop()
        server.MUSIC_DIR = self.original_music_dir
        server.DATA_DIR = self.original_data_dir
        server.PORT = self.original_port
        server._scan_job.finish(
            progress=0,
            total=0,
            current_file="",
            tracks_added=0,
            tracks_skipped=0,
            error=None,
            music_dir=server.MUSIC_DIR,
        )
        self.temp_dir.cleanup()

    def _open_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _connect(self):
        return closing(self._open_connection())

    def _seed(self, conn: sqlite3.Connection):
        music_dir = self.root / "music"
        music_dir.mkdir()
        track_one = music_dir / "one.flac"
        track_two = music_dir / "two.flac"
        track_one.write_bytes(b"one")
        track_two.write_bytes(b"two")

        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (1, 'Artist One', 'Artist One')")
        conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (2, 'Artist Two', 'Artist Two')")
        conn.execute(
            """
            INSERT INTO albums (id, artist_id, title, year, genre, cover_path)
            VALUES
                (1, 1, 'Album One', 2001, 'Classical', NULL),
                (2, 2, 'Album Two', 2002, 'Jazz', 'C:/covers/two.jpg')
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, duration, format, file_path)
            VALUES
                (1, 1, 1, 'Track One', 180, 'FLAC', ?),
                (2, 2, 2, 'Track Two', 210, 'MP3', ?)
            """,
            (str(track_one), str(track_two)),
        )
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (1, 1)")
        conn.execute("INSERT INTO track_artists (track_id, artist_id) VALUES (2, 2)")
        conn.execute("INSERT INTO libraries (id, path) VALUES (1, 'C:/ExistingLibrary')")
        conn.commit()

    def test_playlist_routes_support_full_crud_flow(self):
        create_resp = self.client.post(
            "/api/playlists",
            json={"name": "Road Trip", "description": "Drive mix"},
        )
        self.assertEqual(create_resp.status_code, 200)
        playlist_id = create_resp.json()["id"]

        add_resp = self.client.post(f"/api/playlists/{playlist_id}/tracks", json={"track_id": 1})
        self.assertEqual(add_resp.status_code, 200)
        self.assertEqual(add_resp.json(), {"success": True})

        detail_resp = self.client.get(f"/api/playlists/{playlist_id}")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["tracks"][0]["title"], "Track One")

        update_resp = self.client.put(
            f"/api/playlists/{playlist_id}",
            json={"name": "Road Trip 2", "description": "Updated"},
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json(), {"success": True})

        remove_resp = self.client.delete(f"/api/playlists/{playlist_id}/tracks/1")
        self.assertEqual(remove_resp.status_code, 200)
        self.assertEqual(remove_resp.json(), {"success": True})

        delete_resp = self.client.delete(f"/api/playlists/{playlist_id}")
        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(delete_resp.json(), {"success": True})

    def test_runtime_routes_record_play_and_report_stats(self):
        play_resp = self.client.post("/api/tracks/1/played", json={"duration_played": 175})
        self.assertEqual(play_resp.status_code, 200)
        self.assertEqual(play_resp.json(), {"ok": True})

        history_resp = self.client.get("/api/history?limit=10")
        self.assertEqual(history_resp.status_code, 200)
        self.assertEqual(history_resp.json()[0]["title"], "Track One")

        stats_resp = self.client.get("/api/stats/listening")
        self.assertEqual(stats_resp.status_code, 200)
        self.assertEqual(stats_resp.json()["total_plays"], 1)
        self.assertEqual(stats_resp.json()["plays_today"], 1)

    def test_config_routes_manage_libraries_and_music_dir(self):
        config_resp = self.client.get("/api/config")
        self.assertEqual(config_resp.status_code, 200)
        self.assertEqual(config_resp.json()["libraries"][0]["path"], "C:/ExistingLibrary")
        self.assertEqual(config_resp.json()["music_dir"], "C:/TestMusic")

        add_resp = self.client.post("/api/config/libraries", json={"path": "C:/SecondLibrary"})
        self.assertEqual(add_resp.status_code, 200)
        added_paths = [item["path"] for item in add_resp.json()["libraries"]]
        self.assertIn("C:/SecondLibrary", added_paths)

        remove_resp = self.client.delete("/api/config/libraries/1")
        self.assertEqual(remove_resp.status_code, 200)
        removed_paths = [item["path"] for item in remove_resp.json()["libraries"]]
        self.assertNotIn("C:/ExistingLibrary", removed_paths)

        set_resp = self.client.post("/api/config", json={"music_dir": "C:/UpdatedMusic"})
        self.assertEqual(set_resp.status_code, 200)
        final_paths = [item["path"] for item in set_resp.json()["libraries"]]
        self.assertIn("C:/UpdatedMusic", final_paths)
        self.assertEqual(set_resp.json()["music_dir"], "C:/UpdatedMusic")
        self.assertEqual(set_resp.json()["port"], 9876)
        self.assertEqual(set_resp.json()["data_dir"], str(self.root / "data"))

    def test_config_fallback_seeds_music_dir_when_no_libraries_exist(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM libraries")
            conn.commit()

        resp = self.client.get("/api/config")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["libraries"][0]["path"], "C:/TestMusic")

    def test_scan_status_route_returns_job_snapshot(self):
        resp = self.client.get("/api/scan/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["running"])
        self.assertEqual(body["progress"], 3)
        self.assertEqual(body["current_file"], "demo.flac")

    def test_dsp_profiles_route_returns_seeded_factory_presets(self):
        resp = self.client.get("/api/dsp/profiles")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        preset_keys = {profile["preset_key"] for profile in body}
        self.assertIn("flat", preset_keys)
        self.assertIn("reference_neutral", preset_keys)
        self.assertIn("black_background", preset_keys)
        self.assertIn("vocal_forward_warm", preset_keys)
        self.assertIn("vocal_forward_cool", preset_keys)
        self.assertIn("bass_boost", preset_keys)
        self.assertIn("sub_bass_boost", preset_keys)
        self.assertIn("pop", preset_keys)
        self.assertIn("classical", preset_keys)

    def test_create_custom_dsp_profile_persists_ordered_filters(self):
        resp = self.client.post(
            "/api/dsp/profiles",
            json={
                "name": "My Custom",
                "description": "Personal voicing",
                "category": "custom",
                "preamp_db": -2.0,
                "filters": [
                    {"type": "peaking", "freq": 1000, "gain": 1.5, "q": 1.1, "enabled": True}
                ],
                "tags": ["custom", "personal"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        profile_id = resp.json()["id"]

        detail_resp = self.client.get(f"/api/dsp/profiles/{profile_id}")
        self.assertEqual(detail_resp.status_code, 200)
        profile = detail_resp.json()
        self.assertEqual(profile["category"], "custom")
        self.assertEqual(profile["preamp_db"], -2.0)
        self.assertIn('"type": "peaking"', profile["filter_chain_json"])

    def test_create_custom_dsp_profile_sanitizes_invalid_filters(self):
        resp = self.client.post(
            "/api/dsp/profiles",
            json={
                "name": "Unsafe Custom",
                "filters": [
                    {"type": "PEAKING", "freq": "18", "gain": "99", "q": "0.01", "enabled": True},
                    {"type": "unknown", "freq": 1000, "gain": 2, "q": 1.0, "enabled": True},
                    {"type": "high_shelf", "freq": "12000", "gain": "-3.5", "q": "2.2", "enabled": False},
                    {"type": "low_shelf", "freq": "oops", "gain": 4, "q": 1.0, "enabled": True},
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        profile_id = resp.json()["id"]

        detail_resp = self.client.get(f"/api/dsp/profiles/{profile_id}")
        self.assertEqual(detail_resp.status_code, 200)
        profile = detail_resp.json()
        filter_chain = server.json.loads(profile["filter_chain_json"])

        self.assertEqual(len(filter_chain), 2)
        self.assertEqual(filter_chain[0]["type"], "peaking")
        self.assertEqual(filter_chain[0]["freq"], 20.0)
        self.assertEqual(filter_chain[0]["gain"], 18.0)
        self.assertEqual(filter_chain[0]["q"], 0.1)
        self.assertTrue(filter_chain[0]["enabled"])
        self.assertEqual(filter_chain[1]["type"], "high_shelf")
        self.assertEqual(filter_chain[1]["freq"], 12000.0)
        self.assertEqual(filter_chain[1]["gain"], -3.5)
        self.assertEqual(filter_chain[1]["q"], 2.2)
        self.assertFalse(filter_chain[1]["enabled"])

    def test_factory_dsp_profile_can_be_cloned_but_not_updated_or_deleted(self):
        update_resp = self.client.put(
            "/api/dsp/profiles/2",
            json={"name": "Should Fail"},
        )
        self.assertEqual(update_resp.status_code, 400)

        delete_resp = self.client.delete("/api/dsp/profiles/2")
        self.assertEqual(delete_resp.status_code, 400)

        clone_resp = self.client.post("/api/dsp/profiles/2/clone", json={"name": "Black Background Custom"})
        self.assertEqual(clone_resp.status_code, 200)
        clone_id = clone_resp.json()["id"]

        detail_resp = self.client.get(f"/api/dsp/profiles/{clone_id}")
        self.assertEqual(detail_resp.status_code, 200)
        cloned_profile = detail_resp.json()
        self.assertEqual(cloned_profile["name"], "Black Background Custom")
        self.assertEqual(cloned_profile["category"], "custom")
        self.assertEqual(cloned_profile["is_factory"], 0)

    def test_build_dsp_filter_chain_supports_preamp_and_ordered_filters(self):
        profile = {
            "preamp_db": -4.0,
            "filter_chain_json": """
            [
              {"type": "low_shelf", "freq": 90, "gain": -0.8, "q": 0.7, "enabled": true},
              {"type": "peaking", "freq": 1800, "gain": 1.5, "q": 0.9, "enabled": true},
              {"type": "high_shelf", "freq": 10000, "gain": 0.8, "q": 0.7, "enabled": true}
            ]
            """,
            "volume_normalize": 0,
            "stereo_width": 0,
            "reverb_room_size": 0,
            "reverb_wet_dry": 0,
        }

        chain = server.build_dsp_filter_chain(profile)

        self.assertIn("volume=-4.0dB", chain)
        self.assertIn("bass=g=-0.8:f=90.0:w=0.7", chain)
        self.assertIn("equalizer=f=1800.0:t=q:w=0.9:g=1.5", chain)
        self.assertIn("treble=g=0.8:f=10000.0:w=0.7", chain)
        self.assertTrue(chain.startswith("volume=-4.0dB"))

    def test_playback_route_forwards_dsp_profile_and_upsample_to_native_player(self):
        with patch.object(local_playback, "play_local", return_value={"success": True}) as play_local_mock:
            resp = self.client.post(
                "/api/playback/play",
                json={
                    "track_id": 1,
                    "device_id": 3,
                    "latency": "medium",
                    "volume": 0.85,
                    "dsp_profile_id": 4,
                    "upsample": "2x",
                },
            )

        self.assertEqual(resp.status_code, 200)
        play_local_mock.assert_called_once_with(
            track_id=1,
            device_id=3,
            latency="medium",
            volume=0.85,
            dsp_profile_id=4,
            upsample="2x",
        )

    def test_zones_route_uses_cached_devices_unless_refresh_is_requested(self):
        cached_devices = [{"udn": "cached-1", "name": "Cached Renderer"}]
        refreshed_devices = [{"udn": "fresh-1", "name": "Fresh Renderer"}]

        with patch.object(server.upnp_server, "get_cached_renderers", return_value=cached_devices) as get_cached, \
             patch.object(server.upnp_server, "refresh_renderers", return_value=refreshed_devices) as refresh_renderers, \
             patch.object(server.upnp_server, "refresh_renderers_async") as refresh_renderers_async, \
             patch.object(local_playback, "get_output_devices", return_value=[]), \
             patch.object(local_playback, "SOUNDDEVICE_AVAILABLE", True):
            cached_resp = self.client.get("/api/zones")
            self.assertEqual(cached_resp.status_code, 200)
            self.assertEqual(cached_resp.json()["network"]["devices"], cached_devices)
            get_cached.assert_called_once()
            refresh_renderers.assert_not_called()
            refresh_renderers_async.assert_not_called()

            refreshed_resp = self.client.get("/api/zones?refresh=true")
            self.assertEqual(refreshed_resp.status_code, 200)
            self.assertEqual(refreshed_resp.json()["network"]["devices"], refreshed_devices)
            self.assertEqual(refresh_renderers.call_count, 1)


if __name__ == "__main__":
    unittest.main()
