"""Space.com listing and existing report/SQLite integration regressions."""

import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import timezone
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from database import sqlite
from feeds.spacecom import SpaceComHTTPError, SpaceComParseError, SpaceComWebProvider
from models.article import Article
from services import weekly_report
from services.cancellation import CancellationToken, TaskCancelledError
from services.feed_manager import FeedSource
from services.settings import default_settings
from utils.time import parse_datetime


def card(url="/article/mission-update", title="Mission update",
         published="2026-09-20T06:40:13Z", summary="A mission reached orbit."):
    # Same nesting/classes as the live /news page; ads/category links are siblings.
    return f"""<div class="listingResult small">
      <a class="article-link" href="{escape(url, quote=True)}">
        <article class="search-result search-result-news">
          <div class="article-name">{escape(title)}</div>
          <p class="byline"><span class="by-author">By Test Author</span>
            <time datetime="{escape(published, quote=True)}"></time></p>
          <p class="synopsis">{escape(summary)}</p>
        </article>
      </a>
      <a class="category-link" href="/space-exploration/launches-spacecraft">Launches</a>
    </div>"""


def listing(*cards):
    return '<html><body><div class="listingResults news">' + ''.join(cards) + '</div></body></html>'


def paginated(*cards, next_url="/news/page/2"):
    html = listing(*cards)
    if next_url:
        html = html.replace('<html>', f'<html><head><link rel="next" href="{escape(next_url, quote=True)}"></head>')
    return html


def response(html):
    result = requests.Response()
    result.status_code = 200
    result._content = html.encode("utf-8")
    result.url = SpaceComWebProvider.listing_url
    result.encoding = "utf-8"
    return result


class SpaceComWebProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = SpaceComWebProvider()

    def test_http_200_returns_articles_with_one_listing_request(self):
        with patch("feeds.spacecom.requests.get", return_value=response(listing(card()))) as get:
            with self.assertLogs("feeds.spacecom", level="INFO") as logs:
                articles = self.provider.fetch()
        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertIsInstance(article, Article)
        self.assertEqual(article.news.source, "Space.com")
        self.assertEqual(article.news.title, "Mission update")
        self.assertEqual(article.news.summary, "A mission reached orbit.")
        self.assertEqual(article.body, article.news.summary)
        self.assertEqual(article.parser, "Space.com Web")
        self.assertEqual(article.news.published, "2026-09-20T06:40:13+00:00")
        get.assert_called_once()
        self.assertEqual(get.call_args.args, ("https://www.space.com/news",))
        self.assertIn("HTTP 200", '\n'.join(logs.output))
        self.assertIn("Parsed 1 articles", '\n'.join(logs.output))

    def test_modified_label_is_not_original_publication(self):
        self.assertIsNone(self.provider._parse_date('Last updated October 7, 2026'))
        published = self.provider.parse_listing(listing(card(published='2026-10-04T01:00:00+08:00')))[0].news.published
        self.assertEqual(parse_datetime(published).isoformat(), '2026-10-03T17:00:00+00:00')

    def test_root_level_article_slug_is_not_mistaken_for_navigation(self):
        articles = self.provider.parse_listing(listing(card('/amazon-prime-day-space-deals')))
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].news.url, 'https://www.space.com/amazon-prime-day-space-deals')

    def test_urls_are_canonical_and_deduplicated(self):
        html = listing(
            card("/article/xxx?utm_source=rss&fbclid=123#comments"),
            card("http://space.com/article/xxx/"),
            card("//www.space.com/article/xxx?ref=newsletter"),
            card("/article/yyy?view=full&utm_medium=email"),
        )
        articles = self.provider.parse_listing(html)
        self.assertEqual([a.news.url for a in articles], [
            "https://www.space.com/article/xxx",
            "https://www.space.com/article/yyy?view=full",
        ])

    def test_navigation_categories_authors_images_and_external_links_are_filtered(self):
        invalid = ["/", "/news", "/news/page/2", "/astronomy", "/tag/space",
                   "/tags/space", "/author/mike-wall", "/authors/mike-wall",
                   "/category/astronomy", "/space-exploration/launches-spacecraft",
                   "/assets/photo.jpg", "https://cdn.mos.cms.futurecdn.net/photo.jpg",
                   "https://www.space.com.evil.example/article/xxx", "javascript:alert(1)",
                   "mailto:editor@space.com", "/feeds.xml", "https://[invalid"]
        html = listing(*(card(url) for url in invalid), card("/astronomy/black-holes"), card())
        html = html.replace('<body>', '<body><nav><a href="/astronomy/black-holes">Black holes</a></nav>')
        html = html.replace('</body>', card('/article/outside-listing') + '</body>')
        with self.assertLogs("feeds.spacecom", level="WARNING"):
            articles = self.provider.parse_listing(html)
        self.assertEqual([a.news.url for a in articles], ["https://www.space.com/article/mission-update"])

    def test_bad_articles_do_not_prevent_later_articles_or_consume_limit(self):
        html = listing(card(title=""), card(published="invalid"),
                       card(published=""), card(), card("/article/second"))
        with self.assertLogs("feeds.spacecom", level="WARNING") as logs:
            articles = self.provider.parse_listing(html, limit=1)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].news.title, "Mission update")
        self.assertEqual(len(logs.output), 3)
        self.assertTrue(all("Failed to parse article" in line for line in logs.output))

    def test_unexpected_single_card_error_is_logged_and_skipped(self):
        parse = self.provider.parse_article
        def fail_first(node):
            if node["href"] == "/article/bad":
                raise RuntimeError("unexpected card failure")
            return parse(node)
        with patch.object(self.provider, "parse_article", side_effect=fail_first):
            with self.assertLogs("feeds.spacecom", level="WARNING") as logs:
                articles = self.provider.parse_listing(listing(card("/article/bad"), card()))
        self.assertEqual(len(articles), 1)
        self.assertIn("unexpected card failure", logs.output[0])

    def test_empty_listing_returns_empty_list(self):
        with patch("feeds.spacecom.requests.get", return_value=response(listing('<div class="listingResult sponsored-post"></div>'))):
            with self.assertLogs("feeds.spacecom", level="WARNING") as logs:
                self.assertEqual(self.provider.fetch(), [])
        self.assertIn("No article candidates found", logs.output[0])

    def test_unrecognizable_page_raises_parse_error(self):
        with self.assertLogs("feeds.spacecom", level="ERROR"):
            with self.assertRaisesRegex(SpaceComParseError, "listing container"):
                self.provider.parse_listing("<html><h1>Unexpected response</h1></html>")

    def test_network_failure_is_explicit_and_preserves_cause(self):
        failure = requests.Timeout("connection timed out")
        with patch("feeds.spacecom.requests.get", side_effect=failure):
            with self.assertLogs("feeds.spacecom", level="ERROR"):
                with self.assertRaises(SpaceComHTTPError) as caught:
                    self.provider.fetch()
        self.assertIs(caught.exception.__cause__, failure)

    def test_http_failure_is_not_reported_as_an_empty_page(self):
        failed = response("Service unavailable")
        failed.status_code = 503
        with patch("feeds.spacecom.requests.get", return_value=failed):
            with self.assertLogs("feeds.spacecom", level="ERROR"):
                with self.assertRaisesRegex(SpaceComHTTPError, "503"):
                    self.provider.fetch()

    def test_limit_50_fetches_five_pages_and_does_not_fetch_sixth(self):
        pages = [response(paginated(
            *(card(f"/article/{index}") for index in range(page * 10, (page + 1) * 10)),
            next_url=f"/news/page/{page + 2}",
        )) for page in range(5)]
        with patch("feeds.spacecom.requests.get", side_effect=pages) as get:
            articles = self.provider.fetch(limit=50)
        self.assertEqual(len(articles), 50)
        self.assertEqual(len({a.news.url for a in articles}), 50)
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         [self.provider.listing_url] + [f"https://www.space.com/news/page/{p}" for p in range(2, 6)])

    def test_limit_counts_unique_articles_across_different_page_sizes(self):
        pages = [
            response(paginated(card('/article/a'), card('/article/b'))),
            response(paginated(card('/article/b?utm_source=next'), card('/article/c'),
                               card('/article/d'), next_url='/news/page/3')),
            response(paginated(card('/article/d'), card('/article/e'), card('/article/f'),
                               next_url='/news/page/4')),
        ]
        with patch("feeds.spacecom.requests.get", side_effect=pages) as get:
            articles = self.provider.fetch(limit=5)
        self.assertEqual([a.news.url.rsplit('/', 1)[1] for a in articles], list('abcde'))
        self.assertEqual(get.call_count, 3)

    def test_small_limit_and_default_do_not_request_next_page(self):
        html = paginated(*(card(f'/article/{i}') for i in range(10)))
        for limit, count in [(1, 1), (3, 3), (10, 10), (0, 10)]:
            with self.subTest(limit=limit), patch("feeds.spacecom.requests.get", return_value=response(html)) as get:
                self.assertEqual(len(self.provider.fetch(limit=limit)), count)
                get.assert_called_once()

    def test_last_page_empty_page_and_repeated_page_stop_pagination(self):
        for final in [paginated(card('/article/b'), next_url=''),
                      paginated(next_url='/news/page/3'),
                      paginated(card(), next_url='/news/page/3')]:
            with self.subTest(final=final), patch("feeds.spacecom.requests.get", side_effect=[
                response(paginated(card())), response(final),
            ]) as get:
                with self.assertLogs("feeds.spacecom", level="WARNING") as logs:
                    articles = self.provider.fetch(limit=50)
                self.assertTrue(1 <= len(articles) <= 2)
                self.assertEqual(get.call_count, 2)
                self.assertIn('before requested limit', logs.output[-1])

    def test_next_page_cycle_and_invalid_links_are_not_followed(self):
        with patch("feeds.spacecom.requests.get", side_effect=[
            response(paginated(card())),
            response(paginated(card('/article/second'), next_url='/news/page/2')),
        ]) as get:
            with self.assertLogs("feeds.spacecom", level="WARNING"):
                self.assertEqual(len(self.provider.fetch(limit=50)), 2)
            self.assertEqual(get.call_count, 2)
        for url in ['https://example.com/news/page/2', '/author/test',
                    '/news/page/2?unrelated=yes', 'https://[bad', '/news']:
            with self.subTest(url=url), patch("feeds.spacecom.requests.get", return_value=response(paginated(card(), next_url=url))) as get:
                with self.assertLogs("feeds.spacecom", level="WARNING"):
                    self.assertEqual(len(self.provider.fetch(limit=50)), 1)
                get.assert_called_once()

    def test_later_page_errors_preserve_articles_and_log_partial_result(self):
        for failure in [requests.Timeout('page two offline'), response('<html>Bad page</html>')]:
            with self.subTest(failure=failure), patch("feeds.spacecom.requests.get", side_effect=[response(paginated(card())), failure]):
                with self.assertLogs("feeds.spacecom", level="WARNING") as logs:
                    articles = self.provider.fetch(limit=50)
                self.assertEqual(len(articles), 1)
                self.assertTrue(any('Pagination interrupted' in line for line in logs.output))
                self.assertIn('1 / 50', logs.output[-1])

    def test_cancellation_during_pagination_is_not_swallowed(self):
        token = CancellationToken()
        def request(url, **kwargs):
            if url.endswith('/2'):
                token.cancel()
            return response(paginated(card(), next_url='/news/page/2'))
        with patch("feeds.spacecom.requests.get", side_effect=request) as get:
            with self.assertRaises(TaskCancelledError):
                self.provider.fetch(limit=50, cancel_check=token.check)
        self.assertEqual(get.call_count, 2)

    def test_dates_use_existing_timezone_and_filter_contract(self):
        for value in ["2026-09-20T14:40:13+08:00", "2026-09-20T06:40:13",
                      "Sun, 20 Sep 2026 06:40:13 GMT", "20 September 26",
                      "20 September 2026", "Published 20 September 26"]:
            with self.subTest(value=value):
                article = self.provider.parse_listing(listing(card(published=value)))[0]
                parsed = parse_datetime(article.news.published)
                self.assertEqual(parsed.tzinfo, timezone.utc)
                self.assertEqual(parsed.date().isoformat(), "2026-09-20")
                self.assertEqual(weekly_report._filter_news([article.news], ("2026-09-20", "2026-09-20")), [article.news])
                self.assertEqual(weekly_report._filter_news([article.news], ("2026-09-21", "2026-09-21")), [])

    def test_visible_date_fallback_and_optional_summary(self):
        html = listing(card(summary="", published="bad"))
        html = html.replace('datetime="bad"></time>', 'datetime="bad">20 September 26</time>')
        article = self.provider.parse_listing(html)[0]
        self.assertEqual(article.news.summary, "")
        self.assertEqual(article.body, article.news.title)

    def test_saved_spacecom_urls_route_to_web_and_other_hosts_do_not(self):
        for url in ["https://www.space.com/news", "https://www.space.com/feeds.xml",
                    "https://www.space.com/feeds/all", "https://space.com/"]:
            self.assertTrue(self.provider.handles(url))
        self.assertFalse(self.provider.handles("https://science.nasa.gov/feed/"))
        self.assertFalse(self.provider.handles("https://www.space.com.evil.example/feed"))


