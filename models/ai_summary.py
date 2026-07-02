from dataclasses import dataclass


@dataclass
class AISummary:
    summary: str
    keywords: list[str]
    category: str
    importance: str
