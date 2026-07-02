import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from resources import resource_path

FEEDS_PATH = resource_path("feeds.json")


@dataclass
class FeedSource:
    name: str
    rss: str
    enabled: bool


def load_feeds() -> list[FeedSource]:
    """Load RSS feed sources from feeds.json."""
    if not FEEDS_PATH.exists():
        save_feeds([])
        return []

    data = json.loads(FEEDS_PATH.read_text(encoding="utf-8"))
    return [_feed_from_dict(item) for item in data]


def save_feeds(feeds: list[FeedSource]) -> None:
    """Save RSS feed sources to feeds.json."""
    data = [asdict(feed) for feed in feeds]
    FEEDS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def enabled_feeds() -> list[FeedSource]:
    """Return enabled RSS feed sources."""
    return [feed for feed in load_feeds() if feed.enabled]


def _feed_from_dict(data: dict[str, object]) -> FeedSource:
    return FeedSource(
        name=str(data.get("name", "")),
        rss=str(data.get("rss", "")),
        enabled=bool(data.get("enabled", True)),
    )