class SpaceComPipelineTests(unittest.TestCase):
    def workflow(self, stack, database_path, rss_limit=0, start="2026-09-20", end="2026-09-20"):
        settings = replace(default_settings(), news_event_date_check_enabled=False, deepseek_api_key="", date_mode="custom",
                           start_date=start, end_date=end, rss_limit=rss_limit,
                           launch_range="disabled", export_markdown=False, export_word=False)
        stack.enter_context(patch.object(sqlite, "DATABASE_PATH", database_path))
        stack.enter_context(patch.object(weekly_report, "setup_logging"))
        stack.enter_context(patch.object(weekly_report, "load_settings", return_value=settings))
        stack.enter_context(patch.object(weekly_report, "apply_settings"))
        stack.enter_context(patch.object(weekly_report, "_export_reports"))

    def test_web_article_enters_report_and_existing_sqlite_url_dedup(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            self.workflow(stack, Path(directory) / "news.db")
            get = stack.enter_context(patch("feeds.spacecom.requests.get", return_value=response(listing(card(), card('/article/mission-update?utm_source=x')))))
            rss = stack.enter_context(patch("feedparser.parse"))
            download = stack.enter_context(patch.object(weekly_report, "download"))
            feeds = [FeedSource("Space.com", "https://www.space.com/feeds.xml", True)]
            first = weekly_report.generate_weekly(feeds)
            second = weekly_report.generate_weekly(feeds)
            self.assertEqual(len(first), 1)
            self.assertEqual(first, second)
            self.assertEqual(sqlite.get_articles(), first)
            self.assertFalse(sqlite.save_article(first[0]))
            self.assertEqual(get.call_count, 2)
            rss.assert_not_called()
            download.assert_not_called()

    def test_configured_limit_50_reaches_pipeline_and_sqlite(self):
        pages = [response(paginated(
            *(card(f'/article/{i}') for i in range(p * 10, (p + 1) * 10)),
            next_url=f'/news/page/{p + 2}',
        )) for p in range(5)]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            self.workflow(stack, Path(directory) / 'news.db', rss_limit=50)
            get = stack.enter_context(patch('feeds.spacecom.requests.get', side_effect=pages))
            download = stack.enter_context(patch.object(weekly_report, 'download'))
            articles = weekly_report.generate_weekly([FeedSource('Space.com', 'https://www.space.com/feeds.xml', True)])
            self.assertEqual(len(articles), 50)
            self.assertEqual(len(sqlite.get_articles()), 50)
            self.assertEqual(sum(sqlite.save_article(a) for a in articles), 0)
            self.assertEqual(get.call_count, 5)
            download.assert_not_called()

    def test_provider_error_does_not_pollute_other_rss_source(self):
        for failure in [requests.Timeout("offline"), None]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                self.workflow(stack, Path(directory) / "news.db")
                stack.enter_context(patch("feeds.spacecom.requests.get", side_effect=failure,
                                          return_value=response('<html>Wrong page</html>')))
                item = SimpleNamespace(link="https://science.nasa.gov/example", title="NASA news",
                                       published="Sun, 20 Sep 2026 08:00:00 GMT", summary="NASA summary")
                rss = stack.enter_context(patch("feedparser.parse", return_value=SimpleNamespace(entries=[item])))
                download = stack.enter_context(patch.object(weekly_report, "download", return_value="<html><article><p>NASA article body</p></article></html>"))
                feeds = [FeedSource("Space.com", "https://www.space.com/news", True),
                         FeedSource("NASA Science", "https://science.nasa.gov/feed/", True)]
                with self.assertLogs(level="WARNING"):
                    articles = weekly_report.generate_weekly(feeds)
                self.assertEqual(len(articles), 1)
                self.assertEqual(articles[0].news.source, "NASA Science")
                self.assertEqual(sqlite.get_articles(), articles)
                rss.assert_called_once_with("https://science.nasa.gov/feed/")
                download.assert_called_once_with(item.link)

    def test_limit_date_range_and_unselected_provider(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            self.workflow(stack, Path(directory) / "news.db", rss_limit=1)
            get = stack.enter_context(patch("feeds.spacecom.requests.get", return_value=response(listing(card(), card('/article/second')))))
            feeds = [FeedSource("Space.com", "https://www.space.com/news", True)]
            self.assertEqual(len(weekly_report.generate_weekly(feeds)), 1)
            self.assertEqual(weekly_report.generate_weekly([]), [])
            get.assert_called_once()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            self.workflow(stack, Path(directory) / "news.db", start="2026-09-21", end="2026-09-21")
            stack.enter_context(patch("feeds.spacecom.requests.get", return_value=response(listing(card()))))
            self.assertEqual(weekly_report.generate_weekly(feeds), [])
            self.assertEqual(sqlite.get_articles(), [])


if __name__ == "__main__":
    unittest.main()
