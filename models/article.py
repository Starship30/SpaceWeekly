# models/article.py

from dataclasses import dataclass
from .news import News

@dataclass
class Article:

    news: News

    body: str