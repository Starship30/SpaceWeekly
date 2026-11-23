# models/news.py

from dataclasses import dataclass, field
import hashlib
import re


def is_manual_id(value):
    return isinstance(value, str) and re.fullmatch(r'manual:[0-9a-f]{32}', value) is not None


def is_news_url(url):
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not re.fullmatch(r'https?://[^\s/]+(?:/[^\s]*)?', url, re.I):
        return False
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:
        return False


def news_id(url):
    if is_manual_id(url):
        return url
    return 'url-sha256:' + hashlib.sha256(url.encode('utf-8')).hexdigest()

@dataclass
class News:

    source: str

    title: str

    published: str

    summary: str

    url: str

    images: list[dict] = field(default_factory=list)

    manual_id: str = ""

    @property
    def key(self):
        return self.manual_id or self.url
