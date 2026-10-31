"""Fixed data and mocked APIs only; real acceptance is recorded separately."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from ai.json_utils import AIResponseParseError
from feeds.launches import launch_context
from services import launch_roundup as r, launch_research as research
from services.launch_data import supplement_context, write_json
from services.news_dates import publication_evidence
from services.cancellation import TaskCancelledError
from services.roundup_export import export_package
from tests import test_launch_roundup as base
from tests.test_launch_research import RAW
from tests.test_launch_research import source, match, EVENT
from translator.search import SearchResult, SearchRateLimitError


def ll2_material(item, description='The spacecraft tests a radio receiver.'):
    item = deepcopy(item)
    item['description'] = description
    item['ll2_source'] = dict(url='https://ll.thespacedevs.com/2.3.0/launches/' + item['ll2_id'] + '/',
        description=description, mission_name=item['english_name'], net=item['net'], retrieved_at='2026-10-31T00:00:00Z',
        identity={k: item[k] for k in ('english_name', 'net', 'rocket', 'site', 'provider')})
    return item


def claimed():
    data = base.styled_generated()
    data['claims'] = [dict(id='c1', text='该航天器测试无线电接收机。', task_ids=['launch-1'],
                          fact_refs=['launch-1#ll2-description'])]
    data['segments'][1]['spoken'] += '[[claim:c1]]'
    return data


class MaterialsTests(unittest.TestCase):
    setUp = base.RoundupTests.setUp

    def add_material(self):
        self.draft['snapshot']['items'][0] = ll2_material(self.draft['snapshot']['items'][0])
        self.settings.deepseek_api_key = 'fixture-credential'

    def test_ll2_recovery_checks_id_and_actual_time_without_mutating_old_context(self):
        raw = dict(RAW, id='01234567-89ab-cdef-0123-456789abcdef')
        context = launch_context(raw)
        old = deepcopy(context)
        response = Mock()
        response.json.return_value = raw
        get = Mock(return_value=response)
        result = supplement_context(context, self.settings, self.token, get=get)
        self.assertEqual(context, old)
        self.assertEqual(result['description'], RAW['mission']['description'])
        self.assertTrue(research.background_facts(result))
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        response.json.return_value = dict(raw, net='2025-10-10T20:49:00Z')
        with self.assertRaisesRegex(ValueError, '时间'):
            supplement_context(context, self.settings, self.token, get=get)
        response.json.return_value = dict(RAW, id='different')
        with self.assertRaises(ValueError):
            supplement_context(context, self.settings, self.token, get=get)
        response.json.return_value = []
        with self.assertRaises(ValueError):
            supplement_context(context, self.settings, self.token, get=get)

    def test_unproven_original_description_is_context_only_and_changed_identity_invalidates(self):
        item = self.draft['snapshot']['items'][0]
        self.assertEqual(research.background_facts(item), [])
        item = ll2_material(item)
        self.assertEqual(len(research.background_facts(item)), 1)
        item['rocket'] = 'Other rocket'
        self.assertEqual(research.background_facts(item), [])

    def test_english_announcement_facts_do_not_require_an_invented_chinese_name(self):
        context = launch_context(RAW)
        event = 'On October 10, 2026 at 20:49 UTC, Orienspace launched Xiguang-4 01 on Gravity-1 at Haiyang.'
        quote = 'The spacecraft tests a radio receiver.'
        page = dict(source_id=0, text=event + ' ' + quote, url='https://www.orienspace.com/fixture')
        match = dict(source_id=0, chinese_name='', aliases=[], name_quote='', summary='', summary_quote='',
                     event_time=context['net'], event_quote=event, facts=[dict(quote=quote)])
        matches = research._validated_matches(dict(matches=[match]), [page], context)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['chinese_name'], '')
        match['event_time'] = '2025-10-10T20:49:00Z'
        self.assertEqual(research._validated_matches(dict(matches=[match]), [page], context), [])
        match['facts'] = [dict(quote='An invented purpose.')]
        with self.assertRaises(AIResponseParseError):
            research._validated_matches(dict(matches=[match]), [page], context)

    def test_launch_page_may_lack_publication_date_but_news_still_requires_it(self):
        html = '<title>Mission</title><article>' + 'A specific dated mission announcement. ' * 5 + '</article>'
        with self.assertRaises(ValueError):
            publication_evidence(html, 'https://www.spacex.com/fixture')
        page = publication_evidence(html, 'https://www.spacex.com/fixture', require_publication=False)
        self.assertEqual(page['published'], '')

    def test_conflict_excludes_ll2_fallback_rather_than_selecting_convenient_facts(self):
        item = ll2_material(self.draft['snapshot']['items'][0])
        item['research'] = dict(version=1, fingerprint=research.fingerprint(item), evidence_status='conflict',
                                sources=[dict(text='Version A'), dict(text='Version B')])
        self.assertEqual(research.background_facts(item), [])
        self.assertIn('冲突', r.material_status(item)['label'])
        self.assertEqual(len(item['research']['sources']), 2)
        previous = deepcopy(item['research'])
        item['research'].update(evidence_status='pending', previous_evidence=previous)
        self.assertEqual(research.background_facts(item), [])
        item['research'].update(human_status='confirmed', human_fingerprint=research.fingerprint(item),
            manual=dict(chinese_name='人工确认名称'),
            sources=[dict(source_id=0, url='https://example.invalid/fixture', text='存在冲突的背景原句。')],
            matches=[dict(source_id=0, summary='存在冲突的背景原句。')])
        self.draft['snapshot']['items'][0] = item
        catalog = r.token_catalog(self.draft)
        self.assertEqual(catalog['[[name:launch-1]]']['text'], '人工确认名称')
        self.assertFalse(any(k.startswith('[[fact:launch-1:') for k in catalog))

    def test_same_name_sources_with_different_facts_keep_both_and_block_writing(self):
        context = launch_context(RAW)
        quotes = ['该卫星主要用于对地观测与成像。', '该卫星主要用于通信技术验证。']
        urls = [RAW['info_urls'][0]['url'], 'https://www.xiopmspace.com/fixture-2']
        provider = Mock()
        provider.search.return_value = [SearchResult('mock', u, '', True) for u in urls]
        fetch = Mock(side_effect=lambda url, *_a, **_k: source(url, EVENT + quotes[urls.index(url)]))
        def organize(prompt, config, **kw):
            pages = json.loads(prompt.split('\n')[-1])['sources']
            matches = [dict(match(p), facts=[dict(quote=quotes[urls.index(p['url'])])]) for p in pages]
            data = dict(matches=matches, conflicts=[dict(source_ids=[0,1], quotes=quotes, reason='用途差异')])
            kw['validator'](data)
            return data
        service = research.LaunchResearchService(replace(self.settings, web_search_enabled=True), self.token,
                                                  search_provider=provider, fetch=fetch)
        with patch.object(research, 'request_json', side_effect=organize):
            record = service.review(context)
        self.assertEqual(record['evidence_status'], 'conflict')
        self.assertEqual(record['conflicts'][0]['quotes'], quotes)
        self.assertEqual(research.background_facts(dict(context, research=record)), [])

    def test_search_challenge_pauses_further_queries_but_records_the_failure(self):
        context = launch_context(RAW)
        provider = Mock()
        provider.search.side_effect = SearchRateLimitError('mock verification challenge')
        fetch = Mock(side_effect=lambda url, *_a, **_k: source(url))
        service = research.LaunchResearchService(replace(self.settings, web_search_enabled=True), self.token,
                                                  search_provider=provider, fetch=fetch)
        def organize(prompt, config, **kw):
            return dict(matches=[match(p) for p in json.loads(prompt.split('\n')[-1])['sources']])
        with patch.object(research, 'request_json', side_effect=organize):
            first = service.review(context)
            second = service.review(dict(context, ll2_id='another-fixed-fixture'))
        self.assertEqual(provider.search.call_count, 1)
        self.assertIn('verification challenge', second['reason'])
        self.assertEqual(first['evidence_status'], 'pending')

    def test_no_new_pages_does_not_repeat_the_same_organizing_request(self):
        context = launch_context(RAW)
        provider = Mock()
        provider.search.return_value = [SearchResult('old event', RAW['info_urls'][0]['url'], '', True)]
        fetch = Mock(side_effect=lambda url, *_a, **_k: source(url, EVENT.replace('2026', '2025')))
        service = research.LaunchResearchService(replace(self.settings, web_search_enabled=True), self.token,
                                                  search_provider=provider, fetch=fetch)
        def organize(prompt, config, **kw):
            return dict(matches=[match(p) for p in json.loads(prompt.split('\n')[-1])['sources']])
        with patch.object(research, 'request_json', side_effect=organize) as client:
            record = service.review(context)
        self.assertEqual(record['evidence_status'], 'pending')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(client.call_count, 1)

    def test_preparation_reuses_unchanged_background_and_force_rechecks_only_selected_id(self):
        self.add_material()
        first = self.draft['snapshot']['items'][0]
        first['research'] = dict(fingerprint=research.fingerprint(first), evidence_status='pending')
        service, hydrate = Mock(), Mock(side_effect=lambda i, *_: ll2_material(i))
        service.review.side_effect = lambda i, **kw: dict(fingerprint=research.fingerprint(i), evidence_status='pending', reason='mock search unavailable')
        before = deepcopy(self.draft['segments'])
        r.prepare_materials(self.draft, self.settings, self.token, service=service, hydrate=hydrate)
        self.assertEqual(hydrate.call_count, 2)
        self.assertEqual(service.review.call_count, 2)
        service.reset_mock()
        r.prepare_materials(self.draft, self.settings, self.token, service=service, hydrate=hydrate)
        service.review.assert_not_called()
        r.prepare_materials(self.draft, self.settings, self.token, service=service, hydrate=hydrate, recheck_id='launch-1')
        service.review.assert_called_once()
        self.assertTrue(service.review.call_args.kwargs['force'])
        self.assertEqual(self.draft['segments'], before)

    def test_supported_chinese_quantity_is_normalized_for_checking_without_changing_the_text(self):
        self.add_material()
        self.draft['snapshot']['items'][0] = ll2_material(self.draft['snapshot']['items'][0], 'A batch of 3 testing satellites.')
        data = claimed()
        data['claims'][0]['text'] = 'LL2记录载荷为三颗测试卫星。'
        segment = r.validate_generated(data, self.draft)[1]
        self.assertIn('三颗', segment['spoken'])
        self.assertEqual(next(s for s in segment['source_refs'] if s.get('claim_id') == 'c1')['support_review'], 'pending')

    def test_new_local_research_adoption_is_explicit_and_preserves_human_edits(self):
        item = self.draft['snapshot']['items'][0]
        new = ll2_material(item)
        record = dict(version=1, input=new, fingerprint=research.fingerprint(new), evidence_status='pending')
        self.draft['segments'][1].update(edited=True, spoken='人工修改保留')
        before = deepcopy(self.draft['segments'])
        with patch.object(r, 'load_launch_research', return_value={'launch-1': record}):
            self.assertFalse(item.get('ll2_source'))
            fresh = r.create_draft(self.collection, self.draft['start'], self.draft['end'])
            self.assertTrue(fresh['snapshot']['items'][0]['ll2_source'])
            self.assertFalse(item.get('ll2_source'))
            self.assertEqual(r.sync_materials(self.draft), ['launch-1'])
        self.assertTrue(item['ll2_source'])
        self.assertEqual(self.draft['segments'], before)
        self.assertEqual(r.material_status(item)['usable_facts'], 1)

    def test_full_materials_reach_actual_request_boundary_and_wording_review_is_separate(self):
        self.add_material()
        modules = []
        def respond(prompt, config, **kw):
            modules.append(kw['module_name'])
            self.assertNotIn(self.settings.deepseek_api_key, prompt)
            if kw['module_name'] == 'launch_roundup':
                payload = json.loads(prompt.split('\n')[-1])
                self.assertIn('radio receiver', payload['task_materials'][0]['original']['description'])
                self.assertEqual(payload['task_materials'][0]['usable_facts'][0]['evidence_level'], 'll2_record')
                result = claimed()
            else:
                result = dict(checks=[dict(id='c1', supported=True)])
            kw['validator'](result)
            return result
        with patch.object(r, 'request_json', side_effect=respond):
            segments = r.generate_script(self.draft, self.settings, self.token)
        self.assertEqual(modules, ['launch_roundup', 'roundup_fact_review'])
        source = next(s for s in segments[1]['source_refs'] if s.get('claim_id') == 'c1')
        self.assertEqual(source['citation_status'], 'exact_quote')
        self.assertEqual(source['support_review'], 'model_supported_not_human_confirmed')
        self.assertTrue(self.draft['writing_warnings'])
        self.draft['segments'] = segments
        out = export_package(self.draft, self.root, self.token, visuals=False)
        self.assertTrue((out / 'writing_input.json').exists())
        plan = [dict(task_ids=['launch-1'], fact_refs=['launch-1#ll2-description'])]
        self.draft['focus_plan'] = deepcopy(plan)
        single = claimed()
        single['segments'] = [single['segments'][1]]
        with patch.object(r, 'request_json', side_effect=[single, dict(checks=[dict(id='c1', supported=True)])]):
            r.generate_script(self.draft, self.settings, self.token, single_id='focus1')
        self.assertEqual(self.draft['focus_plan'], plan)

    def test_valid_quote_reference_does_not_make_an_unsupported_claim_pass(self):
        self.add_material()
        bad = claimed()
        bad['claims'][0]['text'] = '该航天器提升了全球通信能力。'
        with patch.object(r, 'request_json', side_effect=[bad, dict(checks=[dict(id='c1', supported=False, reason='没有提升能力的原文')])]):
            segments = r.generate_script(self.draft, self.settings, self.token)
        self.assertEqual({s['origin'] for s in segments}, {'local_template'})
        self.assertIn('未获来源支持', segments[0]['generation_reason'])

    def test_numbers_records_and_unmatched_sources_rejected_but_attribution_optional(self):
        self.add_material()
        for text in ('部署了999颗卫星。', '创造首次纪录。', '部署了三颗卫星。'):
            data = claimed()
            data['claims'][0]['text'] = text
            with self.subTest(text=text), self.assertRaises(AIResponseParseError):
                r.validate_generated(data, self.draft)
        self.assertTrue(r.validate_generated(claimed(), self.draft))
        data = claimed()
        data['claims'][0]['fact_refs'] = ['launch-1#unmatched-page']
        with self.assertRaises(AIResponseParseError):
            r.validate_generated(data, self.draft)

    def test_background_limited_script_may_be_short_and_manual_focus_still_protected(self):
        data = base.generated()
        data['segments'] = [data['segments'][0], dict(id='all', kind='remaining', spoken='其余任务中，' +
            '、'.join(f'[[name:launch-{i}]]' for i in (1, 2, 3)) + '。', screen='其余任务中',
            task_ids=['launch-1','launch-2','launch-3']), data['segments'][-1]]
        data['focus_plan'] = []
        self.assertEqual(len(r.validate_generated(data, self.draft)), 3)
        self.draft['focus_mode'] = 'manual'
        with self.assertRaises(AIResponseParseError):
            r.validate_generated(data, self.draft)

    def test_auto_focus_tracks_generated_highlights_but_manual_choices_do_not_change(self):
        data = base.generated()
        for segment, number in zip(data['segments'][1:4], (3, 1, 2)):
            segment.update(spoken=f'[[name:launch-{number}]]', screen=f'[[name:launch-{number}]]',
                           task_ids=[f'launch-{number}'])
        result = r.validate_generated(data, self.draft)
        r.apply_segments(self.draft, result)
        self.assertEqual({s['id'] for s in self.draft['selection'] if s['featured']}, {'launch-1','launch-3'})
        # Old automatic drafts are migrated locally on opening, without replacing their text.
        self.draft['selection'][2]['featured'] = False
        path = self.root/'auto-draft.json'
        r.save_draft(self.draft, path)
        loaded = r.load_draft(path)
        self.assertTrue(loaded['selection'][2]['featured'])
        self.assertEqual(loaded['segments'], result)
        self.draft['focus_mode'] = 'manual'
        before = deepcopy(self.draft['selection'])
        r.apply_segments(self.draft, result)
        self.assertEqual(self.draft['selection'], before)

    def test_cancel_keeps_completed_materials_and_all_existing_segments(self):
        before = deepcopy(self.draft['segments'])
        service = Mock()
        service.review.return_value = dict(evidence_status='pending')
        def progress(_):
            self.token.cancel()
        with self.assertRaises(TaskCancelledError):
            r.prepare_materials(self.draft, self.settings, self.token, service=service,
                               hydrate=lambda i, *_: ll2_material(i), on_progress=progress)
        self.assertTrue(self.draft['snapshot']['items'][0]['ll2_source'])
        self.assertFalse(self.draft['snapshot']['items'][1].get('ll2_source'))
        self.assertEqual(self.draft['segments'], before)

    def test_cancel_during_wording_review_preserves_the_original_human_paragraphs(self):
        self.add_material()
        self.draft['segments'][1].update(edited=True, spoken='人工稿仍保留')
        before = deepcopy(self.draft['segments'])
        with patch.object(r, 'request_json', side_effect=[claimed(), TaskCancelledError('mock review cancelled')]):
            with self.assertRaises(TaskCancelledError):
                r.generate_script(self.draft, self.settings, self.token)
        self.assertEqual(self.draft['segments'], before)

    def test_ll2_fetch_failure_is_visible_and_does_not_prevent_other_preparation(self):
        service = Mock()
        service.review.side_effect = lambda item, **kw: dict(evidence_status='pending', reason='mock no primary source')
        r.prepare_materials(self.draft, self.settings, self.token, service=service,
                            hydrate=Mock(side_effect=OSError('mock LL2 timeout')))
        self.assertEqual(service.review.call_count, 3)
        self.assertIn('LL2 timeout', r.material_status(self.draft['snapshot']['items'][0])['reason'])

    def test_ui_shows_material_status_without_fetching_on_open(self):
        from PySide6.QtWidgets import QApplication, QPushButton
        from ui.launch_roundup_dialog import LaunchRoundupDialog
        app = QApplication.instance() or QApplication([])
        with patch('ui.launch_roundup_dialog.load_existing', return_value=self.collection), \
             patch.object(r, 'prepare_materials') as prepare:
            dialog = LaunchRoundupDialog(self.settings)
            self.addCleanup(dialog.close)
            dialog.draft = deepcopy(self.draft)
            dialog._show_draft()
            self.assertEqual(dialog.tasks.columnCount(), 12)
            self.assertIn('背景不足', dialog.tasks.item(0,8).text())
            labels = [b.text() for b in dialog.findChildren(QPushButton)]
            self.assertIn('补充缺失资料（联网）', labels)
            self.assertIn('重新核对选中任务（联网）', labels)
            prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
