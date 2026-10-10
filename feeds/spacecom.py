"""Space.com news cards, isolated from the RSS parsers."""

import logging
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from downloader import client
from models.article import Article
from models.news import News
from services.cancellation import CancelCheck, no_cancel
from utils.time import parse_datetime

logger = logging.getLogger(__name__)


class SpaceComError(RuntimeError):
    """A Space.com failure that the report can isolate from other sources."""


class SpaceComHTTPError(SpaceComError):
    """The news page could not be downloaded."""


class SpaceComParseError(SpaceComError):
    """The response is not a recognizable news listing."""


class SpaceComWebProvider:
    listing_url = "https://www.space.com/news"

    @staticmethod
    def handles(url: str) -> bool:
        # Also routes saved feeds.xml / feeds/all configurations to the web page.
        try:
            return urlsplit(url).hostname in {"space.com", "www.space.com"}
        except ValueError:
            return False

    def fetch_listing(self, url: str | None = None) -> str:
        url = url or self.listing_url
        logger.info("[Space.com] Fetching news page: %s", url)
        try:
            response = requests.get(
                url,
                headers=client.headers,
                timeout=(client.CONNECT_TIMEOUT, client.READ_TIMEOUT),
            )
            logger.info("[Space.com] HTTP %s", response.status_code)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException as exc:
            logger.error("[Space.com] HTTP request failed: %s", exc)
            raise SpaceComHTTPError(f"HTTP request failed: {exc}") from exc

    def fetch(
        self, limit: int = 0, cancel_check: CancelCheck = no_cancel,
        date_range: tuple[str, str] | None = None,
    ) -> list[Article]:
        """Follow pages within a date range or to a limit; bare 0 reads one page."""
        articles: list[Article] = []
        seen_urls: set[str] = set()
        visited_pages: set[str] = set()
        url = self.listing_url
        while url and url not in visited_pages:
            cancel_check()
            visited_pages.add(url)
            try:
                html = self.fetch_listing(url)
                cancel_check()
                page_articles = self.parse_listing(html)
            except SpaceComError as exc:
                if not articles:
                    raise
                logger.warning(
                    "[Space.com] Pagination interrupted at %s: %s; retained %s articles",
                    url, exc, len(articles),
                )
                break
            previous_count = len(seen_urls)
            for article in page_articles:
                cancel_check()
                if article.news.url in seen_urls:
                    continue
                seen_urls.add(article.news.url)
                if date_range:
                    published = parse_datetime(article.news.published)
                    if published is None or not date_range[0] <= published.date().isoformat() <= date_range[1]:
                        continue
                articles.append(article)
                if limit > 0 and len(articles) >= limit:
                    break
            logger.info(
                "[Space.com] Collected %s articles (requested: %s, pages: %s)",
                len(articles), limit or "current page", len(visited_pages),
            )
            if (limit <= 0 and not date_range) or (limit > 0 and len(articles) >= limit):
                break
            if len(seen_urls) == previous_count:
                logger.warning("[Space.com] No new articles on %s; stopping pagination", url)
                break
            # ponytail: listings are newest-first; scan all pages if the site changes ordering.
            if date_range and page_articles and all(
                parse_datetime(article.news.published).date().isoformat() < date_range[0]
                for article in page_articles
            ):
                break
            url = self._next_listing(html, url)
        if limit > 0 and len(articles) < limit:
            logger.warning(
                "[Space.com] Listing stopped before requested limit: %s / %s articles",
                len(articles), limit,
            )
        return articles

    def _next_listing(self, html: str, current_url: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one('link[rel~="next"][href], a[rel~="next"][href]')
        if link is None:
            return ""
        url = self._normalize_url(str(link["href"]), base_url=current_url)
        parsed = urlsplit(url)
        if not url or parsed.query or not re.fullmatch(r"/news/page/[1-9]\d*", parsed.path):
            logger.warning("[Space.com] Ignoring invalid next listing URL: %s", link["href"])
            return ""
        return url

    def parse_listing(self, html: str, limit: int = 0) -> list[Article]:
        try:
            soup = BeautifulSoup(html, "html.parser")
            listing = soup.select_one(".listingResults")
            if listing is None:
                raise ValueError("News listing container is missing")
            candidates = listing.select(".listingResult a.article-link[href]")
            excluded_urls = {
                self._normalize_url(str(link["href"]))
                for link in soup.select("nav a[href], a.category-link[href]")
            }
        except Exception as exc:
            logger.error("[Space.com] Page parsing failed: %s", exc)
            raise SpaceComParseError(f"Page parsing failed: {exc}") from exc

        logger.info("[Space.com] Found %s article candidates", len(candidates))
        if not candidates:
            logger.warning("[Space.com] No article candidates found")

        articles: list[Article] = []
        seen: set[str] = set()
        for card in candidates:
            try:
                url = self._normalize_url(str(card["href"]))
                if url in seen or url in excluded_urls:
                    continue
                article = self.parse_article(card)
            except Exception as exc:
                logger.warning(
                    "[Space.com] Failed to parse article: %s: %s",
                    card.get("href", ""), exc,
                )
                continue
            seen.add(url)
            articles.append(article)
            if limit > 0 and len(articles) >= limit:
                break

        logger.info("[Space.com] Parsed %s articles", len(articles))
        return articles

    def parse_article(self, card: Tag) -> Article:
        """Build an Article from a listing card; no detail-page requests."""
        url = self._normalize_url(str(card["href"]))
        path = urlsplit(url).path.lower()
        parts = path.strip("/").split("/")
        if (
            not url
            or parts[0] in {"news", "tag", "tags", "author", "authors",
                            "category", "categories", "search", "about-us", "feeds"}
            or (len(parts) == 1 and not path.endswith(".html") and "-" not in parts[0])
            or path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif",
                              ".svg", ".xml", ".js", ".pdf"))
        ):
            raise ValueError("Not a Space.com article URL")

        title_node = card.select_one(".article-name")
        title = title_node.get_text(" ", strip=True) if title_node else ""
        if not title or card.select_one("article") is None:
            raise ValueError("Missing article title or article markup")

        time_node = card.select_one("time")
        published = None
        if time_node is not None:
            published = self._parse_date(str(time_node.get("datetime", "")))
            if published is None:
                published = self._parse_date(time_node.get_text(" ", strip=True))
        if published is None:
            # Missing dates are skipped rather than guessed or fetched individually.
            raise ValueError("Missing or invalid publication date")

        summary_node = card.select_one(".synopsis")
        summary = summary_node.get_text(" ", strip=True) if summary_node else ""
        news = News(
            source="Space.com",
            title=title,
            # News stores RFC date strings; this also works with _filter_news.
            published=format_datetime(published.astimezone(timezone.utc)),
            summary=summary,
            url=url,
        )
        return Article(news=news, body=summary or title, parser="Space.com Web")

    @staticmethod
    def _normalize_url(value: str, base_url: str | None = None) -> str:
        try:
            parsed = urlsplit(urljoin(base_url or SpaceComWebProvider.listing_url, value.strip()))
        except ValueError:
            return ""
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() not in {"space.com", "www.space.com"}
        ):
            return ""
        query = urlencode([
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "dclid", "msclkid",
                                    "mc_cid", "mc_eid", "ref", "source"}
        ])
        return urlunsplit(("https", "www.space.com", parsed.path.rstrip("/"), query, ""))

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
        text = value.strip().removeprefix("Published ").removeprefix("Last updated ")
        for date_format in ("%d %B %y", "%d %B %Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(text, date_format).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
