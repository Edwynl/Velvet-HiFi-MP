from __future__ import annotations

import time
import urllib.request
import webbrowser
from collections.abc import Callable

from settings import get_settings


def wait_until_ready(
    url: str,
    *,
    attempts: int = 120,
    delay_seconds: float = 0.5,
    opener: Callable = urllib.request.urlopen,
) -> bool:
    """Wait until the VELVET frontend responds successfully."""
    for _ in range(attempts):
        try:
            with opener(url, timeout=1.5) as response:
                if 200 <= response.status < 400:
                    return True
        except Exception:
            pass
        time.sleep(delay_seconds)
    return False


def main() -> int:
    url = f"http://127.0.0.1:{get_settings().port}/"
    if not wait_until_ready(url):
        return 1
    return 0 if webbrowser.open(url, new=2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
