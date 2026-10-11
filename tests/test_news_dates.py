import copy
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from ai.json_utils import AIResponseParseError
from models.article import Article
from models.news import News
from services import news_dates as dates
from services.cancellation import CancellationToken, TaskCancelledError
from services.settings import default_settings
from translator.search import SearchResult

CAS = 'https://www.cas.cn/yx/202609/t20260930_5121293.shtml'
NSF = 'https://www.nasaspaceflight.com/2026/10/smile-uvi-sxi-commissioning/'
OLD = 'The team announced the first science images and data from SMILE on September 30.'
NEW = 'The team announced a new instrument calibration result on October 7.'


def article(title='SMILE first science results', url=NSF):
    return Article(News('NSF', title, '2026-10-07T12:00:00Z', title, url), title, 'RSS Summary')


def page(day='2026-09-30', text=OLD):
    return f'<html><head><title>SMILE science announcement</title><meta name="PubDate" content="{day}"><meta property="article:modified_time" content="2026-10-09"></head><body><article>{text} The spacecraft launched on May 19. An observation was made on August 4.</article></body></html>'


class NewsDateTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(default_settings(), deepseek_api_key='fake-test-key',
                                deepseek_base_url='https://example.com', deepseek_model='test-model')
        self.token = CancellationToken()
        wait = patch.object(self.token, 'wait', return_value=False)
        wait.start();self.addCleanup(wait.stop)
        self.provider = Mock()
        self.provider.search.return_value = [SearchResult('SMILE announcement', CAS, '')]

    def run_check(self, rows, sources=None, match=None):
        if sources is None:
            sources = [dates.publication_evidence(page(), CAS)]
        if match is None:
            match = {'matches': [dict(evidence_id='source_1', quote=OLD)]}
        with patch.object(dates, 'create_search_provider', return_value=self.provider), \
             patch.object(dates, '_fetch_evidence', side_effect=sources) as fetch, \
             patch.object(dates, 'request_json', return_value=match) as ai:
            result = dates.verify_news_dates(rows, self.settings, ('2026-10-04', '2026-10-10'), self.token)
        return result, fetch, ai

    def test_smile_late_report_excluded_without_changing_original_dates(self):
        row = article(); original = copy.deepcopy(row)
        with self.assertLogs('services.news_dates', level='INFO') as logs:
            result, fetch, ai = self.run_check([row])
        self.assertEqual(result, ([], 1))
        self.assertEqual(row, original)
        self.assertIn('2026-09-30', '\n'.join(logs.output))
        self.assertIn(CAS, '\n'.join(logs.output))
        self.assertEqual(ai.call_args.kwargs['max_attempts'], 1)
        ai.call_args.kwargs['validator']({'matches': [dict(evidence_id='source_1', quote=OLD)]})

    def test_new_progress_with_historical_background_retained(self):
        row = article('SMILE new calibration result')
        evidence = dates.publication_evidence(page('2026-10-07', NEW), CAS)
        result, _, _ = self.run_check([row], [evidence], {'matches': [dict(evidence_id='source_1', quote=NEW)]})
        self.assertEqual(result, ([row], 1))
        self.assertEqual(evidence['published'], '2026-10-07')

    def test_earliest_matching_announcement_used_even_if_search_order_is_newest_first(self):
        self.provider.search.return_value.append(SearchResult('Earlier results', 'https://www.esa.int/old', ''))
        sources = [dates.publication_evidence(page('2026-10-07', OLD), CAS),
                   dates.publication_evidence(page(), 'https://www.esa.int/old')]
        result, _, _ = self.run_check([article()], sources, {'matches': [
            dict(evidence_id='source_1', quote=OLD), dict(evidence_id='source_2', quote=OLD)]})
        self.assertEqual(result, ([], 1))

    def test_metadata_publication_priority_timezone_and_modified_only_rejected(self):
        html = '<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","datePublished":"2026-10-04T01:00:00+08:00","dateModified":"2026-10-09"}]}</script><article>'+OLD+'</article>'
        self.assertEqual(dates.publication_evidence(html, CAS)['published'], '2026-10-03')
        for html in [page().replace('<meta name="PubDate" content="2026-09-30">', ''),
                     page().replace('</head>', '<meta property="article:published_time" content="2026-10-08"></head>')]:
            with self.assertRaises(ValueError): dates.publication_evidence(html, CAS)
        html = '<p>发布时间：2026年9月30日</p><article>'+OLD+'</article>'
        self.assertEqual(dates.publication_evidence(html, CAS)['published'], '2026-09-30')

    def test_esa_date_is_scoped_to_article_not_related_items(self):
        html = '<article><div class="meta article__item"><span>30/09/2026</span><span>100 views</span></div>'+OLD+'</article><aside><div class="meta"><span>07/10/2026</span></div></aside>'
        self.assertEqual(dates.publication_evidence(html,'https://www.esa.int/test')['published'], '2026-09-30')
        with self.assertRaises(ValueError): dates.publication_evidence(html,CAS)

    def test_fabricated_quote_date_or_source_rejected(self):
        source = dict(dates.publication_evidence(page(), CAS), evidence_id='source_1')
        for value in [{'matches': 'yes'}, {'matches': [dict(evidence_id='source_99', quote=OLD)]},
                      {'matches': [dict(evidence_id='source_1', quote='Invented announcement on October 7.')]},
                      {'matches': [dict(evidence_id='source_1', quote=OLD)]*2}]:
            with self.assertRaises(AIResponseParseError): dates._matched_evidence(value, [source])
        result, _, _ = self.run_check([article()], match={'matches': [dict(evidence_id='source_1', quote='Made up evidence that never occurred.')]})
        self.assertEqual(result, ([article()], 1))

    def test_disabled_no_range_or_no_news_never_search_or_call_ai(self):
        rows = [article()]
        with patch.object(dates, 'create_search_provider') as provider, patch.object(dates, 'request_json') as ai:
            self.assertEqual(dates.verify_news_dates(rows, replace(self.settings, news_event_date_check_enabled=False), ('2026-10-04','2026-10-10'), self.token), (rows, 0))
            self.assertEqual(dates.verify_news_dates(rows, self.settings, None, self.token), (rows, 0))
            self.assertEqual(dates.verify_news_dates([], self.settings, ('2026-10-04','2026-10-10'), self.token), ([], 0))
        provider.assert_not_called(); ai.assert_not_called()

    def test_unconfirmed_news_retained_by_publication_not_claimed_verified(self):
        rows = [article()]
        with patch.object(dates, 'create_search_provider') as provider:
            self.assertEqual(dates.verify_news_dates(rows, replace(self.settings, deepseek_api_key=''), ('2026-10-04','2026-10-10'), self.token), (rows, 0))
            provider.assert_not_called()
        self.provider.search.side_effect = RuntimeError('Search unavailable')
        with self.assertLogs('services.news_dates',level='INFO') as logs:
            result, _, ai = self.run_check(rows)
        self.assertEqual(result,(rows,0));ai.assert_not_called()
        self.assertIn('已核验 0；待复核 1；排除 0；保留 1 / 1','\n'.join(logs.output))
        self.provider.search.side_effect = None
        self.provider.search.return_value = [SearchResult('Not official','https://news.example/test','',True),SearchResult('Same organization','https://www.nasaspaceflight.com/another','')]
        result, fetch, ai = self.run_check(rows)
        self.assertEqual(result,(rows,0));fetch.assert_not_called();ai.assert_not_called()
        self.provider.search.return_value = [SearchResult('Official',CAS,'')]
        result, _, _ = self.run_check(rows,match={'matches':[]})
        self.assertEqual(result,(rows,1))

    def test_limited_search_preserves_21_in_range_reports_and_stops_requests(self):
        rows = [article(f'Fictional news {i}') for i in range(21)]
        self.provider.search.side_effect = dates.SearchRateLimitError('HTTP 429')
        with self.assertLogs('services.news_dates',level='INFO') as logs:
            result, fetch, ai = self.run_check(rows)
        self.assertEqual(result,(rows,0))
        self.provider.search.assert_called_once_with(rows[0].news.title,max_results=8)
        fetch.assert_not_called();ai.assert_not_called()
        self.assertIn('已核验 0；待复核 21；排除 0；保留 21 / 21','\n'.join(logs.output))

    def test_missing_or_out_of_range_publication_never_passes_failure_fallback(self):
        good = article('Valid')
        rows = [article('Old'),good,article('Undated')]
        rows[0].news.published = '2026-09-30'
        rows[2].news.published = ''
        self.provider.search.side_effect = RuntimeError('offline')
        result,_,_ = self.run_check(rows)
        self.assertEqual(result,([good],0))
        with patch.object(dates,'create_search_provider') as provider:
            self.assertEqual(dates.verify_news_dates(rows,replace(self.settings,deepseek_api_key=''),('2026-10-04','2026-10-10'),self.token),([good],0))
            provider.assert_not_called()

    def test_search_query_and_provider_are_news_specific(self):
        with patch.object(dates,'create_search_provider',return_value=self.provider) as factory,patch.object(dates,'_fetch_evidence',return_value=dates.publication_evidence(page(),CAS)),patch.object(dates,'request_json',return_value={'matches':[]}):
            dates.verify_news_dates([article()],self.settings,('2026-10-04','2026-10-10'),self.token)
        factory.assert_called_once_with(self.settings,allow_wikipedia_fallback=False)
        self.provider.search.assert_called_once_with(article().news.title,max_results=8)

    def test_cancel_is_not_converted_to_missing_evidence(self):
        self.provider.search.side_effect = lambda *args, **kwargs: self.token.cancel() or []
        with self.assertRaises(TaskCancelledError): self.run_check([article()])

    def test_cancel_during_search_spacing_stops_before_second_request(self):
        self.provider.search.return_value = []
        self.token.wait.side_effect = lambda seconds: self.token.cancel()
        with self.assertRaises(TaskCancelledError):self.run_check([article(),article('Second')])
        self.provider.search.assert_called_once()

    def test_pending_news_keeps_order_with_verified_and_excluded_news(self):
        rows=[article('Pending'),article('Verified'),article('Old')]
        self.provider.search.side_effect=[RuntimeError('offline'),[SearchResult('New',CAS,'')],[SearchResult('Old','https://www.esa.int/old','')]]
        sources=[dates.publication_evidence(page('2026-10-07',NEW),CAS),dates.publication_evidence(page(),'https://www.esa.int/old')]
        with patch.object(dates,'create_search_provider',return_value=self.provider),patch.object(dates,'_fetch_evidence',side_effect=sources),patch.object(dates,'request_json',side_effect=[{'matches':[dict(evidence_id='source_1',quote=NEW)]},{'matches':[dict(evidence_id='source_1',quote=OLD)]}]):
            with self.assertLogs('services.news_dates',level='INFO') as logs:
                result=dates.verify_news_dates(rows,self.settings,('2026-10-04','2026-10-10'),self.token)
        self.assertEqual(result,([rows[0],rows[1]],2))
        self.assertIn('已核验 1；待复核 1；排除 1；保留 2 / 3','\n'.join(logs.output))

    def test_source_cache_reuses_fetch_but_checks_each_news_fact(self):
        result, fetch, ai = self.run_check([article(), article('Other science result')])
        self.assertEqual(result, ([], 2));fetch.assert_called_once();self.assertEqual(ai.call_count, 2)

    def test_pipeline_gate_runs_before_database_summary_and_exports(self):
        from contextlib import ExitStack
        from services import weekly_report as report
        old, new = article(), article('New calibration result')
        settings = replace(self.settings, date_mode='custom', start_date='2026-10-04',
                           end_date='2026-10-10', launch_range='disabled')
        self.provider.search.side_effect = [[SearchResult('Old', CAS, '')],
                                           [SearchResult('New', 'https://www.esa.int/new', '')]]
        with ExitStack() as stack:
            stack.enter_context(patch.object(report, 'load_settings', return_value=settings))
            stack.enter_context(patch.object(report, 'setup_logging'))
            stack.enter_context(patch.object(report, 'apply_settings'))
            stack.enter_context(patch.object(report, '_resolve_runtime_model', return_value=settings))
            stack.enter_context(patch.object(report, 'initialize_database'))
            stack.enter_context(patch.object(report, 'get_news', return_value=([old.news,new.news], [])))
            stack.enter_context(patch.object(report, '_parse_articles', return_value=[old,new]))
            stack.enter_context(patch.object(report, '_screen_articles', return_value=([old,new], 0)))
            stack.enter_context(patch.object(dates, 'create_search_provider', return_value=self.provider))
            stack.enter_context(patch.object(dates, '_fetch_evidence', side_effect=[
                dates.publication_evidence(page(), CAS),
                dates.publication_evidence(page('2026-10-07', NEW), 'https://www.esa.int/new')]))
            stack.enter_context(patch.object(dates, 'request_json', side_effect=[
                {'matches': [dict(evidence_id='source_1',quote=OLD)]},
                {'matches': [dict(evidence_id='source_1',quote=NEW)]}]))
            database = stack.enter_context(patch.object(report, 'save_article', return_value=True))
            content = stack.enter_context(patch.object(report, '_generate_selected_content', return_value=2))
            stack.enter_context(patch.object(report, '_localize_selected_titles', return_value=({},2)))
            export = stack.enter_context(patch.object(report, '_export_reports'))
            self.assertEqual(report.generate_weekly([], export_sqlite=True), [new])
        database.assert_called_once_with(new)
        self.assertEqual(content.call_args.args[0], [new])
        self.assertEqual(export.call_args.args[0], [new])

    def test_url_and_redirect_boundaries(self):
        for url in ['https://cas.cn.evil.example/test','https://user:pass@www.cas.cn/x', 'http://www.cas.cn/x','https://www.cas.cn:444/x']:
            self.assertFalse(dates._official_url(url))
        self.assertTrue(dates._official_url(CAS))
        response = Mock();response.__enter__ = Mock(return_value=response);response.__exit__ = Mock(return_value=False)
        response.is_redirect = True;response.headers = {'Location': 'https://other.example/x'}
        with patch.object(dates.requests, 'get', return_value=response) as get:
            with self.assertRaises(ValueError): dates._fetch_evidence(CAS, self.settings, self.token)
        get.assert_called_once();self.assertFalse(get.call_args.kwargs['allow_redirects'])


if __name__ == '__main__': unittest.main()
