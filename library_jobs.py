from __future__ import annotations

import threading
import time


class JobState:
    def __init__(self, initial_state: dict):
        self._state = dict(initial_state)
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._state.get("running"))

    def update(self, **kwargs) -> dict:
        with self._lock:
            self._state.update(kwargs)
            return dict(self._state)

    def set(self, key: str, value) -> dict:
        with self._lock:
            self._state[key] = value
            return dict(self._state)

    def increment(self, key: str, amount: int = 1) -> dict:
        with self._lock:
            self._state[key] = self._state.get(key, 0) + amount
            return dict(self._state)

    def mark_running(self, **kwargs) -> dict:
        payload = {"running": True}
        payload.update(kwargs)
        return self.update(**payload)

    def finish(self, **kwargs) -> dict:
        payload = {"running": False, "finished_at": time.time()}
        payload.update(kwargs)
        return self.update(**payload)


class ScanJobState(JobState):
    def __init__(self, music_dir: str):
        super().__init__(
            {
                "running": False,
                "progress": 0,
                "total": 0,
                "current_file": "",
                "tracks_added": 0,
                "tracks_skipped": 0,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "music_dir": music_dir,
            }
        )
        self._last_request_time = 0.0
        self._cooldown_lock = threading.Lock()

    def begin(self, music_dir: str) -> dict:
        return self.mark_running(
            progress=0,
            total=0,
            current_file="Discovering files…",
            tracks_added=0,
            tracks_skipped=0,
            started_at=time.time(),
            finished_at=None,
            error=None,
            music_dir=music_dir,
        )

    def begin_refresh(self, folder_path: str) -> dict:
        return self.mark_running(
            progress=0,
            total=0,
            current_file=f"Refreshing {folder_path}…",
            tracks_added=0,
            tracks_skipped=0,
            started_at=time.time(),
            finished_at=None,
            error=None,
            music_dir=folder_path,
        )

    def check_and_mark_request(self, cooldown_seconds: int, now: float | None = None) -> tuple[bool, int]:
        with self._cooldown_lock:
            current_time = now if now is not None else time.time()
            time_since_last = current_time - self._last_request_time
            if time_since_last < cooldown_seconds:
                remaining = int(cooldown_seconds - time_since_last)
                return False, remaining
            self._last_request_time = current_time
            return True, 0


def create_fingerprint_job_state() -> JobState:
    return JobState(
        {
            "running": False,
            "processed": 0,
            "total": 0,
            "current": "",
            "matched": 0,
            "finished_at": None,
            "error": None,
        }
    )


def create_bio_job_state() -> JobState:
    return JobState(
        {
            "running": False,
            "processed": 0,
            "total": 0,
            "current": "",
            "fetched": 0,
            "not_found": 0,
            "finished_at": None,
            "error": None,
        }
    )
