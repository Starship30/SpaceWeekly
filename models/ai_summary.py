from dataclasses import dataclass
from dataclasses import field


@dataclass
class AISummary:
    summary: str
    keywords: list[str]
    categories: list[str] = field(default_factory=list)
