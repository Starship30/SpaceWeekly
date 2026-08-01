from dataclasses import dataclass


@dataclass
class NewsAssessment:
    """Minimal AI result used by the screening stage."""

    importance: str
    reason: str
