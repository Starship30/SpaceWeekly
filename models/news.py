# models/news.py

from dataclasses import dataclass

@dataclass
class News:

    source: str

    title: str

    published: str

    summary: str

    url: str