import random
import time

import requests

REQUEST_TIMEOUT = 10
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
RETRY_COUNT = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )
}

_SESSION: requests.Session | None = None


def download(url):
    """Download a URL with retry/backoff while preserving the public API."""
    last_error: Exception | None = None

    for attempt in range(RETRY_COUNT + 1):
        if attempt:
            time.sleep(_retry_delay(attempt))

        try:
            response = _session().get(
                url,
                headers=headers,
                timeout=_timeout(),
            )

            if response.status_code in RETRY_STATUS_CODES and attempt < RETRY_COUNT:
                last_error = requests.HTTPError(
                    f"HTTP {response.status_code} for {url}",
                    response=response,
                )
                continue

            response.raise_for_status()
            response.encoding = _detect_encoding(response)

            return response.text
        except requests.RequestException as exc:
            last_error = exc

            if attempt >= RETRY_COUNT:
                break

    raise RuntimeError(f"Download failed after {RETRY_COUNT + 1} attempts: {url}") from last_error


def _session() -> requests.Session:
    global _SESSION

    if _SESSION is None:
        _SESSION = requests.Session()

    return _SESSION


def _timeout() -> tuple[int, int]:
    connect_timeout = CONNECT_TIMEOUT or REQUEST_TIMEOUT
    read_timeout = READ_TIMEOUT or max(REQUEST_TIMEOUT, 30)

    return connect_timeout, read_timeout


def _retry_delay(attempt: int) -> float:
    return min(30.0, 2.0 ** (attempt - 1)) + random.uniform(0.1, 0.7)


def _detect_encoding(response) -> str:
    encoding = response.encoding

    if not encoding or encoding.lower() == "iso-8859-1":
        apparent_encoding = response.apparent_encoding

        if apparent_encoding:
            return apparent_encoding

    return encoding or "utf-8"
