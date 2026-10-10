"""API pagination, per-source limits and deduplication before AI processing."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from PySide6.QtWidgets import QApplication

from exporters import premiere
from feeds.spacecom import SpaceComWebProvider
from feeds.spaceflightnews import ARTICLES_URL, SpaceflightNewsError, SpaceflightNewsProvider
from models.article import Article
from models.news import News
from services import settings as settings_module, weekly_report as report
from services.cancellation import CancellationToken, TaskCancelledError
from services.feed_manager import FeedSource
from services.settings import default_settings, load_settings, save_settings
from ui.feed_dialog import FeedDialog
from ui.widgets.dashboard_panel import DashboardPanel


def item(index=1, **changes):
    return {"id": index, "title": f"虚构新闻 {index}", "summary": "完整中文摘要", "news_site": "示例网站",
            "published_at": "2026-10-01T12:00:00Z", "url": f"https://example.com/news/{index}", **changes}


def response(items, next_url=None):
    result = Mock()
    result.json.return_value = {"count": len(items), "next": next_url, "results": items}
    return result


def article(url, title="Falcon 9 launch: News", source="First", published="2026-10-01T12:00:00Z"):
    return Article(News(source, title, published, "摘要", url), "原始正文", "Test")


class NewsSourceTests(unittest.TestCase):
    def test_api_routing_and_dates_filter_before_limit(self):
        provider = SpaceflightNewsProvider()
        self.assertTrue(provider.handles(ARTICLES_URL))
        self.assertTrue(provider.handles(ARTICLES_URL.rstrip("/")))
        for url in ["https://api.spaceflightnewsapi.net.evil.example/v4/articles/",
                    "https://api.spaceflightnewsapi.net/v4/blogs/", "https://example.com/rss"]:
            self.assertFalse(provider.handles(url))
        rows = [dict(item(0), published_at="2026-10-03T00:00:00Z"), item(), item(2)]
        with patch("feeds.spaceflightnews.requests.get", return_value=response(rows)) as get:
            articles = provider.fetch(1, date_range=("2026-10-01", "2026-10-02"))
        self.assertEqual([a.news.url for a in articles], [item()["url"]])
        self.assertEqual(articles[0].news.source, "示例网站")
        self.assertEqual(articles[0].body, "完整中文摘要")
        self.assertEqual(articles[0].news.published, item()["published_at"])
        params = parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual(params["published_at_gte"], ["2026-10-01T00:00:00+00:00"])
        self.assertEqual(params["published_at_lt"], ["2026-10-03T00:00:00+00:00"])
        self.assertEqual(params["ordering"], ["-published_at"])

    def test_api_unlimited_and_custom_above_500_follow_pages(self):
        pages = [response([item(i) for i in range(start, start+100)],
                    ARTICLES_URL+f"?offset={start+100}") for start in range(0, 600, 100)]
        pages[-1].json.return_value["next"] = None
        for limit, expected in [(0, 600), (550, 550)]:
            with patch("feeds.spaceflightnews.requests.get", side_effect=pages) as get:
                articles = SpaceflightNewsProvider().fetch(limit, date_range=("2026-10-01", "2026-10-01"))
            self.assertEqual(len(articles), expected)
            self.assertEqual(get.call_count, 6)
        with patch("feeds.spaceflightnews.requests.get") as get:
            for kwargs in [dict(limit=0), dict(limit=-1), dict(date_range=("2026-10-02", "2026-10-01"))]:
                with self.assertRaises(ValueError): SpaceflightNewsProvider().fetch(**kwargs)
            get.assert_not_called()

    def test_later_page_failure_preserves_fetched_articles_and_reports_partial_result(self):
        with patch("feeds.spaceflightnews.requests.get", side_effect=[response([item()], ARTICLES_URL+"?offset=100"),
                    requests.HTTPError("HTTP 429")]) as get, self.assertLogs("feeds.spaceflightnews", level="WARNING") as logs:
            articles = SpaceflightNewsProvider().fetch(0, date_range=("2026-10-01", "2026-10-01"))
        self.assertEqual(len(articles), 1)
        self.assertEqual(get.call_count, 2)
        self.assertIn("incomplete", " ".join(logs.output))

    def test_api_end_day_invalid_rows_and_exact_duplicate_urls(self):
        rows = [dict(item(1), published_at="2026-10-01T23:59:59Z"), item(1),
                dict(item(2), published_at="2026-10-02T00:00:00Z"),
                dict(item(3), url="file:///secret.txt"), dict(item(4), title=None),
                dict(item(5), url="https://example.com/news/invalid url"), "invalid"]
        with patch("feeds.spaceflightnews.requests.get", return_value=response(rows)), self.assertLogs("feeds.spaceflightnews", level="WARNING"):
            articles = SpaceflightNewsProvider().fetch(0, date_range=("2026-10-01", "2026-10-01"))
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].news.published, "2026-10-01T23:59:59Z")

    def test_api_failure_bad_next_and_cancellation_are_explicit(self):
        for failure in [requests.Timeout("offline"), ValueError("not JSON")]:
            result = response([])
            result.json.side_effect = failure
            with patch("feeds.spaceflightnews.requests.get", return_value=result), self.assertRaises(SpaceflightNewsError):
                SpaceflightNewsProvider().fetch(1)
        for next_url, expected_calls in [("https://evil.example/v4/articles/", 1),
                                         (ARTICLES_URL+"?limit=100&ordering=-published_at", 2)]:
            with patch("feeds.spaceflightnews.requests.get", return_value=response([item()], next_url)) as get, self.assertRaises(SpaceflightNewsError):
                SpaceflightNewsProvider().fetch(0, date_range=("2026-10-01", "2026-10-01"))
            self.assertEqual(get.call_count, expected_calls)
        token = CancellationToken()
        def first_page(*args, **kwargs):
            token.cancel()
            return response([item()], ARTICLES_URL+"?offset=100")
        with patch("feeds.spaceflightnews.requests.get", side_effect=first_page) as get, self.assertRaises(TaskCancelledError):
            SpaceflightNewsProvider().fetch(0, token.check, ("2026-10-01", "2026-10-01"))
        self.assertEqual(get.call_count, 1)

    def test_dedup_url_tracking_title_day_identity_and_distinct_stories(self):
        first = article("https://www.example.com/news/1?utm_source=RSS#top")
        same_url = article("http://example.com/news/1/", title="Different title", source="API")
        same_title = article("https://another.example/news/8", title="FALCON ９ LAUNCH — NEWS", source="Second")
        alias = article(same_title.news.url, title="Changed title", source="Third")
        other_number = article("https://example.com/news/2", title="Falcon 10 launch: News", source="Second")
        other_day = article("https://example.com/news/3", source="Second", published="2026-10-02T12:00:00Z")
        same_source = article("https://example.com/news/4")
        different_query = [article("https://example.com/page?id=1", title="Query 1"), article("https://example.com/page?id=2", title="Query 2")]
        rows = [first, same_url, same_title, alias, other_number, other_day, same_source, *different_query]
        original_urls = [a.news.url for a in rows]
        unique = report._deduplicate_articles(rows, CancellationToken())
        self.assertEqual(unique, [first, other_number, other_day, same_source, *different_query])
        self.assertIs(unique[0], first)
        self.assertEqual([a.news.url for a in rows], original_urls)
        distinct = [article("https://example.com/decimal", title="Payload 7.5 tonnes", source="First"),
                    article("https://another.example/integer", title="Payload 75 tonnes", source="Second"),
                    article("https://example.com/words", title="NASA finds craft", source="First"),
                    article("https://another.example/compound", title="NASA findscraft", source="Second")]
        self.assertEqual(report._deduplicate_articles(distinct, CancellationToken()), distinct)
        unknown = [article("https://example.com/a", published="bad"), article("https://other.example/b", source="Second", published="bad")]
        self.assertEqual(report._deduplicate_articles(unknown, CancellationToken()), unknown)
        token = CancellationToken(); token.cancel()
        with self.assertRaises(TaskCancelledError): report._deduplicate_articles(rows, token)

    def test_api_merges_before_ai_and_existing_premiere_export(self):
        rss_article = article("https://example.com/news/1", title="虚构新闻 1", source="RSS")
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            settings = replace(default_settings(), deepseek_api_key="", launch_range="disabled", date_mode="custom",
                start_date="2026-10-01", end_date="2026-10-01", export_sqlite=False, export_word=False,
                export_markdown=False, export_premiere=True, rss_limit=0, output_dir=directory)
            stack.enter_context(patch.object(report, "load_settings", return_value=settings))
            stack.enter_context(patch.object(report, "setup_logging"))
            stack.enter_context(patch.object(report, "apply_settings"))
            stack.enter_context(patch.object(premiere, "OUTPUT_DIR", Path(directory)))
            fetch_rss = stack.enter_context(patch.object(report, "get_news", return_value=([rss_article.news], [])))
            stack.enter_context(patch.object(report, "_parse_articles", side_effect=lambda *_: [rss_article]))
            stack.enter_context(patch("feeds.spaceflightnews.requests.get", return_value=response([item(), item(2)])))
            screen = stack.enter_context(patch.object(report, "_screen_articles", wraps=report._screen_articles))
            download = stack.enter_context(patch.object(report, "download"))
            result = report.generate_weekly([FeedSource("RSS", "https://example.com/rss", True), FeedSource("API", ARTICLES_URL, True)], export_sqlite=False)
            self.assertEqual(len(result), 2)
            self.assertEqual(len(screen.call_args.args[0]), 2)
            self.assertEqual([feed.name for feed in fetch_rss.call_args.args[0]], ["RSS"])
            snapshot = json.loads(next(Path(directory).glob("*.premiere.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["items"]), 2)
            self.assertEqual(snapshot["items"][1]["title"], "虚构新闻 2")
            download.assert_not_called()
            settings.news_dedup_enabled = False
            settings.export_premiere = False
            self.assertEqual(len(report.generate_weekly([FeedSource("API", ARTICLES_URL, True)], export_sqlite=False)), 3)

    def test_api_failure_isolated_and_unselected_api_not_called(self):
        retained = article("https://example.com/kept")
        with ExitStack() as stack:
            settings = replace(default_settings(), deepseek_api_key="", launch_range="disabled", export_word=False, export_markdown=False)
            stack.enter_context(patch.object(report, "load_settings", return_value=settings))
            stack.enter_context(patch.object(report, "setup_logging"))
            stack.enter_context(patch.object(report, "apply_settings"))
            stack.enter_context(patch.object(report, "get_news", return_value=([retained.news], [])))
            stack.enter_context(patch.object(report, "_parse_articles", return_value=[retained]))
            stack.enter_context(patch.object(report, "_export_reports"))
            fetch = stack.enter_context(patch.object(SpaceflightNewsProvider, "fetch", side_effect=SpaceflightNewsError("HTTP 429")))
            with self.assertLogs("services.weekly_report", level="WARNING"):
                self.assertEqual(report.generate_weekly([FeedSource("API", ARTICLES_URL, True)], export_sqlite=False), [retained])
            report.generate_weekly([], export_sqlite=False)
            self.assertEqual(fetch.call_count, 1)

    def test_unlimited_web_date_pagination_and_rss_date_filter(self):
        provider = SpaceComWebProvider()
        pages = [[article("https://www.space.com/new", published="2026-10-02T00:00:00Z")],
                 [article("https://www.space.com/yes")],
                 [article("https://www.space.com/old", published="2026-09-30T23:59:59Z")]]
        with patch.object(provider, "fetch_listing", return_value="html") as get, patch.object(provider, "parse_listing", side_effect=pages), patch.object(provider, "_next_listing", side_effect=["https://www.space.com/news/page/2", "https://www.space.com/news/page/3"]):
            result = provider.fetch(0, date_range=("2026-10-01", "2026-10-01"))
        self.assertEqual([a.news.url for a in result], ["https://www.space.com/yes"])
        self.assertEqual(get.call_count, 3)
        rows = [SimpleNamespace(link=f"https://example.com/{i}", published="2026-10-01T12:00:00Z") for i in range(550)]
        rows.append(SimpleNamespace(link="https://example.com/old", published="2026-09-30T12:00:00Z"))
        with patch("feedparser.parse", return_value=SimpleNamespace(entries=rows)):
            result = report._fetch_news([FeedSource("RSS", "https://example.com/rss", True)], 0, CancellationToken(), ("2026-10-01", "2026-10-01"))
        self.assertEqual(len(result), 550)

    def test_settings_zero_custom_legacy_invalid_and_dashboard_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(settings_module, "CONFIG_PATH", Path(directory)/"config.json"):
            for limit in [0, 550, 10000]:
                settings = replace(default_settings(), rss_limit=limit, news_dedup_enabled=False)
                save_settings(settings)
                restored = load_settings()
                self.assertEqual(restored.rss_limit, limit)
                self.assertFalse(restored.news_dedup_enabled)
            for data, expected in [({}, 20), ({"rss_limit": 0}, 0), ({"rss_limit": "700"}, 700), ({"rss_limit": -1}, 20), ({"rss_limit": "bad"}, 20), ({"rss_limit": None}, 20)]:
                settings_module.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")
                restored = load_settings()
                self.assertEqual(restored.rss_limit, expected)
                self.assertTrue(restored.news_dedup_enabled)
        app = QApplication.instance() or QApplication([])
        panel = DashboardPanel(replace(default_settings(), rss_limit=0))
        self.assertEqual(panel.news_limit_combo.currentData(), "unlimited")
        self.assertFalse(panel.rss_limit_spin.isEnabled())
        self.assertEqual(panel.settings_snapshot(default_settings()).rss_limit, 0)
        panel.news_limit_combo.setCurrentIndex(0)
        self.assertTrue(panel.rss_limit_spin.isEnabled())
        panel.rss_limit_spin.setValue(10000)
        panel.news_dedup_check.setChecked(False)
        snapshot = panel.settings_snapshot(default_settings())
        self.assertEqual(snapshot.rss_limit, 10000)
        self.assertFalse(snapshot.news_dedup_enabled)
        panel.close()
        dialog = FeedDialog(FeedSource("API", ARTICLES_URL, True))
        self.assertEqual(dialog.feed().rss, ARTICLES_URL)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
