"""Spaceflight News API articles; reuse the existing Article/export workflow."""
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import requests

from downloader import client
from models.article import Article
from models.news import News
from services.cancellation import CancelCheck, no_cancel
from utils.time import parse_datetime

logger = logging.getLogger(__name__)
ARTICLES_URL = "https://api.spaceflightnewsapi.net/v4/articles/"


class SpaceflightNewsError(RuntimeError):
    """A source failure that does not prevent using other news sources."""


class SpaceflightNewsProvider:
    @staticmethod
    def handles(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (parsed.scheme in {"http", "https"}
                    and parsed.netloc.lower() == "api.spaceflightnewsapi.net"
                    and parsed.path.rstrip("/") == "/v4/articles")
        except ValueError:
            return False

    def fetch(self, limit: int = 20, cancel_check: CancelCheck = no_cancel,
              date_range: tuple[str, str] | None = None) -> list[Article]:
        if limit < 0:
            raise ValueError("News limit must be zero (unlimited) or positive")
        if limit == 0 and date_range is None:
            raise ValueError("不限读取需要选择新闻日期范围")
        params = {"limit": min(limit, 100) if limit else 100, "ordering": "-published_at"}
        if date_range:
            start, end = (date.fromisoformat(value) for value in date_range)
            if start > end:
                raise ValueError("Start date must not be after end date")
            params["published_at_gte"] = datetime.combine(start, datetime.min.time(), timezone.utc).isoformat()
            params["published_at_lt"] = datetime.combine(end+timedelta(days=1), datetime.min.time(), timezone.utc).isoformat()
        url = ARTICLES_URL+"?"+urlencode(params)
        articles, seen_urls, visited = [], set(), set()
        while url:
            cancel_check()
            if not self.handles(url) or urlsplit(url).scheme != "https" or url in visited:
                raise SpaceflightNewsError("Invalid or repeated API pagination URL")
            visited.add(url)
            try:
                response = requests.get(url, headers=client.headers,
                    timeout=(client.CONNECT_TIMEOUT, client.READ_TIMEOUT))
                response.raise_for_status()
                cancel_check()
                data = response.json()
                if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
                        or data.get("next") is not None and not isinstance(data.get("next"), str)):
                    raise ValueError("Invalid articles page")
            except (requests.RequestException, ValueError) as error:
                if articles:
                    logger.warning("[SNAPI] Pagination interrupted at %s: %s; retained %s articles (incomplete)",
                                   url, error, len(articles))
                    break
                raise SpaceflightNewsError(f"API page failed: {error}") from error
            for item in data["results"]:
                cancel_check()
                try:
                    if (not isinstance(item, dict) or any(not isinstance(item.get(key), str)
                            or not item[key].strip() for key in ["title", "url", "news_site", "published_at"])
                            or not isinstance(item.get("summary", ""), str)):
                        raise ValueError("Missing or invalid article fields")
                    parsed = urlsplit(item["url"])
                    published = parse_datetime(item["published_at"])
                    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or published is None
                            or any(char.isspace() for char in item["url"])):
                        raise ValueError("Invalid article URL or publication date")
                    if date_range and not start <= published.astimezone(timezone.utc).date() <= end:
                        continue
                    news = News(source=item["news_site"].strip(), title=item["title"].strip(),
                        published=item["published_at"], summary=item.get("summary", ""), url=item["url"])
                except ValueError as error:
                    logger.warning("[SNAPI] Skipping article %s: %s", item.get("id") if isinstance(item, dict) else "?", error)
                    continue
                if news.url in seen_urls:
                    continue
                seen_urls.add(news.url)
                articles.append(Article(news, news.summary or news.title, "Spaceflight News API"))
                if limit > 0 and len(articles) >= limit:
                    return articles
            logger.info("[SNAPI] Collected %s articles across %s pages", len(articles), len(visited))
            if not data["results"]:
                break
            url = data.get("next") or ""
        return articles
