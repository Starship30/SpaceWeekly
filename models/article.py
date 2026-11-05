# models/article.py

from dataclasses import dataclass,field
from models.news import News

@dataclass
class Article:

    news: News

    body: str

    parser: str

    launch_data: dict | None = None
    evidence: list[dict] = field(default_factory=list)
