import random
import time
from urllib.parse import urljoin, urlsplit
import ipaddress

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


def public_url(value, base=''):
    """Candidate URLs must be ordinary public HTTP links, never credentials or local files."""
    try:
        if not isinstance(value,str) or not value.strip():return ''
        value=urljoin(base,str(value).strip());parsed=urlsplit(value)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or len(value)>4096:
            return ''
        host=parsed.hostname.lower()
        if host=='localhost' or host.endswith(('.localhost','.local')) or any(c.isspace() for c in value):return ''
        try:
            if not ipaddress.ip_address(host).is_global:return ''
        except ValueError:pass
        return value.split('#')[0]
    except ValueError:return ''


def download_to(url, path, token, *, max_bytes=24*1024*1024, progress=None):
    """Bounded streamed assets; callers verify decoded content before committing."""
    for attempt in range(2):
        token.check();current=public_url(url)
        if not current:raise ValueError('图片地址无效或不是公开网页地址')
        try:
            for redirect in range(4):
                token.check()
                with _session().get(current,headers=headers,timeout=(5,8),stream=True,allow_redirects=False) as response:
                    if response.status_code in (301,302,303,307,308):
                        current=public_url(response.headers.get('Location',''),current)
                        if not current or redirect==3:raise ValueError('图片重定向无效或次数过多')
                        continue
                    response.raise_for_status()
                    total=int(response.headers.get('Content-Length','0'))
                    if total>max_bytes:raise ValueError('下载文件超过大小限制')
                    count=0
                    with path.open('wb') as handle:
                        for block in response.iter_content(65536):
                            token.check();count+=len(block)
                            if count>max_bytes:raise ValueError('下载文件超过大小限制')
                            handle.write(block)
                            if progress:progress(count,total or None)
                    token.check()
                    return dict(url=current,bytes=count,content_type=response.headers.get('Content-Type',''))
        except requests.RequestException:
            if attempt or token.wait(.5):token.check();raise


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
