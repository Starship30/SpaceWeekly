# models/news.py

from dataclasses import dataclass, field
import hashlib


def news_id(url):
    return 'url-sha256:' + hashlib.sha256(url.encode('utf-8')).hexdigest()

@dataclass
class News:

    source: str

    title: str

    published: str

    summary: str

    url: str

    images: list[dict] = field(default_factory=list)
