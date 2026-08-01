from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache

from models.ai_summary import AISummary
from resources import bundled_resource_path

CATEGORY_CONFIG_PATH = bundled_resource_path("categories.json")


@lru_cache(maxsize=1)
def category_config() -> dict[str, object]:
    """Load and validate the single canonical category configuration."""

    data = json.loads(CATEGORY_CONFIG_PATH.read_text(encoding="utf-8"))
    categories = data.get("categories")
    maximum = data.get("max_per_article")

    if not isinstance(categories, list) or not categories:
        raise ValueError("categories.json must contain a non-empty categories list")

    if not isinstance(maximum, int) or maximum < 1:
        raise ValueError("categories.json max_per_article must be a positive integer")

    required = {"id", "zh_CN", "en_US"}

    for item in categories:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("Each category must define id, zh_CN, and en_US")

        if not all(str(item[key]).strip() for key in required):
            raise ValueError("Category identifiers and labels cannot be empty")

    return data


def category_limit() -> int:
    return int(category_config()["max_per_article"])


def category_labels(language: str) -> list[str]:
    """Return the allowed display labels in configured priority order."""

    selected_language = "en_US" if language == "en_US" else "zh_CN"
    return [
        str(item[selected_language])
        for item in category_config()["categories"]
    ]


def normalize_categories(values, language: str) -> list[str]:
    """Map IDs or either language label to localized canonical labels."""

    if not isinstance(values, list):
        return []

    selected_language = "en_US" if language == "en_US" else "zh_CN"
    lookup: dict[str, str] = {}

    for item in category_config()["categories"]:
        localized = str(item[selected_language])

        for key in ("id", "zh_CN", "en_US"):
            lookup[str(item[key]).strip().casefold()] = localized

    normalized: list[str] = []

    for value in values:
        label = lookup.get(str(value).strip().casefold())

        if label and label not in normalized:
            normalized.append(label)

        if len(normalized) >= category_limit():
            break

    return normalized


def category_counts(
    summaries: dict[str, AISummary] | list[AISummary],
) -> dict[str, int]:
    """Return a stable statistics interface for future UI/report features."""

    values = summaries.values() if isinstance(summaries, dict) else summaries
    counts = Counter(
        category
        for summary in values
        for category in getattr(summary, "categories", [])
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
