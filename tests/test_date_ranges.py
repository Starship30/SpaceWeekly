import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='spaceweekly-review-')
import unittest
from contextlib import ExitStack
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
from ui.widgets.dashboard_panel import DashboardPanel
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

    def test_dashboard_ranges_roundtrip_and_independent_disable(self):
        app = QApplication.instance() or QApplication([])
        base = replace(default_settings(), deepseek_api_key="", date_mode="custom",
                       start_date="2026-10-01", end_date="2026-10-08",
                       launch_range="custom", launch_start_date="2026-10-02",
                       launch_end_date="2026-10-09")
        for language in ["zh_CN", "en_US"]:
            set_language(language)
            panel = DashboardPanel(base)
            self.assertEqual(panel.settings_snapshot(base), base)
            for combo, start, end in [
                (panel.date_mode_combo, panel.start_date_edit, panel.end_date_edit),
                (panel.launch_range_combo, panel.launch_start_date_edit, panel.launch_end_date_edit),
            ]:
                self.assertTrue(start.isEnabled())
                self.assertFalse(start.parentWidget().isHidden())
                start.setDate(QDate(2026, 10, 10))
                end.setDate(QDate(2026, 10, 1))
                self.assertFalse(panel.date_ranges_valid())
                end.setDate(QDate(2026, 10, 10))
                self.assertTrue(panel.date_ranges_valid())
                combo.setCurrentIndex(combo.findData("disabled"))
                self.assertFalse(start.isEnabled())
                self.assertTrue(start.parentWidget().isHidden())
            snapshot = panel.settings_snapshot(base)
            self.assertEqual((snapshot.date_mode, snapshot.launch_range), ("disabled", "disabled"))
            dialog = SettingsDialog(snapshot)
            self.assertEqual(dialog.date_mode_combo.currentData(), "disabled")
            self.assertEqual(dialog.launch_range_combo.currentData(), "disabled")
            dialog.news_custom_radio.setChecked(True)
            dialog.start_date_edit.setDate(QDate(2026, 10, 3))
            panel.apply_settings(dialog.settings())
            self.assertEqual(panel.date_mode_combo.currentData(), "custom")
            self.assertEqual(panel.start_date_edit.date(), QDate(2026, 10, 3))
            self.assertEqual(panel.launch_range_combo.currentData(), "disabled")
            with tempfile.TemporaryDirectory() as directory, patch("services.settings.CONFIG_PATH", Path(directory)/"config.json"):
                save_settings(snapshot)
                self.assertEqual(load_settings().date_mode, "disabled")
            dialog.close()
            panel.close()

    def test_main_window_dates_settings_and_start_validation(self):
        import ui.main_window as main
        app = QApplication.instance() or QApplication([])
        base = replace(default_settings(), deepseek_api_key="", aerospace_translation_enabled=False)
        with patch.object(main, "load_settings", return_value=base), \
             patch.object(main, "load_feeds", return_value=[]), \
             patch.object(main, "save_settings") as save, \
             patch.object(main.MainWindow, "_connect_logging"), \
             patch.object(main.MainWindow, "_start_startup_checks"), \
             patch.object(main, "ReportWorker") as worker:
            window = main.MainWindow()
            panel = window.dashboard_panel
            panel.date_mode_combo.setCurrentIndex(panel.date_mode_combo.findData("custom"))
            panel.start_date_edit.setDate(QDate(2026, 10, 8))
            panel.end_date_edit.setDate(QDate(2026, 10, 1))
            with patch.object(main, "SettingsDialog") as dialog:
                dialog.return_value.exec.return_value = 0
                window._open_settings()
                self.assertEqual(dialog.call_args.args[0].start_date, "2026-10-08")
                self.assertEqual(dialog.call_args.args[0].date_mode, "custom")
            with patch.object(main.QMessageBox, "warning") as warning:
                window._start_report()
                warning.assert_called_once()
                save.assert_not_called()
                worker.assert_not_called()
            panel.end_date_edit.setDate(QDate(2026, 10, 10))
            panel.launch_range_combo.setCurrentIndex(panel.launch_range_combo.findData("custom"))
            panel.launch_start_date_edit.setDate(QDate(2026, 10, 2))
            panel.launch_end_date_edit.setDate(QDate(2026, 10, 9))
            with patch.object(main, "get_launches", return_value=[]) as launches, \
                 patch.object(main, "launch_last_error", return_value=""), \
                 patch.object(main.QMessageBox, "information"):
                window._test_launch_data()
                self.assertEqual(launches.call_args.kwargs["start_date"], "2026-10-02")
                self.assertEqual(launches.call_args.kwargs["end_date"], "2026-10-09")
            with patch.object(window.feed_panel, "save_state"):
                window._start_report()
            saved = save.call_args.args[0]
            self.assertEqual((saved.start_date, saved.end_date), ("2026-10-08", "2026-10-10"))
            self.assertEqual(saved.launch_range, "custom")
            worker.return_value.start.assert_called_once()
            window.worker = None
            window.close()

    def test_disabled_news_skips_all_sources_but_keeps_launches(self):
        import services.weekly_report as report
        from feeds.spaceflightnews import ARTICLES_URL, SpaceflightNewsProvider
        from feeds.spacecom import SpaceComWebProvider
        from services.feed_manager import FeedSource
        from models.article import Article
        from models.news import News
        settings = replace(default_settings(), deepseek_api_key="", date_mode="disabled",
                           launch_range="past_week", export_premiere=False)
        launch = Article(News("LL2", "测试发射", "2026-10-01", "", "https://example.com/launch"), "", "LL2")
        feeds = [FeedSource("RSS", "https://example.com/rss", True),
                 FeedSource("Web", "https://www.space.com/feeds/all", True),
                 FeedSource("API", ARTICLES_URL, True)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(report, "load_settings", return_value=settings))
            stack.enter_context(patch.object(report, "setup_logging"))
            stack.enter_context(patch.object(report, "apply_settings"))
            launch_fetch = stack.enter_context(patch.object(report, "_fetch_launch_articles", return_value=[launch]))
            export = stack.enter_context(patch.object(report, "_export_reports"))
            unused = [stack.enter_context(patch(target)) for target in [
                "feedparser.parse", "services.weekly_report.download",
                "services.weekly_report.assess_importance", "services.weekly_report.generate_content",
                "services.weekly_report.translate_titles",
            ]]
            unused.append(stack.enter_context(patch.object(SpaceComWebProvider, "fetch")))
            unused.append(stack.enter_context(patch.object(SpaceflightNewsProvider, "fetch")))
            self.assertEqual(report.generate_weekly(feeds, export_sqlite=False), [])
            self.assertEqual(launch_fetch.call_args.args[0], "past_week")
            self.assertEqual(export.call_args.args[4], [launch])
            for operation in unused:
                operation.assert_not_called()

if __name__ == '__main__':
    unittest.main()
