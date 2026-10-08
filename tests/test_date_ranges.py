import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='spaceweekly-review-')
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication
from config import app_version
from feeds.launches import _query_params
from services.settings import default_settings, load_settings, save_settings
from services.weekly_report import _date_range, get_news
from ui.settings_dialog import SettingsDialog
from i18n import tr, set_language

class DateRangeTests(unittest.TestCase):
    def test_launch_bounds_and_presets(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        past = _query_params(10, 'past_week', now)
        self.assertEqual(past['net__gte'], '2026-10-01T12:00:00Z')
        self.assertEqual(past['ordering'], '-net')
        self.assertEqual(_query_params(10, 'next_30_days', now)['net__lte'], '2026-11-07T12:00:00Z')
        self.assertEqual(_query_params(10, 'past_30_days', now)['net__gte'], '2026-09-08T12:00:00Z')
        custom = _query_params(10, 'custom', start_date='2026-10-01', end_date='2026-10-08')
        self.assertEqual(custom['net__gte'], '2026-09-30T16:00:00Z')
        self.assertEqual(custom['net__lt'], '2026-10-08T16:00:00Z')
        for start, end in [('2026-10-08', '2026-10-01'), ('', '')]:
            with self.assertRaises(ValueError):
                _query_params(10, 'custom', start_date=start, end_date=end)
        with self.assertRaises(ValueError):
            _date_range(replace(default_settings(), date_mode='custom', start_date='2026-10-08', end_date='2026-10-01'))

    def test_news_filter_precedes_limit_and_accepts_iso_updated_dates(self):
        from types import SimpleNamespace
        from services.weekly_report import _fetch_news, _published_date
        from services.feed_manager import FeedSource
        from services.cancellation import CancellationToken
        entries = [
            SimpleNamespace(link="https://example.com/new", published="2026-10-09T12:00:00Z"),
            SimpleNamespace(link="https://example.com/selected", updated="2026-10-01T12:00:00Z"),
            SimpleNamespace(link="https://example.com/old", published="2026-09-30T12:00:00Z"),
        ]
        with patch("feedparser.parse", return_value=SimpleNamespace(entries=entries)):
            news = _fetch_news([FeedSource("Test", "https://example.com/rss", True)], 1, CancellationToken(), ("2026-10-01", "2026-10-08"))
        self.assertEqual([item.url for item in news], ["https://example.com/selected"])
        self.assertEqual(_published_date("Thu, 01 Oct 2026 12:00:00 GMT").isoformat(), "2026-10-01")
        self.assertIsNone(_published_date("invalid"))

    def test_web_pagination_finds_dates_before_consuming_limit(self):
        from feeds.spacecom import SpaceComWebProvider
        from models.article import Article
        from models.news import News
        def article(day):
            return Article(News("Space.com", "Test", f"2026-10-{day:02}T12:00:00Z", "", f"https://www.space.com/test-{day}"), "", "Test")
        provider = SpaceComWebProvider()
        with patch.object(provider, "fetch_listing", return_value="html") as fetch, patch.object(provider, "parse_listing", side_effect=[[article(9)], [article(8), article(7)]]), patch.object(provider, "_next_listing", return_value="https://www.space.com/news/page/2"):
            result = provider.fetch(limit=1, date_range=("2026-10-01", "2026-10-08"))
        self.assertEqual(result[0].news.url, "https://www.space.com/test-8")
        self.assertEqual(fetch.call_count, 2)

    def test_settings_and_pipeline(self):
        import services.settings as module
        settings = replace(default_settings(), launch_range='custom', launch_start_date='2026-10-01', launch_end_date='2026-10-08')
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'CONFIG_PATH', Path(directory)/'config.json'):
            save_settings(settings)
            restored = load_settings()
            self.assertEqual(restored.launch_start_date, settings.launch_start_date)
            self.assertEqual(restored.launch_range, 'custom')
        with patch('services.weekly_report._fetch_news', return_value=[]), patch('services.weekly_report.get_launches', return_value=[]) as fetch:
            get_news([], launch_range='custom', launch_start_date=settings.launch_start_date, launch_end_date=settings.launch_end_date)
            self.assertEqual(fetch.call_args.kwargs['start_date'], settings.launch_start_date)

    def test_dialog_switches_and_validation(self):
        app = QApplication.instance() or QApplication([])
        for language in ['zh_CN', 'en_US']:
            set_language(language)
            self.assertIn(app_version(), tr('app.title', version=app_version()))
            dialog = SettingsDialog(replace(default_settings(), deepseek_api_key=""))
            self.assertFalse(dialog.start_date_edit.isEnabled())
            self.assertFalse(dialog.launch_start_date_edit.isEnabled())
            for custom, combo, start, end in [(dialog.news_custom_radio, dialog.date_mode_combo, dialog.start_date_edit, dialog.end_date_edit), (dialog.launch_custom_radio, dialog.launch_range_combo, dialog.launch_start_date_edit, dialog.launch_end_date_edit)]:
                custom.setChecked(True)
                self.assertFalse(combo.isEnabled())
                self.assertTrue(start.isEnabled())
                start.setDate(QDate(2026, 10, 8))
                end.setDate(QDate(2026, 10, 1))
                with patch('ui.settings_dialog.QMessageBox.warning') as warning:
                    dialog.accept()
                    warning.assert_called_once()
                end.setDate(QDate(2026, 10, 8))
            self.assertEqual(dialog.settings().launch_start_date, '2026-10-08')
            dialog.close()

if __name__ == '__main__':
    unittest.main()
