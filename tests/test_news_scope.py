"""Saved source ranges must survive elapsed time without bypassing date edits."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from feeds import launches
from models.article import Article
from models.news import News
from services import production, weekly_report as report
from services.cancellation import CancellationToken
from services.settings import default_settings


class NewsScopeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 15, 13, 55, tzinfo=timezone.utc)
        self.settings = replace(default_settings(), date_mode='custom', start_date='2026-10-28',
            end_date='2026-11-03', launch_range='past_week', deepseek_api_key='',
            deepseek_base_url='', model_candidates=[], aerospace_translation_enabled=False,
            news_event_date_check_enabled=False, export_sqlite=False, export_markdown=True,
            export_word=False, export_premiere=False)
        clock = patch.object(launches, 'datetime', wraps=datetime)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.clock.now.return_value = self.now

    def scope(self, *, legacy=False, settings=None):
        value = report.prepared_scope(settings or self.settings)
        if legacy:
            value.pop('launch_range')
            value['launches'] = {key: value['launches'].get(key) for key in ('net__gte', 'net__lt')}
        return value

    def bundle(self, scope):
        article = Article(News('Test source', 'Fixture news', '2026-11-03T00:00:00Z',
            'Test summary', 'https://example.invalid/news'), 'Test body', 'fixture')
        return dict(articles=[article], launch_articles=[], launch_video_items=[],
                    assessments={}, summaries={}, context=None, source_scope=scope)

    def test_rolling_ranges_survive_seconds_and_preserve_both_query_bounds(self):
        for mode in ('past_week', 'next_week', 'past_30_days', 'next_30_days'):
            for legacy in (False, True):
                with self.subTest(mode=mode, legacy=legacy):
                    self.clock.now.return_value = self.now
                    settings = replace(self.settings, launch_range=mode)
                    scope = self.scope(legacy=legacy, settings=settings)
                    if not legacy:
                        self.assertIsNotNone(scope['launches']['net__lte'])
                    self.clock.now.return_value = self.now + timedelta(minutes=10)
                    self.assertTrue(report.prepared_scope_matches(scope, settings))

    def test_news_launch_mode_dates_and_timezone_edits_still_require_collection(self):
        for legacy in (False, True):
            scope = self.scope(legacy=legacy)
            for change in (dict(start_date='2026-10-27'), dict(end_date='2026-11-02'),
                           dict(date_mode='disabled'), dict(launch_range='next_week'),
                           dict(launch_range='past_30_days'), dict(launch_range='disabled'),
                           dict(display_timezone='UTC')):
                with self.subTest(legacy=legacy, change=change):
                    self.assertFalse(report.prepared_scope_matches(scope, replace(self.settings, **change)))
        settings = replace(self.settings, launch_range='custom', launch_start_date='2026-10-28',
                           launch_end_date='2026-11-03')
        scope = self.scope(settings=settings)
        for change in (dict(launch_start_date='2026-10-27'), dict(launch_end_date='2026-11-04')):
            self.assertFalse(report.prepared_scope_matches(scope, replace(settings, **change)))
        self.assertTrue(report.prepared_scope_matches(scope, settings))
        scope.pop('launch_range')
        self.assertTrue(report.prepared_scope_matches(scope, settings))
        self.assertFalse(report.prepared_scope_matches(scope, self.settings))

    def test_local_day_boundary_invalid_bounds_and_system_timezone(self):
        for legacy in (False, True):
            self.clock.now.return_value = self.now
            scope = self.scope(legacy=legacy)
            # Midnight UTC is still hours away; Beijing has already entered the next day.
            self.clock.now.return_value = self.now + timedelta(hours=1)
            self.assertFalse(report.prepared_scope_matches(scope, self.settings))
        self.clock.now.return_value = self.now
        for key, value in (('net__gte', 'invalid'), ('net__lte', 'invalid'),
                           ('net__lte', '2026-11-05T15:13:55Z')):
            scope = self.scope();scope['launches'][key] = value
            self.assertFalse(report.prepared_scope_matches(scope, self.settings))
        settings = replace(self.settings, display_timezone='system')
        self.assertTrue(report.prepared_scope_matches(self.scope(settings=settings), settings))
        self.assertTrue(report.prepared_scope_matches(None, self.settings))
        settings = replace(self.settings, launch_range='disabled')
        self.assertTrue(report.prepared_scope_matches(self.scope(settings=settings), settings))

    def test_legacy_sources_reach_ai_screening_without_refetch_and_keep_original_scope(self):
        bundle = self.bundle(self.scope(legacy=True))
        original = deepcopy(bundle)
        self.clock.now.return_value = self.now + timedelta(minutes=10)
        with ExitStack() as stack:
            stack.enter_context(patch.object(report, 'setup_logging'))
            stack.enter_context(patch.object(report, 'load_settings', return_value=self.settings))
            stack.enter_context(patch.object(report, 'apply_settings'))
            fetch = stack.enter_context(patch.object(report, 'get_news', side_effect=AssertionError('No refetch')))
            screen = stack.enter_context(patch.object(report, '_screen_articles', wraps=report._screen_articles))
            result = []
            articles = report.generate_weekly([], export_sqlite=False, prepared=bundle,
                defer_exports=True, result_callback=result.append)
        self.assertEqual(articles, bundle['articles'])
        screen.assert_called_once();fetch.assert_not_called()
        self.assertEqual(result[0]['source_scope'], original['source_scope'])
        self.assertEqual(bundle, original)

    def test_date_edit_is_rejected_before_ai_fetch_or_export(self):
        bundle = self.bundle(self.scope(legacy=True))
        settings = replace(self.settings, start_date='2026-10-27')
        with patch.object(report, 'load_settings', return_value=settings), \
             patch.object(report, 'setup_logging'), patch.object(report, 'apply_settings'), \
             patch.object(report, 'get_news') as fetch, \
             patch.object(report, '_screen_articles') as screen, \
             patch('exporters.markdown.export_markdown') as export:
            with self.assertRaisesRegex(ValueError, '范围'):
                report.generate_weekly([], export_sqlite=False, prepared=bundle, defer_exports=True)
            with self.assertRaisesRegex(ValueError, '范围'):
                production.export_news(bundle, settings, CancellationToken())
        fetch.assert_not_called();screen.assert_not_called();export.assert_not_called()

    def test_collection_captures_scope_once_even_if_clock_moves_during_fetch(self):
        scope = self.scope()
        bundle = self.bundle(scope)
        def fetch(*args, **kwargs):
            self.clock.now.return_value = self.now + timedelta(hours=1)
            return [bundle['articles'][0].news], []
        with patch.object(report, 'load_settings', return_value=self.settings), \
             patch.object(report, 'setup_logging'), patch.object(report, 'apply_settings'), \
             patch.object(report, 'get_news', side_effect=fetch), \
             patch.object(report, '_parse_articles', return_value=bundle['articles']), \
             patch.object(report, 'prepared_scope', wraps=report.prepared_scope) as capture:
            result = []
            report.generate_weekly([], export_sqlite=False, collect_only=True, result_callback=result.append)
        capture.assert_called_once()
        self.assertEqual(result[0]['source_scope'], scope)

    def test_export_uses_same_legacy_scope_validation_offline(self):
        bundle = self.bundle(self.scope(legacy=True))
        self.clock.now.return_value = self.now + timedelta(minutes=10)
        with patch('services.settings.apply_settings'), \
             patch('exporters.markdown.export_markdown', return_value=Path('test.md')) as export, \
             patch.object(report, 'get_news', side_effect=AssertionError('No refetch')):
            self.assertEqual(production.export_news(bundle, self.settings, CancellationToken()), ['test.md'])
        export.assert_called_once()


if __name__ == '__main__':
    unittest.main()
