from typing import Protocol

from models.ai_summary import AISummary
from models.article import Article


class AIProvider(Protocol):
    """Common interface for AI summary providers."""

    def summarize(self, article: Article) -> AISummary:
        """Summarize an article."""
