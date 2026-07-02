from dataclasses import dataclass


@dataclass
class NewsAnalysis:
    summary: str
    translation: str
    keywords: list[str]
    categories: list[str]
    importance: str
    score: int
    keep: bool
    reason: str
