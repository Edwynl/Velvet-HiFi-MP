import asyncio
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
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
import upnp_server
from audio_streaming import StreamSlots
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
        # TestClient is not a real host browser. Model local, trusted first-time
        # setup explicitly; individual auth tests override the client address.
        self.client_ip_patcher = patch.object(server, "get_client_ip", return_value="127.0.0.1")
        self.client_ip_patcher.start()
        # Never depend on a developer's persisted credentials or smoke tests.
        # Authentication-specific tests override this patch explicitly.
        self.auth_enabled_patcher = patch.object(server.security, "is_auth_enabled", return_value=False)
        self.auth_enabled_patcher.start()
        # Media tickets are signed with a persisted secret: never touch the
        # developer's velvet_data/security.json from tests.
        self.security_file_patcher = patch.object(server.security, "CONFIG_FILE", self.root / "security.json")
        self.security_file_patcher.start()
        self.security_manager_patcher = patch.object(
            server.security, "_security_manager", server.security.SecurityManager())
        self.security_manager_patcher.start()
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
        self.client_ip_patcher.stop()
        self.auth_enabled_patcher.stop()
        self.security_manager_patcher.stop()
        self.security_file_patcher.stop()
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

    def test_local_ip_detection_handles_172_16_private_boundary(self):
        self.assertTrue(server._is_local_client_ip("172.16.0.1"))
        self.assertTrue(server._is_local_client_ip("172.31.255.254"))
        self.assertFalse(server._is_local_client_ip("172.15.255.255"))
        self.assertFalse(server._is_local_client_ip("172.32.0.1"))

    def test_auth_middleware_only_skips_private_network_when_disabled_for_all_networks(self):
        with patch.dict(os.environ, {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "false"}), \
             patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": False, "error": "denied"}) as verify_auth, \
             patch.object(server, "get_client_ip", return_value="172.16.5.10") as client_ip:
            private_resp = self.client.get("/api/config")
            self.assertEqual(private_resp.status_code, 200)
            verify_auth.assert_not_called()

            client_ip.return_value = "172.15.5.10"
            non_private_resp = self.client.get("/api/config")
            self.assertEqual(non_private_resp.status_code, 401)
            verify_auth.assert_called_once()

    def test_cors_origins_include_documented_settings_and_legacy_override(self):
        with patch.object(server.settings, "cors_origins", ["https://example.test"]), \
             patch.dict(os.environ, {"CORS_ORIGINS": "https://legacy.test"}):
            origins = server._get_cors_origins()
        self.assertIn("https://example.test", origins)
        self.assertIn("https://legacy.test", origins)
        self.assertEqual(len(origins), len(set(origins)))

    def test_auth_middleware_ignores_credentials_in_query_string(self):
        # Query strings leak into logs, history and renderer metadata.
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth",
                          return_value={"allowed": False, "error": "Authentication required"}) as verify_auth:
            resp = self.client.get("/api/config?api_key=test-query-key&password=pw")
            self.assertEqual(resp.status_code, 401)
            self.assertIsNone(verify_auth.call_args.kwargs["api_key"])
            self.assertIsNone(verify_auth.call_args.kwargs["password"])

    def test_stream_route_allows_tracks_inside_configured_library_roots(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM libraries")
            conn.execute("INSERT INTO libraries (path) VALUES (?)", (str(self.root / "music"),))
            conn.commit()

        resp = self.client.get("/api/stream/1", headers={"Range": "bytes=0-3"})
        self.assertIn(resp.status_code, {200, 206})

    def test_stream_route_rejects_tracks_outside_configured_library_roots(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM libraries")
            conn.execute("INSERT INTO libraries (path) VALUES (?)", ("C:/UnrelatedRoot",))
            conn.commit()

        resp = self.client.get("/api/stream/1", headers={"Range": "bytes=0-3"})
        self.assertEqual(resp.status_code, 403)

    def test_dop_and_unknown_pcm_multiplier_fail_explicitly(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM libraries")
            conn.execute("INSERT INTO libraries (path) VALUES (?)", (str(self.root / "music"),))
            conn.commit()
        self.assertEqual(self.client.get("/api/stream/1?dop=dsd64").status_code, 501)
        self.assertEqual(self.client.get("/api/stream/1?upsample=32x").status_code, 422)

    def _allow_music_root(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM libraries")
            conn.execute("INSERT INTO libraries (path) VALUES (?)", (str(self.root / "music"),))
            conn.commit()

    @staticmethod
    def _key_only_auth(**kwargs):
        allowed = kwargs.get("api_key") == "session-key"
        return {"allowed": allowed, "error": None if allowed else "Authentication required"}

    def _ticket(self, scope="stream", resource_id=1, ttl=600):
        return server.security.issue_media_ticket(scope, resource_id, ttl)[0]

    def test_protected_cast_hands_renderer_track_scoped_ticket_urls(self):
        self._allow_music_root()
        sent = {}

        def fake_cast(udn, url, meta):
            sent.update(udn=udn, url=url, meta=meta)
            return True

        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": True}), \
             patch.object(upnp_server, "cast_track", side_effect=fake_cast):
            response = self.client.post("/api/cast/renderer-1", json={"track_id": 1},
                                        headers={"X-API-Key": "session-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        self.assertIn("/api/stream/1?ticket=", sent["url"])
        self.assertNotIn("api_key", sent["url"] + sent["meta"])
        self.assertNotIn("session-key", sent["url"] + sent["meta"])
        self.assertIn("ticket=", sent["meta"])  # cover art URL is ticketed too

        # The renderer on the LAN fetches with the ticket only (no headers).
        path = sent["url"].split(f":{upnp_server.MAIN_PORT}", 1)[1]
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth",
                          return_value={"allowed": False, "error": "denied"}) as verify_auth, \
             patch.object(server, "get_client_ip", return_value="192.168.1.50"):
            self.assertIn(self.client.get(path, headers={"Range": "bytes=0-1"}).status_code, {200, 206})
            verify_auth.assert_not_called()
            # The same ticket cannot read another track or a non-media route.
            other = path.replace("/api/stream/1?", "/api/stream/2?")
            self.assertEqual(self.client.get(other).status_code, 401)
            ticket = path.split("ticket=", 1)[1]
            self.assertEqual(self.client.get(f"/api/config?ticket={ticket}").status_code, 401)

    def test_browser_stream_ticket_requires_auth_and_only_reads_audio(self):
        self._allow_music_root()
        key = {"X-API-Key": "session-key"}
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", side_effect=self._key_only_auth), \
             patch.object(server, "get_client_ip", return_value="203.0.113.9"):
            self.assertEqual(self.client.post("/api/stream-ticket").status_code, 401)
            issued = self.client.post("/api/stream-ticket", headers=key)
            self.assertEqual(issued.status_code, 200)
            self.assertEqual(issued.headers["cache-control"], "no-store")
            body = issued.json()
            self.assertAlmostEqual(body["expires_at"],
                                   time.time() + server.security.BROWSER_STREAM_TICKET_SECONDS, delta=5)
            ticket = body["ticket"]
            self.assertNotIn("session-key", ticket)
            for track_id in (1, 2):  # any track, so tap-to-play needs no extra round trip
                self.assertIn(self.client.get(f"/api/stream/{track_id}?ticket={ticket}",
                                              headers={"Range": "bytes=0-1"}).status_code, {200, 206})
            # Stream-only: never covers, the API or credential management.
            self.assertEqual(self.client.get(f"/api/covers/1/thumb?ticket={ticket}").status_code, 401)
            self.assertEqual(self.client.get(f"/api/tracks?ticket={ticket}").status_code, 401)
            self.assertEqual(self.client.post(f"/api/stream/1?ticket={ticket}").status_code, 401)
            self.assertEqual(self.client.get("/api/stream/1?ticket=1.forged").status_code, 401)
            # Rotating the API key revokes outstanding browser tickets.
            server.security.get_security_manager().generate_api_key()
            self.assertEqual(self.client.get(f"/api/stream/1?ticket={ticket}").status_code, 401)

    def test_dlna_head_probes_are_answered_without_a_body_or_decoder(self):
        self._allow_music_root()
        size = (self.root / "music" / "one.flac").stat().st_size
        ticket = self._ticket()
        with patch.object(server, "get_client_ip", return_value="192.168.1.50"):
            full = self.client.head(f"/api/stream/1?ticket={ticket}")
            self.assertEqual(full.status_code, 200)
            self.assertEqual(full.headers["content-length"], str(size))
            self.assertEqual(full.headers["accept-ranges"], "bytes")
            self.assertEqual(full.content, b"")
            partial = self.client.head(f"/api/stream/1?ticket={ticket}", headers={"Range": "bytes=0-1"})
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(partial.headers["content-range"], f"bytes 0-1/{size}")
            self.assertEqual(partial.headers["content-length"], "2")
            self.assertEqual(partial.content, b"")
            cover = self.client.head(f"/api/covers/2/thumb?ticket={self._ticket('cover', 2)}")
            self.assertNotIn(cover.status_code, {401, 405})
        with self._connect() as conn:
            profile_id = conn.execute(
                "INSERT INTO dsp_profiles (name, preamp_db) VALUES ('Head test', -3)").lastrowid
            conn.commit()
        with patch.object(server.asyncio, "create_subprocess_exec",
                          side_effect=AssertionError("HEAD must not start FFmpeg")):
            transcoded = self.client.head(f"/api/stream/1?dsp={profile_id}")
        self.assertEqual(transcoded.status_code, 200)
        self.assertEqual(transcoded.headers["content-type"], "audio/flac")

    def test_renderer_urls_use_the_configured_server_port(self):
        self.assertEqual(upnp_server.MAIN_PORT, server.settings.port)
        with patch.object(upnp_server, "MAIN_PORT", 9123):
            stream_url, cover_url = upnp_server.media_urls({"id": 1, "album_id": 2, "duration": 60})
        self.assertIn(":9123/api/stream/1?ticket=", stream_url)
        self.assertIn(":9123/api/covers/2/thumb?", cover_url)

    def test_ticket_from_unconfigured_host_lets_lan_renderer_play_only_that_track(self):
        self._allow_music_root()
        with patch.object(server, "get_client_ip", return_value="192.168.1.50"):
            self.assertEqual(self.client.get("/api/stream/1").status_code, 403)
            self.assertIn(self.client.get(f"/api/stream/1?ticket={self._ticket()}",
                                          headers={"Range": "bytes=0-1"}).status_code, {200, 206})
            self.assertEqual(self.client.get(f"/api/stream/2?ticket={self._ticket()}").status_code, 401)
            self.assertEqual(self.client.get("/api/tracks?ticket=" + self._ticket()).status_code, 403)

    def test_cover_ticket_is_scoped_to_cover_reads(self):
        cover = self._ticket("cover", 2)
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": False, "error": "denied"}), \
             patch.object(server, "get_client_ip", return_value="192.168.1.50"):
            self.assertNotEqual(self.client.get(f"/api/covers/2/thumb?size=medium&ticket={cover}").status_code, 401)
            self.assertEqual(self.client.get(f"/api/covers/1/thumb?ticket={cover}").status_code, 401)
            self.assertEqual(self.client.get(f"/api/stream/2?ticket={cover}").status_code, 401)

    def test_dlna_browse_window_requires_credentials_and_is_bounded(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", side_effect=self._key_only_auth), \
             patch.dict(os.environ, {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "false"}), \
             patch.object(server, "get_client_ip", return_value="192.168.1.23"):
            # LAN auth opt-out must not let a guest open library browsing.
            self.assertEqual(self.client.post("/api/dlna/browse-access", json={}).status_code, 401)
            self.client.headers["X-API-Key"] = "session-key"
            self.assertFalse(self.client.get("/api/dlna/browse-access").json()["active"])
            granted = self.client.post("/api/dlna/browse-access", json={"minutes": 30}).json()
            self.assertTrue(granted["active"])
            self.assertAlmostEqual(granted["expires_at"], time.time() + 1800, delta=5)
            for minutes in [0, 481, "60", True]:
                self.assertEqual(self.client.post("/api/dlna/browse-access",
                                                  json={"minutes": minutes}).status_code, 422)
            self.assertFalse(self.client.delete("/api/dlna/browse-access").json()["active"])
            self.assertEqual(server.security.get_security_manager().dlna_browse_expires_at(), 0)

    def test_transcoded_streams_are_limited_to_configured_concurrency(self):
        self._allow_music_root()
        with self._connect() as conn:
            profile_id = conn.execute(
                "INSERT INTO dsp_profiles (name, preamp_db) VALUES ('Quiet test', -3)").lastrowid
            conn.commit()
        slots = StreamSlots(1)
        held = slots.try_acquire()
        with patch.object(server, "TRANSCODE_SLOTS", slots), \
             patch.object(server, "TRANSCODE_SLOT_WAIT_SECONDS", 0.2):
            response = self.client.get(f"/api/stream/1?dsp={profile_id}")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "5")
            # Direct (untranscoded) playback is not limited.
            self.assertIn(self.client.get("/api/stream/1", headers={"Range": "bytes=0-1"}).status_code, {200, 206})
            held.release()
            self.assertIsNotNone(slots.try_acquire())

    def test_track_switch_waits_for_the_previous_transcode_slot(self):
        slots = StreamSlots(1)
        held = slots.try_acquire()

        async def switch_tracks():
            async def old_stream_closes():
                await asyncio.sleep(0.2)
                held.release()
            closer = asyncio.create_task(old_stream_closes())
            lease = await server._acquire_transcode_slot()
            await closer
            return lease

        with patch.object(server, "TRANSCODE_SLOTS", slots):
            lease = asyncio.run(switch_tracks())
        self.assertEqual(slots.active, 1)
        lease.release()

    def test_transcode_time_limit_respects_configuration_and_long_tracks(self):
        limit = server._transcode_time_limit_seconds
        with patch.object(server.settings, "stream_timeout_minutes", 60):
            self.assertEqual(limit(180), 3600)
            self.assertEqual(limit(None), 3600)
            self.assertEqual(limit(95 * 60), 95 * 60 + 1800)  # long-form track is not cut at 60 min
            self.assertEqual(limit(30 * 3600), server.TRANSCODE_MAX_SECONDS)
            self.assertEqual(limit(180, timeout_minutes=240), 240 * 60)
        with patch.object(server.settings, "stream_timeout_minutes", 120):
            self.assertEqual(limit(180), 7200)  # configuration above the old hard 60-min cap


if __name__ == "__main__":
    unittest.main()
