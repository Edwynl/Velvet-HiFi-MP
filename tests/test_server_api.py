import asyncio
import concurrent.futures
import importlib.util
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
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
    def test_artist_region_facets_filter_and_reject_unknown_values(self):
        with self._connect() as conn:
            conn.execute("UPDATE artists SET name='BIGBANG' WHERE id=1")
            conn.commit()
        response = self.client.get('/api/artists/page?region=korea&limit=1&sort=popular')
        self.assertEqual(response.status_code, 200)
        page = response.json()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['artists'][0]['id'], 1)
        self.assertEqual(sum(region['count'] for region in page['regions']), page['all_total'])
        self.assertEqual(self.client.get('/api/artists/page?region=invalid').status_code, 400)

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
            albums_before=1,
            artists_before=1,
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

    def test_home_shelves_resolve_only_current_library_ids(self):
        response = self.client.get('/api/home?album_ids=2,999,1&artist_ids=2,1')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([a['id'] for a in data['recently_opened']], [2, 1])
        self.assertEqual([a['id'] for a in data['frequent_artists']], [2, 1])
        self.assertNotIn('recent_tracks', data)
        self.assertEqual(self.client.get('/api/home?album_ids=' + '1' * 513).status_code, 422)

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

        listed = self.client.get('/api/playlists').json()
        self.assertEqual(next(item for item in listed if item['id'] == playlist_id)['cover_album_ids'], [1])

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
        self.assertTrue(add_resp.json()["added"])
        added_paths = [item["path"] for item in add_resp.json()["libraries"]]
        self.assertIn("C:/SecondLibrary", added_paths)

        duplicate_resp = self.client.post("/api/config/libraries", json={"path": "C:/SecondLibrary"})
        self.assertEqual(duplicate_resp.status_code, 200)
        self.assertFalse(duplicate_resp.json()["added"])

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
        self.assertEqual(body["library_albums"], 2)
        self.assertEqual(body["library_tracks"], 2)
        self.assertEqual(body["albums_added"], 1)
        self.assertEqual(body["artists_added"], 1)

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

    def test_artist_image_route_uses_no_content_for_missing_local_image(self):
        missing_image_resp = self.client.get("/api/artists/1/image")
        self.assertEqual(missing_image_resp.status_code, 204)
        # A backfilled avatar must appear on the next load, so "no image yet"
        # must not be cacheable - unlike a real image, which is cached a day.
        self.assertEqual(missing_image_resp.headers["cache-control"], "no-store")

        missing_artist_resp = self.client.get("/api/artists/999/image")
        self.assertEqual(missing_artist_resp.status_code, 404)

    def _login_for_image_cookie(self):
        """Log in with a fixed key so the client carries the image cookie."""
        security = server.security.get_security_manager()
        original = security.get_or_create_api_key
        security.get_or_create_api_key = lambda: "session-key"
        try:
            with patch.object(server.security, "is_auth_enabled", return_value=True):
                resp = self.client.post("/api/security/login")
        finally:
            security.get_or_create_api_key = original
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_image_cookie_is_issued_for_cover_and_artist_paths(self):
        """An <img> element cannot send X-API-Key, so the cookie must exist.

        Before this, /api/artists/{id}/image had no cookie scope at all, so
        every artist card returned 401 and the library rendered no avatars
        even when image_path was populated.
        """
        set_cookie = self._login_for_image_cookie().headers.get_list("set-cookie")
        joined = " ".join(set_cookie)
        self.assertIn("velvet_cover_key=session-key", joined)
        self.assertIn("Path=/api/covers", joined)
        self.assertIn("Path=/api/artists", joined)
        cookie = next(c for c in set_cookie if c.startswith("velvet_cover_key="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)

    def test_artist_image_accepts_the_image_cookie_without_a_header(self):
        """The regression: an <img> request carries only the cookie."""
        seen = {}

        def capture(**kwargs):
            seen["api_key"] = kwargs.get("api_key")
            seen["record_failure"] = kwargs.get("record_failure")
            return {"allowed": kwargs.get("api_key") == "session-key",
                    "error": None}

        self._login_for_image_cookie()

        with patch.object(server.security, "is_auth_enabled", return_value=True), \
                patch.object(server.security, "verify_auth", side_effect=capture), \
                patch.dict(os.environ, {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "true"}), \
                patch.object(server, "get_client_ip", return_value="192.0.2.55"):
            resp = self.client.get("/api/artists/1/image")

        self.assertEqual(seen.get("api_key"), "session-key")
        self.assertFalse(seen.get("record_failure"))
        self.assertEqual(resp.status_code, 204)

    def test_image_cookie_does_not_unlock_other_api_routes(self):
        """Scoping must not turn the image cookie into a general credential."""
        self._login_for_image_cookie()
        captured = []

        def record(**kwargs):
            captured.append(kwargs.get("api_key"))
            return {"allowed": False, "error": "denied"}

        with patch.object(server.security, "is_auth_enabled", return_value=True), \
                patch.object(server.security, "verify_auth", side_effect=record), \
                patch.dict(os.environ, {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "true"}), \
                patch.object(server, "get_client_ip", return_value="192.0.2.55"):
            self.assertEqual(self.client.get("/api/config").status_code, 401)

        self.assertTrue(all(k != "session-key" for k in captured))

    def test_stale_image_cookie_does_not_count_toward_lockout(self):
        """A grid of <img> tags replays one stale cookie dozens of times.

        Those are not credential guesses; counting them would lock the owner
        out before they can log in again.
        """
        recorded = []

        def deny(**kwargs):
            if kwargs.get("record_failure"):
                recorded.append(kwargs.get("ip"))
            return {"allowed": False, "error": "Invalid password or API key"}

        with patch.object(server.security, "is_auth_enabled", return_value=True), \
                patch.object(server.security, "verify_auth", side_effect=deny), \
                patch.dict(os.environ, {"VELVET_AUTH_ENFORCE_ALL_NETWORKS": "true"}), \
                patch.object(server, "get_client_ip", return_value="192.0.2.55"):
            self.client.cookies.set("velvet_cover_key", "stale-key",
                                    path="/api/artists", domain="testserver")
            for artist_id in range(1, 12):
                self.client.get(f"/api/artists/{artist_id}/image")

        self.assertEqual(recorded, [])

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

    def _dsp_profile_count(self):
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM dsp_profiles").fetchone()[0]

    def test_create_custom_dsp_profile_rejects_invalid_filters_without_mutation(self):
        valid = {"type": "peaking", "freq": 1000, "gain": 2, "q": 1.0, "enabled": True}
        invalid_filters = [
            {**valid, "type": "PEAKING", "freq": "18"},       # below 20 Hz
            {**valid, "gain": "99"},                          # excessive gain
            {**valid, "q": "0.01"},                           # Q too small
            {**valid, "q": 0},
            {**valid, "freq": -1000},
            {**valid, "type": "unknown"},
            {**valid, "freq": "oops"},
        ]
        before = self._dsp_profile_count()
        for bad in invalid_filters:
            with self.subTest(bad=bad):
                resp = self.client.post("/api/dsp/profiles",
                                        json={"name": "Unsafe Custom", "filters": [valid, bad]})
                self.assertEqual(resp.status_code, 422)
                self.assertIn("filter 2", resp.json()["detail"])
        self.assertEqual(self._dsp_profile_count(), before)

    def test_dsp_api_rejects_non_finite_json_values(self):
        before = self._dsp_profile_count()
        for raw in [
            '{"name":"x","filters":[{"type":"peaking","freq":NaN,"gain":2,"q":1}]}',
            '{"name":"x","filters":[{"type":"peaking","freq":1000,"gain":Infinity,"q":1}]}',
            '{"name":"x","filters":[{"type":"peaking","freq":1000,"gain":2,"q":-Infinity}]}',
            '{"name":"x","preamp_db":NaN,"filters":[]}',
            '{"name":"x","stereo_width":Infinity}',
        ]:
            with self.subTest(raw=raw):
                resp = self.client.post("/api/dsp/profiles", content=raw,
                                        headers={"Content-Type": "application/json"})
                self.assertEqual(resp.status_code, 422)
        self.assertEqual(self._dsp_profile_count(), before)

    def test_dsp_api_validates_scalar_fields_and_filter_count(self):
        valid = {"type": "peaking", "freq": 1000, "gain": 2, "q": 1.0, "enabled": True}
        for body in [
            {"name": "x", "filters": [valid] * 21},
            {"name": "x", "preamp_db": "loud"},
            {"name": "x", "preamp_db": -31},
            {"name": "x", "preamp_db": 7},
            {"name": "x", "stereo_width": 500},
            {"name": "x", "reverb_room_size": -1},
            {"name": "x", "reverb_wet_dry": 101},
            {"name": "x", "category": "<script>"},
            {"name": "", "filters": []},
            {"name": "x" * 121},
            {"name": 5},
            {"name": "x", "filters": "not a list"},
            {"name": "x", "category": []},
            {"name": "x", "category": {"a": 1}},
            {"name": "x", "eq_bands": 1},
            {"name": "x", "eq_bands": [1]},
            {"name": "x", "eq_bands": "nope"},
            {"name": "x", "eq_bands": [{"freq": 1000, "gain": 99}]},
            {"name": "x", "volume_normalize": 2},
            {"name": "x", "volume_normalize": "true"},
            # Valid filters whose combined boost no allowed preamp can offset.
            {"name": "x", "filters": [{"type": "high_shelf", "freq": 19900, "gain": 12, "q": 10}]},
            {"name": "x", "filters": [{**valid, "gain": 18}] * 20},
        ]:
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.client.post("/api/dsp/profiles", json=body).status_code, 422)
        ok = self.client.post("/api/dsp/profiles",
                              json={"name": "x", "preamp_db": "-13.5", "volume_normalize": 1,
                                    "filters": [{**valid, "gain": 1}] * 20})
        self.assertEqual(ok.status_code, 200)
        saved = self.client.get(f"/api/dsp/profiles/{ok.json()['id']}").json()
        self.assertEqual(saved["preamp_db"], -13.5)
        self.assertEqual(len(server.json.loads(saved["filter_chain_json"])), 20)

    def test_update_with_invalid_payload_leaves_profile_unchanged(self):
        created = self.client.post("/api/dsp/profiles", json={
            "name": "Keep me", "preamp_db": -3,
            "filters": [{"type": "low_shelf", "freq": 90, "gain": 2, "q": 0.7, "enabled": True}],
        }).json()
        before = self.client.get(f"/api/dsp/profiles/{created['id']}").json()
        resp = self.client.put(f"/api/dsp/profiles/{created['id']}", json={
            "name": "Changed", "preamp_db": -1,
            "filters": [{"type": "peaking", "freq": 1000, "gain": 50, "q": 1.0}],
        })
        self.assertEqual(resp.status_code, 422)
        after = self.client.get(f"/api/dsp/profiles/{created['id']}").json()
        self.assertEqual(after, before)

    def test_shelf_and_peq_presets_round_trip_exactly(self):
        # EQ-2: the editor saves an untouched preset's filters as they are;
        # the stored chain keeps type, freq, gain, Q, enabled and order.
        factory = next(p for p in self.client.get("/api/dsp/profiles").json()
                       if p.get("preset_key") == "black_background")
        filters = server.json.loads(factory["filter_chain_json"])
        filters[1] = {**filters[1], "enabled": False}
        filters.reverse()
        created = self.client.post("/api/dsp/profiles", json={
            "name": "Round trip", "preamp_db": factory["preamp_db"], "stereo_width": 12,
            "filters": filters}).json()
        saved = self.client.get(f"/api/dsp/profiles/{created['id']}").json()
        self.assertEqual(server.json.loads(saved["filter_chain_json"]), filters)
        self.assertEqual(saved["preamp_db"], factory["preamp_db"])

        clone = self.client.post(f"/api/dsp/profiles/{created['id']}/clone",
                                 json={"name": "Round trip Copy"}).json()
        copied = self.client.get(f"/api/dsp/profiles/{clone['id']}").json()
        self.assertEqual(server.json.loads(copied["filter_chain_json"]), filters)
        self.assertEqual((copied["preamp_db"], copied["stereo_width"]), (saved["preamp_db"], 12))

    def test_legacy_eq_bands_payload_is_validated_and_converted(self):
        created = self.client.post("/api/dsp/profiles", json={
            "name": "Legacy client", "eq_bands": [{"freq": 1000, "gain": 3, "q": 1.0}]}).json()
        saved = self.client.get(f"/api/dsp/profiles/{created['id']}").json()
        self.assertEqual(server.json.loads(saved["filter_chain_json"]),
                         [{"type": "peaking", "freq": 1000.0, "gain": 3.0, "q": 1.0, "enabled": True}])

    def test_saving_an_empty_filter_list_bypasses_legacy_bands(self):
        with self._connect() as conn:
            profile_id = conn.execute(
                "INSERT INTO dsp_profiles (name, eq_bands, filter_chain_json) VALUES (?, ?, ?)",
                ("Legacy", '[{"freq":1000,"gain":6,"q":1}]', '[{"type":"peaking","freq":1000,"gain":6,"q":1}]'),
            ).lastrowid
            conn.commit()
        self.assertEqual(self.client.put(f"/api/dsp/profiles/{profile_id}",
                                         json={"filters": []}).status_code, 200)
        saved = self.client.get(f"/api/dsp/profiles/{profile_id}").json()
        self.assertEqual(saved["filter_chain_json"], "[]")
        self.assertEqual(server.build_dsp_filter_chain(saved), "")

    def test_resolved_dsp_plan_reports_combined_headroom(self):
        created = self.client.post("/api/dsp/profiles", json={
            "name": "Stacked", "preamp_db": -12,
            "filters": [{"type": "peaking", "freq": f, "gain": 12, "q": 1.41, "enabled": True}
                        for f in (500, 1000, 2000)],
        }).json()
        resp = self.client.get(f"/api/dsp/profiles/{created['id']}/resolved?sample_rate=48000")
        self.assertEqual(resp.status_code, 200)
        plan = resp.json()
        self.assertGreater(plan["peak_gain_db"], 13.0)
        self.assertLess(plan["recommended_preamp_db"], -14.0)
        # Saved -12 dB is insufficient: playback applies the recommended preamp.
        self.assertEqual(plan["preamp_db"], -12.0)
        self.assertEqual(plan["effective_preamp_db"], plan["recommended_preamp_db"])
        self.assertLess(plan["headroom_guard_db"], 0)
        self.assertFalse(plan["eq_clipping_risk"])
        self.assertEqual(plan["sample_rate"], 48000)
        self.assertEqual(self.client.get("/api/dsp/profiles/9999/resolved").status_code, 404)
        self.assertEqual(self.client.get(
            f"/api/dsp/profiles/{created['id']}/resolved?sample_rate=0").status_code, 422)

    def test_insufficient_saved_preamp_is_guarded_on_the_stream(self):
        # Codex review: +12 dB with preamp 0 saved fine and then played +12 dB.
        self._allow_music_root()
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET sample_rate=44100 WHERE id=1")
            conn.commit()
        created = self.client.post("/api/dsp/profiles", json={
            "name": "Hot", "preamp_db": 0,
            "filters": [{"type": "peaking", "freq": 1000, "gain": 12, "q": 1.0, "enabled": True}],
        })
        self.assertEqual(created.status_code, 200)
        captured = {}

        async def fake_stream(fp, dsp_filter, limit, **kwargs):
            captured["chain"] = dsp_filter
            return server.Response(b"", media_type="audio/flac")

        with patch.object(server, "_stream_with_dsp", side_effect=fake_stream):
            self.assertEqual(self.client.get(f"/api/stream/1?dsp={created.json()['id']}").status_code, 200)
        self.assertTrue(captured["chain"].startswith("volume=-13.0dB"))

    def test_stream_with_unknown_dsp_profile_fails_closed(self):
        self._allow_music_root()
        with patch.object(server.asyncio, "create_subprocess_exec",
                          side_effect=AssertionError("must not transcode")):
            for method in ("head", "get"):
                with self.subTest(method=method):
                    resp = getattr(self.client, method)("/api/stream/1?dsp=9999")
                    self.assertEqual(resp.status_code, 404)
        self.assertIn("DSP profile not found", resp.json()["detail"])

    AUTOEQ_TEXT = ("Preamp: -6.4 dB\n"
                   "Filter 1: ON LSC Fc 105 Hz Gain 6.3 dB Q 0.70\n"
                   "Filter 2: ON PK Fc 2840 Hz Gain 3.5 dB Q 2.43\n"
                   "Filter 3: OFF PK Fc 3720 Hz Gain 1.1 dB Q 4.67\n"
                   "Filter 4: ON HSC Fc 10000 Hz Gain -2.4 dB Q 0.70\n")

    def test_autoeq_import_saves_an_exact_correction_preset_and_exports_it_back(self):
        resp = self.client.post("/api/dsp/profiles/import/autoeq",
                                json={"name": "HD 600 (AutoEq)", "text": self.AUTOEQ_TEXT})
        self.assertEqual(resp.status_code, 200, resp.text)
        saved = self.client.get(f"/api/dsp/profiles/{resp.json()['id']}").json()
        self.assertEqual(saved["name"], "HD 600 (AutoEq)")
        self.assertEqual(saved["category"], "correction")
        self.assertEqual(json.loads(saved["tags"]), ["autoeq", "correction"])
        self.assertEqual(saved["is_factory"], 0)
        self.assertEqual(saved["preamp_db"], -6.4)
        self.assertEqual(json.loads(saved["filter_chain_json"]), [
            {"type": "low_shelf", "freq": 105.0, "gain": 6.3, "q": 0.7 / math.sqrt(2), "enabled": True},
            {"type": "peaking", "freq": 2840.0, "gain": 3.5, "q": 2.43, "enabled": True},
            {"type": "peaking", "freq": 3720.0, "gain": 1.1, "q": 4.67, "enabled": False},
            {"type": "high_shelf", "freq": 10000.0, "gain": -2.4, "q": 0.7 / math.sqrt(2), "enabled": True},
        ])

        exported = self.client.get(f"/api/dsp/profiles/{resp.json()['id']}/autoeq")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()["filename"], "HD 600 (AutoEq) ParametricEQ.txt")
        again = self.client.post("/api/dsp/profiles/import/autoeq",
                                 json={"name": "Again", "text": exported.json()["text"]}).json()
        copy = self.client.get(f"/api/dsp/profiles/{again['id']}").json()
        self.assertEqual(copy["filter_chain_json"], saved["filter_chain_json"])
        self.assertEqual(copy["preamp_db"], saved["preamp_db"])

    def test_autoeq_import_rejects_invalid_files_without_mutation(self):
        before = self._dsp_profile_count()
        for body in ({"name": "x", "text": "Filter 1: ON LS Fc 100 Hz Gain 2 dB"},
                     {"name": "x", "text": "Preamp: -1 dB"},
                     {"name": "", "text": self.AUTOEQ_TEXT},
                     {"name": "x", "text": 5},
                     {"name": "x", "text": "x" * 100_001}):
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.client.post("/api/dsp/profiles/import/autoeq", json=body).status_code, 422)
        self.assertEqual(self._dsp_profile_count(), before)
        self.assertEqual(self.client.get("/api/dsp/profiles/9999/autoeq").status_code, 404)

    def _capture_dsp_stream(self, url, tags):
        captured = {}

        async def fake_stream(fp, dsp_filter, limit, start_seconds=0):
            captured.update(chain=dsp_filter, start=start_seconds)
            return server.Response(b"", media_type="audio/flac")

        with patch.object(server, "_stream_with_dsp", side_effect=fake_stream), \
                patch.object(server, "read_replaygain_tags", return_value=tags):
            resp = self.client.get(url)
        return resp, captured.get("chain")

    def test_stream_applies_replaygain_without_a_dsp_preset(self):
        self._allow_music_root()
        tags = {"track_gain": -6.53, "track_peak": 0.98, "album_gain": -7.1, "album_peak": 0.99}
        resp, chain = self._capture_dsp_stream("/api/stream/1?replaygain=album", tags)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(chain, "volume=-7.1dB:precision=double")
        _, chain = self._capture_dsp_stream("/api/stream/1?replaygain=track", tags)
        self.assertEqual(chain, "volume=-6.53dB:precision=double")
        # No tags (or off): the source is streamed directly, bit for bit.
        for url, file_tags in (("/api/stream/1?replaygain=album", {}), ("/api/stream/1", tags)):
            with self.subTest(url=url):
                resp, chain = self._capture_dsp_stream(url, file_tags)
                self.assertEqual(resp.status_code, 200)
                self.assertIsNone(chain)
                self.assertEqual(resp.content, b"one")
        self.assertEqual(self.client.get("/api/stream/1?replaygain=loud").status_code, 422)

    def test_replaygain_only_transcode_accepts_a_start_offset(self):
        # EQ-3's start= applies to transcoded streams; a ReplayGain gain is one.
        self._allow_music_root()
        captured = {}

        async def fake_stream(fp, dsp_filter, limit, start_seconds=0):
            captured.update(chain=dsp_filter, start=start_seconds)
            return server.Response(b"", media_type="audio/flac")

        with patch.object(server, "_stream_with_dsp", side_effect=fake_stream):
            with patch.object(server, "read_replaygain_tags", return_value={"track_gain": -3.0}):
                self.assertEqual(self.client.get("/api/stream/1?replaygain=track&start=30").status_code, 200)
            self.assertEqual(captured, {"chain": "volume=-3.0dB:precision=double", "start": 30})
            # Untagged: nothing to transcode, so start is refused as before.
            with patch.object(server, "read_replaygain_tags", return_value={}):
                self.assertEqual(self.client.get("/api/stream/1?replaygain=track&start=30").status_code, 422)

    def test_track_replaygain_route_reports_what_the_stream_applies(self):
        self._allow_music_root()
        with patch.object(server, "read_replaygain_tags", return_value={"track_gain": 8.0, "track_peak": 0.5}):
            info = self.client.get("/api/tracks/1/replaygain?mode=album").json()
        self.assertEqual(info["source"], "track")
        self.assertAlmostEqual(info["applied_db"], 20 * math.log10(2))
        self.assertTrue(info["limited_by_peak"])
        self.assertEqual(self.client.get("/api/tracks/1/replaygain?mode=loud").status_code, 422)
        self.assertEqual(self.client.get("/api/tracks/999/replaygain?mode=album").status_code, 404)

    def _cast(self, body, transport_state="PLAYING", seek_ok=True, cast_result=None):
        sent = {}

        def fake_cast(udn, url, meta, play=True):
            sent.update(udn=udn, url=url, meta=meta, play=play)
            return cast_result if cast_result is not None else ("playing" if play else "loaded")

        with patch.object(upnp_server, "cast_track", side_effect=fake_cast), \
             patch.object(upnp_server, "get_transport_info", return_value=transport_state), \
             patch.object(upnp_server, "seek", return_value=seek_ok) as seek:
            response = self.client.post("/api/cast/renderer-1", json=body)
        return response, sent, seek

    def _hot_profile_at_44k(self):
        self._allow_music_root()
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET sample_rate=44100, bit_depth=16, file_size=1234 WHERE id=1")
            conn.commit()
        created = self.client.post("/api/dsp/profiles", json={
            "name": "Hot", "preamp_db": 0,
            "filters": [{"type": "peaking", "freq": 1000, "gain": 12, "q": 1.0, "enabled": True}],
        })
        self.assertEqual(created.status_code, 200)
        return created.json()["id"]

    def test_cast_applies_dsp_through_the_guarded_server_transcode(self):
        # EQ-3: a renderer gets the same DSP transcode as the browser, guarded
        # at the source rate, and its metadata describes that transcode.
        profile_id = self._hot_profile_at_44k()
        response, sent, seek = self._cast({"track_id": 1, "dsp_profile_id": profile_id})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["dsp"]["status"], "applied")
        self.assertEqual(body["dsp"]["effective_profile_id"], profile_id)
        self.assertEqual(body["dsp"]["stream"], "dsp_transcode")
        self.assertLess(body["dsp"]["headroom_guard_db"], 0)
        self.assertEqual(body["start_offset"], 0)
        self.assertIn(f"&dsp={profile_id}", sent["url"])
        self.assertNotIn("start=", sent["url"])
        self.assertIn("http-get:*:audio/flac:DLNA.ORG_PN=FLAC;DLNA.ORG_OP=00;DLNA.ORG_CI=1", sent["meta"])
        self.assertIn('bitsPerSample="24"', sent["meta"])
        self.assertNotIn('size="1234"', sent["meta"])
        seek.assert_not_called()

        captured = {}

        async def fake_stream(fp, dsp_filter, limit, start_seconds=0):
            captured.update(chain=dsp_filter, start=start_seconds)
            return server.Response(b"", media_type="audio/flac")

        path = sent["url"].split(f":{upnp_server.MAIN_PORT}", 1)[1]
        with patch.object(server, "_stream_with_dsp", side_effect=fake_stream):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertTrue(captured["chain"].startswith("volume=-13.0dB"), captured["chain"])
        self.assertEqual(captured["start"], 0)

    def test_cast_resumes_a_dsp_transcode_at_the_requested_position(self):
        profile_id = self._hot_profile_at_44k()
        response, sent, seek = self._cast({"track_id": 1, "dsp_profile_id": profile_id,
                                           "start_seconds": 42.5})
        self.assertEqual(response.json()["start_offset"], 42.5)
        self.assertIn(f"&dsp={profile_id}&start=42.5", sent["url"])
        self.assertIn('duration="0:02:17.500"', sent["meta"])  # 180 s track, remaining part
        seek.assert_not_called()

        captured = {}

        async def fake_stream(fp, dsp_filter, limit, start_seconds=0):
            captured["start"] = start_seconds
            return server.Response(b"", media_type="audio/flac")

        path = sent["url"].split(f":{upnp_server.MAIN_PORT}", 1)[1]
        with patch.object(server, "_stream_with_dsp", side_effect=fake_stream):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(captured["start"], 42.5)

    def test_cast_without_active_dsp_streams_the_direct_source(self):
        self._allow_music_root()
        empty = self.client.post("/api/dsp/profiles", json={"name": "Empty", "preamp_db": 0, "filters": []})
        self.assertEqual(empty.status_code, 200)
        for body, status in (({"track_id": 1, "dsp_profile_id": empty.json()["id"]}, "bypassed"),
                             ({"track_id": 1}, "off")):
            with self.subTest(status=status):
                response, sent, seek = self._cast({**body, "start_seconds": 30})
                result = response.json()
                self.assertEqual(result["dsp"]["status"], status)
                self.assertIsNone(result["dsp"]["effective_profile_id"])
                self.assertEqual(result["dsp"]["stream"], "direct")
                self.assertNotIn("dsp=", sent["url"])
                self.assertNotIn("start=", sent["url"])
                self.assertNotIn("DLNA.ORG_CI=1", sent["meta"])
                # A direct stream resumes by seeking the renderer; positions stay absolute.
                seek.assert_called_once_with("renderer-1", 30.0)
                self.assertEqual(result["start_offset"], 0)

    def test_cast_applies_replaygain_through_the_transcode(self):
        # A renderer gets ReplayGain the way a browser does: the 24-bit FLAC
        # transcode with &replaygain=, resumed at the requested position.
        self._hot_profile_at_44k()
        tags = {"track_gain": -6.53, "track_peak": 0.98, "album_gain": -7.1, "album_peak": 0.99}
        with patch.object(server, "read_replaygain_tags", return_value=tags):
            response, sent, seek = self._cast({"track_id": 1, "replaygain": "album", "start_seconds": 42.5})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["replaygain"]["mode"], "album")
        self.assertEqual(body["replaygain"]["applied_db"], -7.1)
        self.assertEqual(body["dsp"]["status"], "off")
        self.assertEqual(body["dsp"]["stream"], "dsp_transcode")
        self.assertEqual(body["start_offset"], 42.5)
        self.assertNotIn("dsp=", sent["url"])
        self.assertIn("&replaygain=album&start=42.5", sent["url"])
        self.assertIn("http-get:*:audio/flac:DLNA.ORG_PN=FLAC;DLNA.ORG_OP=00;DLNA.ORG_CI=1", sent["meta"])
        self.assertIn('duration="0:02:17.500"', sent["meta"])
        seek.assert_not_called()

        path = sent["url"].split(f":{upnp_server.MAIN_PORT}", 1)[1]
        resp, chain = self._capture_dsp_stream(path, tags)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(chain, "volume=-7.1dB:precision=double")

    def test_cast_combines_replaygain_with_a_dsp_preset(self):
        profile_id = self._hot_profile_at_44k()
        tags = {"track_gain": -3.0}
        with patch.object(server, "read_replaygain_tags", return_value=tags):
            response, sent, _ = self._cast({"track_id": 1, "dsp_profile_id": profile_id, "replaygain": "track"})
        self.assertEqual(response.json()["replaygain"]["applied_db"], -3.0)
        self.assertIn(f"&dsp={profile_id}&replaygain=track", sent["url"])
        path = sent["url"].split(f":{upnp_server.MAIN_PORT}", 1)[1]
        _, chain = self._capture_dsp_stream(path, tags)
        # ReplayGain comes before the preamp and the EQ's guarded preamp.
        self.assertTrue(chain.startswith("volume=-3.0dB:precision=double,volume=-13.0dB"), chain)

    def test_cast_without_a_replaygain_gain_streams_the_direct_source(self):
        self._allow_music_root()
        for body, tags, reason in (({"replaygain": "album"}, {}, "No ReplayGain tags"),
                                   ({"replaygain": "off"}, {"track_gain": -3.0}, "ReplayGain off"),
                                   ({}, {"track_gain": -3.0}, "ReplayGain off")):
            with self.subTest(body=body), patch.object(server, "read_replaygain_tags", return_value=tags):
                response, sent, seek = self._cast({"track_id": 1, "start_seconds": 30, **body})
                result = response.json()
                self.assertEqual(result["replaygain"]["applied_db"], 0.0)
                self.assertEqual(result["replaygain"]["reason"], reason)
                self.assertEqual(result["dsp"]["stream"], "direct")
                self.assertNotIn("replaygain=", sent["url"])
                self.assertNotIn("start=", sent["url"])
                seek.assert_called_once_with("renderer-1", 30.0)
                self.assertEqual(result["start_offset"], 0)

    def test_recast_keeps_a_paused_renderer_paused(self):
        self._allow_music_root()
        tags = {"track_gain": -3.0}
        cases = (
            # (body, renderer state, Play sent, transport)
            ({"recast": True}, "PAUSED_PLAYBACK", False, "loaded"),
            ({"recast": True}, "PLAYING", True, "playing"),
            ({}, "PAUSED_PLAYBACK", True, "playing"),  # a new track always plays
        )
        for extra, renderer_state, plays, transport in cases:
            with self.subTest(extra=extra, renderer_state=renderer_state), \
                    patch.object(server, "read_replaygain_tags", return_value=tags):
                response, sent, seek = self._cast({"track_id": 1, "replaygain": "track",
                                                   "start_seconds": 42, **extra},
                                                  transport_state=renderer_state)
                body = response.json()
                self.assertEqual(sent["play"], plays)
                self.assertTrue(body["success"])
                self.assertEqual(body["transport"], transport)
                self.assertEqual(body["start_offset"], 42)
                seek.assert_not_called()
        # A paused direct stream is loaded, then seeked without playing.
        response, sent, seek = self._cast({"track_id": 1, "start_seconds": 30, "recast": True},
                                          transport_state="PAUSED_PLAYBACK")
        self.assertFalse(sent["play"])
        seek.assert_called_once_with("renderer-1", 30.0)
        self.assertEqual(response.json()["transport"], "loaded")
        self.assertTrue(response.json()["position_restored"])

    def test_cast_reports_seek_and_play_failures(self):
        self._allow_music_root()
        response, _, _ = self._cast({"track_id": 1, "start_seconds": 30}, seek_ok=False)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertIs(body["position_restored"], False)  # never claimed as resumed
        self.assertEqual(body["start_offset"], 0)
        response, _, seek = self._cast({"track_id": 1, "start_seconds": 30}, cast_result="play_failed")
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["transport"], "play_failed")
        response, _, seek = self._cast({"track_id": 1, "start_seconds": 30}, cast_result=False)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["transport"], "failed")
        seek.assert_not_called()
        self.assertEqual(self.client.post("/api/cast/renderer-1",
                                          json={"track_id": 1, "recast": "yes"}).status_code, 422)

    def test_casts_to_one_renderer_run_in_order_and_skip_superseded_ones(self):
        # A: in progress. B: waiting, superseded by C. Stop then cancels D.
        # The renderer must see A, C, Stop only, in that order.
        profile_id = self._hot_profile_at_44k()
        a_started, release_a = threading.Event(), threading.Event()
        renderer = []
        results = {}

        def fake_cast(udn, url, meta, play=True):
            renderer.append(url)
            if len(renderer) == 1:
                a_started.set()
                self.assertTrue(release_a.wait(10))
            return "playing"

        def control(udn, action):
            renderer.append(action)
            return True

        def post(name, path, body=None):
            results[name] = self.client.post(path, json=body).json()

        def generation():
            return server._cast_generations.get("renderer-1", 0)

        def start(name, path, body=None):
            before = generation()
            thread = threading.Thread(target=post, args=(name, path, body))
            thread.start()
            deadline = time.monotonic() + 10
            while generation() == before and time.monotonic() < deadline:
                time.sleep(0.01)
            return thread

        with patch.object(upnp_server, "cast_track", side_effect=fake_cast), \
             patch.object(upnp_server, "cast_control", side_effect=control), \
             patch.object(upnp_server, "seek", return_value=True), \
             patch.object(server, "read_replaygain_tags", return_value={"track_gain": -3.0}):
            threads = [threading.Thread(target=post, args=("a", "/api/cast/renderer-1", {"track_id": 1}))]
            threads[0].start()
            self.assertTrue(a_started.wait(10))
            threads.append(start("b", "/api/cast/renderer-1", {"track_id": 1, "replaygain": "track"}))
            threads.append(start("c", "/api/cast/renderer-1", {"track_id": 1, "dsp_profile_id": profile_id}))
            release_a.set()
            for thread in threads:
                thread.join(10)
            self.assertEqual(len(renderer), 2, renderer)
            self.assertIn(f"dsp={profile_id}", renderer[1])
            self.assertEqual(results["b"], {"success": False, "superseded": True})
            self.assertTrue(results["c"]["success"])

            # Leaving the renderer: a cast queued behind an in-progress one is cancelled.
            renderer.clear()
            a_started.clear()
            release_a.clear()
            threads = [threading.Thread(target=post, args=("a2", "/api/cast/renderer-1", {"track_id": 1}))]
            threads[0].start()
            self.assertTrue(a_started.wait(10))
            threads.append(start("d", "/api/cast/renderer-1", {"track_id": 1, "replaygain": "track"}))
            threads.append(start("stop", "/api/cast/control/renderer-1/Stop"))
            release_a.set()
            for thread in threads:
                thread.join(10)
        self.assertEqual(renderer[1:], ["Stop"], renderer)
        self.assertTrue(results["d"]["superseded"])
        self.assertTrue(results["stop"]["success"])

    class _FakeRenderer:
        """A renderer whose first cast blocks until released. It always reports
        a stale transport state, so only VELVET's own ordering decides."""

        def __init__(self, test, reported_state="PLAYING"):
            self.test, self.reported_state = test, reported_state
            self.log, self.results, self.threads = [], {}, []
            self.first_started, self.release = threading.Event(), threading.Event()

        def cast_track(self, udn, url, meta, play=True):
            self.log.append(("cast", "replaygain=" in url, play))
            if len(self.log) == 1:
                self.first_started.set()
                self.test.assertTrue(self.release.wait(10))
            return "playing" if play else "loaded"

        def cast_control(self, udn, action):
            self.log.append(("control", action))
            return True

        def patches(self):
            return (patch.object(upnp_server, "cast_track", side_effect=self.cast_track),
                    patch.object(upnp_server, "cast_control", side_effect=self.cast_control),
                    patch.object(upnp_server, "get_transport_info", return_value=self.reported_state),
                    patch.object(upnp_server, "seek", return_value=True),
                    patch.object(server, "read_replaygain_tags", return_value={"track_gain": -3.0}))

        def send(self, name, path, body=None, udn="renderer-1"):
            """Post on a thread and wait until the request holds its turn."""
            dispatcher = server._renderer_dispatcher(udn)
            before = dispatcher.submitted
            thread = threading.Thread(target=lambda: self.results.__setitem__(
                name, self.test.client.post(path, json=body).json()))
            thread.start()
            self.threads.append(thread)
            deadline = time.monotonic() + 10
            while dispatcher.submitted == before and time.monotonic() < deadline:
                time.sleep(0.01)
            self.test.assertGreater(dispatcher.submitted, before, f"{name} never queued")

        def finish(self):
            self.release.set()
            for thread in self.threads:
                thread.join(10)

    def _run_renderer_script(self, steps, reported_state="PLAYING"):
        self._allow_music_root()
        renderer = self._FakeRenderer(self, reported_state)
        patches = renderer.patches()
        for p in patches:
            p.start()
        try:
            for i, (name, path, body) in enumerate(steps):
                renderer.send(name, path, body)
                if i == 0:
                    self.assertTrue(renderer.first_started.wait(10))
            renderer.finish()
        finally:
            for p in patches:
                p.stop()
        return renderer

    def test_pause_queued_behind_a_cast_is_kept_and_wins_over_a_later_recast(self):
        # Cast A in progress -> Pause queued -> re-cast B: B must not resume playback.
        renderer = self._run_renderer_script([
            ("a", "/api/cast/renderer-1", {"track_id": 1}),
            ("pause", "/api/cast/control/renderer-1/Pause", None),
            ("b", "/api/cast/renderer-1", {"track_id": 1, "replaygain": "track", "recast": True}),
        ], reported_state="PLAYING")
        self.assertEqual(renderer.log, [("cast", False, True), ("control", "Pause"), ("cast", True, False)])
        self.assertTrue(renderer.results["pause"]["success"])
        self.assertEqual(renderer.results["b"]["transport"], "loaded")

    def test_play_queued_behind_a_cast_is_kept_and_wins_over_a_later_recast(self):
        renderer = self._run_renderer_script([
            ("a", "/api/cast/renderer-1", {"track_id": 1, "recast": True}),
            ("play", "/api/cast/control/renderer-1/Play", None),
            ("b", "/api/cast/renderer-1", {"track_id": 1, "replaygain": "track", "recast": True}),
        ], reported_state="PAUSED_PLAYBACK")
        self.assertEqual(renderer.log, [("cast", False, False), ("control", "Play"), ("cast", True, True)])
        self.assertTrue(renderer.results["play"]["success"])
        self.assertEqual(renderer.results["b"]["transport"], "playing")

    def test_stop_cancels_waiting_casts_but_not_queued_controls(self):
        renderer = self._run_renderer_script([
            ("a", "/api/cast/renderer-1", {"track_id": 1}),
            ("pause", "/api/cast/control/renderer-1/Pause", None),
            ("b", "/api/cast/renderer-1", {"track_id": 1, "replaygain": "track", "recast": True}),
            ("stop", "/api/cast/control/renderer-1/Stop", None),
        ])
        self.assertEqual(renderer.log, [("cast", False, True), ("control", "Pause"), ("control", "Stop")])
        self.assertEqual(renderer.results["b"], {"success": False, "superseded": True})
        self.assertTrue(renderer.results["stop"]["success"])
        self.assertNotIn("renderer-1", server._cast_user_paused)

    def test_pause_intent_survives_a_failed_recast(self):
        # Pause -> re-cast fails -> retry: the retry must still not start playback,
        # even though the renderer (stale) reports PLAYING.
        self._allow_music_root()
        plays = []
        results = iter([False, "loaded"])

        def fake_cast(udn, url, meta, play=True):
            plays.append(play)
            return next(results)

        with patch.object(upnp_server, "cast_control", return_value=True), \
             patch.object(upnp_server, "cast_track", side_effect=fake_cast), \
             patch.object(upnp_server, "get_transport_info", return_value="PLAYING"), \
             patch.object(upnp_server, "seek", return_value=True):
            self.assertTrue(self.client.post("/api/cast/control/renderer-rg/Pause").json()["success"])
            failed = self.client.post("/api/cast/renderer-rg", json={"track_id": 1, "recast": True}).json()
            self.assertEqual(failed["transport"], "failed")
            self.assertTrue(server._cast_user_paused["renderer-rg"], "intent kept after a failed cast")
            retry = self.client.post("/api/cast/renderer-rg", json={"track_id": 1, "recast": True}).json()
        self.assertEqual(plays, [False, False])
        self.assertEqual(retry["transport"], "loaded")
        self.assertNotIn("renderer-rg", server._cast_user_paused, "honoured by the confirmed cast")

    def _slow_resolve(self, first_only=False):
        """Patch stream resolution to wait until `release` is set (only the first call if `first_only`)."""
        release, entered = threading.Event(), threading.Event()
        original = server._resolve_cast_stream
        calls = []

        def resolve(*args):
            calls.append(args)
            if not first_only or len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(10))
            return original(*args)

        return patch.object(server, "_resolve_cast_stream", side_effect=resolve), release, entered

    def _ordered_with_slow_resolve(self, cast_body, control, reported_state):
        self._allow_music_root()
        log = []
        resolve_patch, release, entered = self._slow_resolve()
        results = {}

        def fake_cast(udn, url, meta, play=True):
            log.append(("cast", play))
            return "playing" if play else "loaded"

        def fake_control(udn, action):
            log.append(("control", action))
            return True

        def post(name, path, body=None):
            results[name] = self.client.post(path, json=body).json()

        dispatcher = server._renderer_dispatcher("renderer-slow")
        with resolve_patch, \
                patch.object(upnp_server, "cast_track", side_effect=fake_cast), \
                patch.object(upnp_server, "cast_control", side_effect=fake_control), \
                patch.object(upnp_server, "get_transport_info", return_value=reported_state), \
                patch.object(upnp_server, "seek", return_value=True):
            cast = threading.Thread(target=post, args=("cast", "/api/cast/renderer-slow", cast_body))
            cast.start()
            self.assertTrue(entered.wait(10))  # the cast is resolving its stream
            before = dispatcher.submitted
            ctl = threading.Thread(target=post, args=("control", f"/api/cast/control/renderer-slow/{control}"))
            ctl.start()
            deadline = time.monotonic() + 10
            while dispatcher.submitted == before and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.1)
            self.assertEqual(log, [], "the control waits for the earlier cast")
            release.set()
            cast.join(10)
            ctl.join(10)
        return log, results

    def test_pause_sent_while_a_new_track_resolves_is_not_undone(self):
        log, results = self._ordered_with_slow_resolve({"track_id": 1}, "Pause", "PLAYING")
        # The cast runs first (it was requested first); the Pause after it wins.
        self.assertEqual(log, [("cast", True), ("control", "Pause")])
        self.assertTrue(results["control"]["success"])
        self.assertTrue(server._cast_user_paused["renderer-slow"])

    def test_play_sent_while_a_recast_resolves_is_not_undone(self):
        log, results = self._ordered_with_slow_resolve(
            {"track_id": 1, "recast": True}, "Play", "PAUSED_PLAYBACK")
        self.assertEqual(log, [("cast", False), ("control", "Play")])
        self.assertEqual(results["cast"]["transport"], "loaded")
        self.assertFalse(server._cast_user_paused["renderer-slow"])

    def _supersede_a_resolving_cast(self, path, body):
        """Cast A blocks in stream resolution; `path` is sent and must not wait for A."""
        self._allow_music_root()
        log, results = [], {}
        resolve_patch, release, entered = self._slow_resolve(first_only=True)
        sent = threading.Event()

        def fake_cast(udn, url, meta, play=True):
            log.append(("cast", "dsp=" in url, play))
            sent.set()
            return "playing"

        def fake_control(udn, action):
            log.append(("control", action))
            sent.set()
            return True

        def post(name, url, json_body=None):
            results[name] = self.client.post(url, json=json_body).json()

        with resolve_patch, \
                patch.object(upnp_server, "cast_track", side_effect=fake_cast), \
                patch.object(upnp_server, "cast_control", side_effect=fake_control), \
                patch.object(upnp_server, "get_transport_info", return_value="PLAYING"), \
                patch.object(upnp_server, "seek", return_value=True):
            a = threading.Thread(target=post, args=("a", "/api/cast/renderer-sup", {"track_id": 1}))
            a.start()
            self.assertTrue(entered.wait(10))  # A holds the queue, waiting for its stream
            b = threading.Thread(target=post, args=("b", path, body))
            b.start()
            # B reaches SOAP while A's lookup is still blocked.
            self.assertTrue(sent.wait(5), "the newer request waited for the superseded lookup")
            b.join(10)
            self.assertTrue(a.is_alive(), "A's lookup is still blocked")
            release.set()
            a.join(10)
        return log, results

    def test_stop_is_not_held_up_by_a_superseded_cast_still_resolving(self):
        log, results = self._supersede_a_resolving_cast("/api/cast/control/renderer-sup/Stop", None)
        self.assertEqual(log, [("control", "Stop")])  # and A never plays afterwards
        self.assertTrue(results["b"]["success"])
        self.assertEqual(results["a"], {"success": False, "superseded": True})

    def test_a_newer_cast_is_not_held_up_by_an_older_one_still_resolving(self):
        profile_id = self._hot_profile_at_44k()
        log, results = self._supersede_a_resolving_cast(
            "/api/cast/renderer-sup", {"track_id": 1, "dsp_profile_id": profile_id})
        self.assertEqual(log, [("cast", True, True)])
        self.assertTrue(results["b"]["success"])
        self.assertEqual(results["a"], {"success": False, "superseded": True})

    def test_a_superseded_cast_keeps_its_result_when_its_lookup_fails_later(self):
        self._allow_music_root()
        release, entered = threading.Event(), threading.Event()

        def failing_resolve(*args):
            entered.set()
            self.assertTrue(release.wait(10))
            raise server.HTTPException(404, "Track not found")

        results = {}

        def post():
            results["a"] = self.client.post("/api/cast/renderer-first",
                                            json={"track_id": 1, "request_id": "first-a"})

        with patch.object(server, "_resolve_cast_stream", side_effect=failing_resolve), \
                patch.object(upnp_server, "cast_control", return_value=True), \
                patch.object(upnp_server, "cast_track", side_effect=AssertionError("must not cast")):
            a = threading.Thread(target=post)
            a.start()
            self.assertTrue(entered.wait(10))
            self.assertTrue(self.client.post("/api/cast/control/renderer-first/Stop").json()["success"])
            superseded = {"done": True, "success": False, "superseded": True}
            self.assertEqual(self.client.get("/api/cast/result/renderer-first/first-a").json(), superseded)
            release.set()  # A's lookup now fails
            a.join(10)
        self.assertEqual(results["a"].status_code, 404)
        self.assertEqual(self.client.get("/api/cast/result/renderer-first/first-a").json(), superseded)

    def test_a_cast_that_fails_or_is_cancelled_while_resolving_is_recorded(self):
        self._allow_music_root()
        with patch.object(server, "_resolve_cast_stream", side_effect=RuntimeError("disk gone")), \
                patch.object(upnp_server, "cast_track", side_effect=AssertionError("must not cast")):
            client = TestClient(server.app, raise_server_exceptions=False)
            response = client.post("/api/cast/renderer-fail", json={"track_id": 1, "request_id": "boom"})
            self.assertEqual(response.status_code, 500)
            self.assertEqual(self.client.get("/api/cast/result/renderer-fail/boom").json(),
                             {"done": True, "success": False, "error": "Cast failed"})

        class FakeRequest:
            async def json(self):
                return {"track_id": 1, "request_id": "gone"}

        resolve_patch, release, entered = self._slow_resolve()

        async def scenario():
            task = asyncio.ensure_future(server.cast_to_device("renderer-fail", FakeRequest()))
            await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            await asyncio.wrap_future(server._renderer_dispatcher("renderer-fail").submit(lambda: None))

        with resolve_patch, patch.object(upnp_server, "cast_track", side_effect=AssertionError("must not cast")):
            asyncio.run(scenario())
        self.assertEqual(self.client.get("/api/cast/result/renderer-fail/gone").json(),
                         {"done": True, "success": False, "cancelled": True})

    def test_cast_status_exposes_only_ticket_free_track_id(self):
        with patch.object(upnp_server, "get_transport_info", return_value="PLAYING"), \
             patch.object(upnp_server, "get_position_info",
                          return_value=(7, 120, 42)) as position:
            response = self.client.get("/api/cast/status/renderer-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "state": "PLAYING",
            "position": 7,
            "duration": 120,
            "track_id": 42,
            "is_playing": True,
        })
        position.assert_called_once_with("renderer-1", include_track_id=True)

    def test_play_and_pause_work_before_any_cast_to_the_renderer(self):
        calls = []
        with patch.object(upnp_server, "cast_control", side_effect=lambda udn, action: calls.append(action) or True):
            for action in ("Pause", "Play"):
                with self.subTest(action=action):
                    response = self.client.post(f"/api/cast/control/never-cast-{action}/{action}")
                    self.assertEqual(response.json(), {"success": True})
        self.assertEqual(calls, ["Pause", "Play"])

    def test_cast_outcome_can_be_fetched_after_a_client_timeout(self):
        self._allow_music_root()
        renderer = self._FakeRenderer(self)
        patches = renderer.patches()
        for p in patches:
            p.start()
        try:
            renderer.send("a", "/api/cast/renderer-1", {"track_id": 1, "request_id": "req-a"})
            self.assertTrue(renderer.first_started.wait(10))
            self.assertEqual(self.client.get("/api/cast/result/renderer-1/req-a").json(), {"done": False})
            renderer.finish()
            # A request that fails before its turn gives the turn up: later casts still run.
            missing = self.client.post("/api/cast/renderer-1", json={"track_id": 999, "request_id": "req-x"})
            self.assertEqual(missing.status_code, 404)
            after = self.client.post("/api/cast/renderer-1", json={"track_id": 1})
            self.assertTrue(after.json()["success"])
        finally:
            for p in patches:
                p.stop()
        result = self.client.get("/api/cast/result/renderer-1/req-a").json()
        self.assertEqual((result["done"], result["success"], result["transport"]), (True, True, "playing"))
        self.assertEqual(self.client.get("/api/cast/result/renderer-1/req-x").json(),
                         {"done": True, "success": False, "error": "Track not found"})
        self.assertEqual(self.client.get("/api/cast/result/renderer-1/unknown").status_code, 404)
        self.assertEqual(self.client.post("/api/cast/renderer-1",
                                          json={"track_id": 1, "request_id": 5}).status_code, 422)

    def test_cast_with_unknown_or_invalid_dsp_fails_closed(self):
        self._allow_music_root()
        with patch.object(upnp_server, "cast_track",
                          side_effect=AssertionError("must not cast")):
            missing = self.client.post("/api/cast/renderer-1", json={"track_id": 1, "dsp_profile_id": 9999})
            self.assertEqual(missing.status_code, 404)
            self.assertIn("DSP profile not found", missing.json()["detail"])
            for body in ({"dsp_profile_id": "2"}, {"dsp_profile_id": True}, {"start_seconds": -1},
                         {"start_seconds": "10"}, {"start_seconds": True},
                         {"replaygain": "loud"}, {"replaygain": 1}, {"replaygain": None}):
                with self.subTest(body=body):
                    response = self.client.post("/api/cast/renderer-1", json={"track_id": 1, **body})
                    self.assertEqual(response.status_code, 422)

    def test_stream_start_offset_requires_a_transcode(self):
        self._allow_music_root()
        self.assertEqual(self.client.get("/api/stream/1?start=5").status_code, 422)
        self.assertEqual(self.client.get("/api/stream/1?start=-1").status_code, 422)

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

    def test_vu_mirror_direct_stream_needs_no_transcode_slot(self):
        # The VU mirror requests upsample=none without DSP. With every FFmpeg
        # slot taken (main playback + other clients) it must still be served
        # and must not hold a slot the main player needs; a duplicate DSP
        # request in the same state is the 503 the mirror used to risk.
        self._allow_music_root()
        with self._connect() as conn:
            profile_id = conn.execute(
                "INSERT INTO dsp_profiles (name, preamp_db) VALUES ('Mirror test', -3)").lastrowid
            conn.commit()
        leases = [server.TRANSCODE_SLOTS.try_acquire() for _ in range(server.TRANSCODE_SLOTS.limit)]
        try:
            self.assertTrue(all(leases))
            mirror = self.client.get("/api/stream/1?upsample=none", headers={"Range": "bytes=0-3"})
            self.assertIn(mirror.status_code, {200, 206})
            self.assertEqual(server.TRANSCODE_SLOTS.active, server.TRANSCODE_SLOTS.limit)
            with patch.object(server, "TRANSCODE_SLOT_WAIT_SECONDS", 0.05), \
                 patch.object(server.asyncio, "create_subprocess_exec",
                              side_effect=AssertionError("no slot, no FFmpeg")):
                duplicate = self.client.get(f"/api/stream/1?dsp={profile_id}")
            self.assertEqual(duplicate.status_code, 503)
        finally:
            for lease in leases:
                lease.release()
        self.assertEqual(server.TRANSCODE_SLOTS.active, 0)

    def test_direct_stream_sends_dlna_headers_only_when_requested(self):
        self._allow_music_root()
        ask = {"getcontentFeatures.dlna.org": "1"}
        features = upnp_server._get_dlna_profile("audio/flac", "FLAC")
        self.assertIn("DLNA.ORG_PN=FLAC;DLNA.ORG_OP=01", features)
        for method, headers, status in [
            ("GET", {}, 200), ("HEAD", {}, 200),
            ("GET", {"Range": "bytes=0-1"}, 206), ("HEAD", {"Range": "bytes=0-1"}, 206),
        ]:
            with self.subTest(method=method, headers=headers):
                plain = self.client.request(method, "/api/stream/1", headers=headers)
                self.assertEqual(plain.status_code, status)
                self.assertNotIn("transferMode.dlna.org", plain.headers)
                self.assertNotIn("contentFeatures.dlna.org", plain.headers)

                dlna = self.client.request(method, "/api/stream/1", headers={**headers, **ask})
                self.assertEqual(dlna.status_code, status)
                self.assertEqual(dlna.headers["transferMode.dlna.org"], "Streaming")
                self.assertEqual(dlna.headers["contentFeatures.dlna.org"], features)
        mp3 = self.client.get("/api/stream/2", headers=ask)
        self.assertIn("DLNA.ORG_PN=MP3;", mp3.headers["contentFeatures.dlna.org"])

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

        def fake_cast(udn, url, meta, play=True):
            sent.update(udn=udn, url=url, meta=meta)
            return "playing"

        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth", return_value={"allowed": True}), \
             patch.object(upnp_server, "cast_track", side_effect=fake_cast):
            response = self.client.post("/api/cast/renderer-1", json={"track_id": 1},
                                        headers={"X-API-Key": "session-key"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
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
            # Rotating the API key revokes outstanding browser tickets, and the
            # rotation response itself carries a ticket valid under the new secret.
            rotated = self.client.post("/api/security/generate-api-key", headers=key).json()
            self.assertEqual(self.client.get(f"/api/stream/1?ticket={ticket}").status_code, 401)
            self.assertIn(self.client.get(f"/api/stream/1?ticket={rotated['stream_ticket']}",
                                          headers={"Range": "bytes=0-1"}).status_code, {200, 206})

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


    def test_modular_frontend_files_are_public_but_allowlisted(self):
        with patch.object(server.security, "is_auth_enabled", return_value=True), \
             patch.object(server.security, "verify_auth",
                          return_value={"allowed": False, "error": "not authorized"}), \
             patch.object(server, "get_client_ip", return_value="192.168.0.20"):
            for name in server.PUBLIC_FRONTEND_JS_MODULES:
                response = self.client.get(f"/static/js/{name}")
                self.assertEqual(response.status_code, 200, name)
                self.assertIn("javascript", response.headers["content-type"])
            self.assertEqual(self.client.get("/api/stats").status_code, 401)
            self.assertNotEqual(self.client.get("/static/js/server.py").status_code, 200)

    def test_every_shell_and_precached_module_is_served(self):
        # A module the shell or service worker loads but the server 404s
        # silently disables that feature (e.g. the VU meter engine).
        root = Path(server.__file__).parent
        shell = (root / "static" / "index.html").read_text(encoding="utf-8")
        worker = (root / "sw.js").read_text(encoding="utf-8")
        referenced = set(re.findall(r"/static/js/([\w.-]+\.js)", shell + worker))
        self.assertIn("vu-meter-engine.js", referenced)
        self.assertEqual(referenced - server.PUBLIC_FRONTEND_JS_MODULES, set())

    def test_duplicate_scanner_runs_as_background_read_only_job(self):
        from library_jobs import JobState
        result = {"groups": [], "groups_found": 0, "candidate_files": 0,
                  "files_examined": 0, "cached_hashes": 0, "truncated": False}
        job = JobState({"running": False, "groups": [], "error": None})
        with patch.object(server, "_duplicate_job", job), \
             patch.object(server, "find_exact_duplicates", return_value=result) as finder, \
             patch.object(server._scan_job, "is_running", return_value=False):
            response = self.client.post("/api/library/duplicates/scan")
            self.assertEqual(response.status_code, 202)
            status = self.client.get("/api/library/duplicates").json()
            self.assertFalse(status["running"])
            self.assertEqual(status["groups"], [])
            self.assertIsNone(status["error"])
            finder.assert_called_once()


    def test_library_scan_and_refresh_do_not_start_during_duplicate_check(self):
        from library_jobs import JobState
        server._scan_job.finish()
        with self._connect() as conn:
            conn.execute("INSERT INTO libraries(path) VALUES ('C:/TestMusic')")
            conn.commit()
        job = JobState({"running": True, "groups": [], "error": None})
        with patch.object(server, "_duplicate_job", job),              patch.object(server._scan_job, "check_and_mark_request", return_value=(True, 0)),              patch.object(server, "scan_libraries") as scan,              patch.object(server, "_refresh_folder_sync") as refresh:
            response = self.client.post("/api/scan", json={})
            self.assertIn("Duplicate check in progress", response.json()["message"])
            response = self.client.post("/api/scan/refresh-folder", json={"folder": "C:/TestMusic"})
            self.assertIn("Duplicate check in progress", response.json()["message"])
        scan.assert_not_called()
        refresh.assert_not_called()
        self.assertFalse(server._scan_job.is_running())

    def test_started_scan_blocks_duplicate_check_before_its_worker_runs(self):
        """The scan is marked running when accepted, not when its thread starts."""
        from library_jobs import JobState
        server._scan_job.finish()
        job = JobState({"running": False, "groups": [], "error": None})

        async def worker_not_started_yet(music_dirs):
            return None

        with patch.object(server, "_duplicate_job", job),              patch.object(server._scan_job, "check_and_mark_request", return_value=(True, 0)),              patch.object(server, "_scan_libraries_async", side_effect=worker_not_started_yet),              patch.object(server, "find_exact_duplicates") as finder:
            self.assertIn("Scan started", self.client.post("/api/scan", json={}).json()["message"])
            self.assertTrue(server._scan_job.is_running())
            self.assertEqual(self.client.post("/api/library/duplicates/scan").status_code, 409)
        finder.assert_not_called()

    def test_album_files_archive_and_restore_without_permanent_deletion(self):
        album_path = self.root / "music" / "one.flac"
        original = album_path.read_bytes()
        with patch.object(server, "_resolve_library_roots", return_value=[album_path.parent.resolve()]), \
             patch.object(server._scan_job, "is_running", return_value=False), \
             patch.object(server._duplicate_job, "is_running", return_value=False):
            deletion = self.client.delete("/api/albums/1?delete_files=true")
            self.assertEqual(deletion.status_code, 200, deletion.text)
            self.assertFalse(album_path.exists())
            self.assertEqual(deletion.json()["archived_files"], 1)
            with self._connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM albums WHERE id=1").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM albums WHERE id=2").fetchone()[0], 1)
            listing = self.client.get("/api/library/trash")
            self.assertTrue(listing.json()["archives"])
            token = deletion.json()["archive_token"]
            restored = self.client.post(f"/api/library/trash/{token}/restore")
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(album_path.read_bytes(), original)
            self.assertFalse(restored.json()["track_ids_preserved"])
            self.assertFalse(restored.json()["index_restored"])
            with self._connect() as db:
                self.assertIsNone(db.execute("SELECT id FROM tracks WHERE id=1").fetchone())

    def test_album_removal_refuses_playlist_or_history_links_before_staging(self):
        audio = self.root / "music" / "one.flac"
        for association in ("playlist", "history", "both"):
            with self._connect() as db:
                db.execute("DELETE FROM playlist_tracks")
                db.execute("DELETE FROM play_history")
                db.execute("INSERT OR IGNORE INTO playlists(id, name) VALUES (1, 'Keep')")
                if association in ("playlist", "both"):
                    db.execute("INSERT INTO playlist_tracks(id, playlist_id, track_id, position, added_at) "
                               "VALUES (17, 1, 1, 4, 123)")
                if association in ("history", "both"):
                    db.execute("INSERT INTO play_history(id, track_id, played_at, duration_played) "
                               "VALUES (19, 1, 456, 120)")
                db.commit()
                tables = ("albums", "artists", "tracks", "track_artists", "playlists",
                          "playlist_tracks", "play_history")
                before = {table: list(map(tuple, db.execute(f"SELECT * FROM {table} ORDER BY rowid")))
                          for table in tables}
            for delete_files in ("true", "false"):
                with self.subTest(association=association, delete_files=delete_files), \
                     patch.object(server._scan_job, "is_running", return_value=False), \
                     patch.object(server._duplicate_job, "is_running", return_value=False), \
                     patch.object(server, "AlbumFileArchive") as staging:
                    response = self.client.delete(f"/api/albums/1?delete_files={delete_files}")
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn("Track IDs and associations are unchanged", response.json()["detail"])
                    staging.assert_not_called()
                    self.assertEqual(audio.read_bytes(), b"one")
                    self.assertFalse(server._album_mutation_active)
                    with self._connect() as db:
                        after = {table: list(map(tuple, db.execute(f"SELECT * FROM {table} ORDER BY rowid")))
                                 for table in tables}
                        self.assertEqual(after, before)
                        # The refused request released its writer reservation.
                        db.execute("BEGIN IMMEDIATE")
                        db.rollback()

    def test_album_archive_keeps_other_albums_playlist_and_history_links(self):
        with self._connect() as db:
            db.execute("INSERT INTO playlists(id, name) VALUES (1, 'Other album')")
            db.execute("INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (1, 2, 1)")
            db.execute("INSERT INTO play_history(track_id, duration_played) VALUES (2, 99)")
            db.commit()
        audio = self.root / "music" / "one.flac"
        with patch.object(server, "_resolve_library_roots", return_value=[audio.parent]), \
             patch.object(server._scan_job, "is_running", return_value=False), \
             patch.object(server._duplicate_job, "is_running", return_value=False):
            response = self.client.delete("/api/albums/1?delete_files=true")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["track_ids_preserved"])
            token = response.json()["archive_token"]
            self.assertEqual(self.client.post(f"/api/library/trash/{token}/restore").status_code, 200)
        with self._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT track_id, position FROM playlist_tracks").fetchone()), (2, 1))
            self.assertEqual(tuple(db.execute("SELECT track_id, duration_played FROM play_history").fetchone()), (2, 99))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_album_archive_sql_failure_restores_files_and_index(self):
        with self._connect() as db:
            db.execute("CREATE TRIGGER fail_album_delete BEFORE DELETE ON albums "
                       "BEGIN SELECT RAISE(ABORT, 'simulated SQL failure'); END")
            db.commit()
        audio = self.root / "music" / "one.flac"
        with patch.object(server, "_resolve_library_roots", return_value=[audio.parent]), \
             patch.object(server._scan_job, "is_running", return_value=False), \
             patch.object(server._duplicate_job, "is_running", return_value=False):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated SQL failure"):
                self.client.delete("/api/albums/1?delete_files=true")
        self.assertEqual(audio.read_bytes(), b"one")
        with self._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT id, album_id, artist_id FROM tracks WHERE id=1").fetchone()), (1, 1, 1))
            self.assertIsNotNone(db.execute("SELECT * FROM track_artists WHERE track_id=1").fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        manifest = next((server.DATA_DIR / "album-trash").glob("*.json"))
        self.assertEqual(json.loads(manifest.read_text())["state"], "restored")
        self.assertFalse(server._album_mutation_active)

    def test_album_archive_reserves_sql_writer_before_staging(self):
        audio = self.root / "music" / "one.flac"
        real_stage = server.AlbumFileArchive.stage

        def stage_with_competing_writer(archive):
            with self._connect() as db:
                db.execute("PRAGMA busy_timeout=0")
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    db.execute("INSERT INTO play_history(track_id) VALUES (1)")
            real_stage(archive)

        with patch.object(server, "_resolve_library_roots", return_value=[audio.parent]), \
             patch.object(server._scan_job, "is_running", return_value=False), \
             patch.object(server._duplicate_job, "is_running", return_value=False), \
             patch.object(server.AlbumFileArchive, "stage", stage_with_competing_writer):
            response = self.client.delete("/api/albums/1?delete_files=true")
        self.assertEqual(response.status_code, 200, response.text)
        with self._connect() as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_missing_archived_and_original_audio_returns_409_and_remains_in_trash(self):
        audio = self.root / "music" / "one.flac"
        with patch.object(server, "_resolve_library_roots", return_value=[audio.parent]), \
             patch.object(server._scan_job, "is_running", return_value=False), \
             patch.object(server._duplicate_job, "is_running", return_value=False):
            deletion = self.client.delete("/api/albums/1?delete_files=true")
            self.assertEqual(deletion.status_code, 200, deletion.text)
            token = deletion.json()["archive_token"]
            manifest = server.DATA_DIR / "album-trash" / f"{token}.json"
            Path(json.loads(manifest.read_text())["files"][0]["staged"]).unlink()
            response = self.client.post(f"/api/library/trash/{token}/restore")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("missing", response.json()["detail"])
            self.assertNotIn("restored", response.json())
            self.assertEqual(self.client.get("/api/library/trash").json()["archives"],
                             [{"token": token, "state": "recovery_required", "files": 1}])
        self.assertFalse(server._album_mutation_active)

    def test_gzip_reduces_static_payload_only_and_honors_opt_out(self):
        # httpx transparently decodes gzip; the wire header must remain present.
        response = self.client.get("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
        self.assertIn("text/html", response.headers.get("Content-Type", ""))
        self.assertIn("VELVET", response.text.upper())

        script = self.client.get(
            "/static/js/library-views.js", headers={"Accept-Encoding": "gzip"}
        )
        self.assertEqual(script.status_code, 200)
        self.assertEqual(script.headers.get("Content-Encoding"), "gzip")
        self.assertIn("function renderAlbums", script.text)

        plain = self.client.get(
            "/static/js/library-views.js", headers={"Accept-Encoding": "gzip;q=0"}
        )
        self.assertEqual(plain.status_code, 200)
        self.assertNotIn("Content-Encoding", plain.headers)
        self.assertIn("function renderAlbums", plain.text)

    def test_album_delete_keeps_artist_still_referenced_by_other_tracks(self):
        # tracks.artist_id has no ON DELETE action: removing an artist that is
        # still the primary artist of a track on another album violates the FK.
        album_path = self.root / "music" / "one.flac"
        guest = self.root / "music" / "guest.flac"
        guest.write_bytes(b"guest")
        with self._connect() as db:
            db.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
                       "VALUES (3, 2, 1, 'Guest Track', ?)", (str(guest),))
            db.commit()
        with patch.object(server, "_resolve_library_roots", return_value=[album_path.parent.resolve()]), \
             patch.object(server._scan_job, "is_running", return_value=False), \
             patch.object(server._duplicate_job, "is_running", return_value=False):
            deletion = self.client.delete("/api/albums/1?delete_files=true")
        self.assertEqual(deletion.status_code, 200, deletion.text)
        self.assertFalse(album_path.exists())
        self.assertTrue(guest.exists())
        with self._connect() as db:
            self.assertIsNotNone(db.execute("SELECT id FROM artists WHERE id=1").fetchone())
            self.assertIsNotNone(db.execute("SELECT id FROM tracks WHERE id=3").fetchone())

    def test_album_delete_refuses_while_library_scan_is_running(self):
        audio = self.root / "music" / "one.flac"
        with patch.object(server._scan_job, "is_running", return_value=True):
            response = self.client.delete("/api/albums/1?delete_files=true")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(audio.exists())
        with self._connect() as db:
            self.assertIsNotNone(db.execute("SELECT id FROM albums WHERE id=1").fetchone())


class CastDispatcherTests(unittest.TestCase):
    """The per-renderer dispatcher: one SOAP job at a time, whatever the callers do."""

    def setUp(self):
        self.lock = threading.Lock()
        self.active = self.max_active = 0
        self.order = []
        self.started, self.release = threading.Event(), threading.Event()

    def job(self, name, block=False):
        def run():
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            self.order.append(name)
            if block:
                self.started.set()
                self.assertTrue(self.release.wait(5))
            time.sleep(0.01)
            with self.lock:
                self.active -= 1
            return name
        return run

    def test_a_job_cancelled_while_queued_never_runs(self):
        async def scenario():
            dispatcher = server._RendererDispatcher("t")
            outcomes = []
            first = asyncio.ensure_future(asyncio.wrap_future(dispatcher.submit(self.job("a", block=True))))
            queued = asyncio.ensure_future(asyncio.wrap_future(
                dispatcher.submit(self.job("b"), on_done=outcomes.append)))
            last = dispatcher.submit(self.job("c"))
            await asyncio.to_thread(self.started.wait, 5)
            queued.cancel()  # e.g. its HTTP request was cancelled
            await asyncio.sleep(0.05)
            self.release.set()
            self.assertEqual(await asyncio.wrap_future(last), "c")
            self.assertEqual(await first, "a")
            return outcomes

        outcomes = asyncio.run(scenario())
        self.assertEqual(self.order, ["a", "c"])
        self.assertEqual(outcomes, [{"success": False, "cancelled": True}])
        self.assertEqual(self.max_active, 1)

    def test_a_job_cancelled_while_running_keeps_the_renderer_until_it_returns(self):
        async def scenario():
            dispatcher = server._RendererDispatcher("t")
            outcomes = []
            running = asyncio.ensure_future(asyncio.wrap_future(
                dispatcher.submit(self.job("a", block=True), on_done=outcomes.append)))
            nxt = dispatcher.submit(self.job("b"))
            await asyncio.to_thread(self.started.wait, 5)
            running.cancel()
            await asyncio.sleep(0.1)
            self.assertEqual(self.order, ["a"], "the next job must wait for the running one")
            self.release.set()
            self.assertEqual(await asyncio.wrap_future(nxt), "b")
            return outcomes

        outcomes = asyncio.run(scenario())
        self.assertEqual(self.order, ["a", "b"])
        self.assertEqual(outcomes, ["a"])  # its result is still recorded
        self.assertEqual(self.max_active, 1)

    def test_waiting_jobs_hold_no_thread_pool_slot(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1))
            blocker = loop.run_in_executor(None, self.release.wait, 5)  # the only pool worker
            dispatcher = server._RendererDispatcher("t")
            futures = [asyncio.wrap_future(dispatcher.submit(self.job(f"j{i}"))) for i in range(5)]
            done = await asyncio.wait_for(asyncio.gather(*futures), 5)
            self.release.set()
            await blocker
            return done

        self.assertEqual(asyncio.run(scenario()), [f"j{i}" for i in range(5)])
        self.assertEqual(self.max_active, 1)

    def test_a_control_still_runs_when_its_request_is_cancelled(self):
        # Active cast -> Pause and Stop queued -> their HTTP waits are cancelled:
        # both SOAP commands still run, after the cast and never overlapping it.
        calls = []

        def control(udn, action):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            calls.append(action)
            with self.lock:
                self.active -= 1
            return True

        async def scenario():
            dispatcher = server._renderer_dispatcher("renderer-cancel")
            busy = dispatcher.submit(self.job("cast", block=True))
            await asyncio.to_thread(self.started.wait, 5)
            generation = server._cast_generations.get("renderer-cancel", 0)
            for action in ("Pause", "Stop"):
                task = asyncio.ensure_future(server.cast_control("renderer-cancel", action))
                await asyncio.sleep(0.05)
                task.cancel()
            await asyncio.sleep(0.05)
            self.assertEqual(calls, [], "controls wait for the running cast")
            self.release.set()
            await asyncio.wrap_future(busy)
            await asyncio.wrap_future(dispatcher.submit(lambda: None))  # drained
            return generation

        with patch.object(upnp_server, "cast_control", side_effect=control):
            generation = asyncio.run(scenario())
        self.assertEqual(calls, ["Pause", "Stop"])
        self.assertEqual(self.max_active, 1)
        self.assertEqual(server._cast_generations["renderer-cancel"], generation + 1)
        self.assertNotIn("renderer-cancel", server._cast_user_paused)

if __name__ == "__main__":
    unittest.main()
