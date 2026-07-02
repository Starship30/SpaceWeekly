# models/article.py

from dataclasses import dataclass
from models.news import News

@dataclass
class Article:

    news: News

    body: str

    parser: str