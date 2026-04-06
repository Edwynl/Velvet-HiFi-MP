import unittest

from library_jobs import ScanJobState, create_bio_job_state, create_fingerprint_job_state


class LibraryJobsTests(unittest.TestCase):
    def test_scan_job_cooldown_blocks_rapid_requests(self):
        job = ScanJobState("C:/Music")

        allowed_first, remaining_first = job.check_and_mark_request(10, now=100.0)
        allowed_second, remaining_second = job.check_and_mark_request(10, now=105.4)
        allowed_third, remaining_third = job.check_and_mark_request(10, now=111.0)

        self.assertTrue(allowed_first)
        self.assertEqual(remaining_first, 0)
        self.assertFalse(allowed_second)
        self.assertEqual(remaining_second, 4)
        self.assertTrue(allowed_third)
        self.assertEqual(remaining_third, 0)

    def test_scan_job_tracks_progress_and_finish(self):
        job = ScanJobState("C:/Music")

        job.begin("C:/Music/A, C:/Music/B")
        job.update(total=12, progress=3, current_file="track.flac")
        job.increment("tracks_added")
        job.increment("tracks_skipped", 2)
        state = job.finish()

        self.assertFalse(state["running"])
        self.assertEqual(state["music_dir"], "C:/Music/A, C:/Music/B")
        self.assertEqual(state["total"], 12)
        self.assertEqual(state["progress"], 3)
        self.assertEqual(state["current_file"], "track.flac")
        self.assertEqual(state["tracks_added"], 1)
        self.assertEqual(state["tracks_skipped"], 2)
        self.assertIsNotNone(state["finished_at"])

    def test_scan_job_begin_refresh_sets_refresh_status(self):
        job = ScanJobState("C:/Music")

        state = job.begin_refresh("C:/Music/Classical")

        self.assertTrue(state["running"])
        self.assertEqual(state["music_dir"], "C:/Music/Classical")
        self.assertEqual(state["current_file"], "Refreshing C:/Music/Classical…")

    def test_fingerprint_job_state_updates_and_finishes(self):
        job = create_fingerprint_job_state()

        job.mark_running(processed=0, total=10, current="", matched=0, error=None, finished_at=None)
        job.update(processed=4, current="demo.flac", matched=2)
        state = job.finish()

        self.assertFalse(state["running"])
        self.assertEqual(state["processed"], 4)
        self.assertEqual(state["current"], "demo.flac")
        self.assertEqual(state["matched"], 2)
        self.assertIsNotNone(state["finished_at"])

    def test_bio_job_state_updates_fields(self):
        job = create_bio_job_state()

        job.mark_running(processed=0, total=20, current="", fetched=0, not_found=0, error=None, finished_at=None)
        job.update(processed=7, current="Artist Name", fetched=3, not_found=1)
        state = job.snapshot()

        self.assertTrue(state["running"])
        self.assertEqual(state["processed"], 7)
        self.assertEqual(state["current"], "Artist Name")
        self.assertEqual(state["fetched"], 3)
        self.assertEqual(state["not_found"], 1)


if __name__ == "__main__":
    unittest.main()
