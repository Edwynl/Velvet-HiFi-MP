import unittest
from unittest.mock import patch

import open_ui_when_ready


class FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class OpenUiWhenReadyTests(unittest.TestCase):
    def test_wait_until_ready_retries_until_success(self):
        attempts = iter([OSError("not ready"), FakeResponse(200)])

        def opener(*_args, **_kwargs):
            result = next(attempts)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(open_ui_when_ready.time, "sleep") as sleep:
            ready = open_ui_when_ready.wait_until_ready(
                "http://127.0.0.1:8765/",
                attempts=2,
                opener=opener,
            )

        self.assertTrue(ready)
        sleep.assert_called_once()

    def test_wait_until_ready_stops_after_timeout(self):
        def unavailable(*_args, **_kwargs):
            raise OSError("not ready")

        with patch.object(open_ui_when_ready.time, "sleep"):
            ready = open_ui_when_ready.wait_until_ready(
                "http://127.0.0.1:8765/",
                attempts=3,
                opener=unavailable,
            )

        self.assertFalse(ready)


if __name__ == "__main__":
    unittest.main()
