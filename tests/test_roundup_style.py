"""Editorial policy regression: fixed snapshots and mocked APIs, no live credentials."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from ai.json_utils import AIResponseParseError
from feeds.launches import launch_context
from services import launch_roundup as r, roundup_style as style, roundup_polish as polish
from services import prompt_manager, settings as settings_module
from services.roundup_export import export_package
from tests import test_launch_roundup as base


class StyleTests(unittest.TestCase):
    setUp = base.RoundupTests.setUp

    def test_country_company_groups_sort_instants_without_changing_raw_fields(self):
        items = self.draft['snapshot']['items']
        items[0].update(provider='China Aerospace Science and Technology Corporation', net='2026-10-22T03:25:00+08:00')
        items[1].update(provider='China Aerospace Science and Technology Corporation', net='2026-10-21T10:15:00Z')
        items[2].update(provider='Rocket Lab', operator_country='', site_country='New Zealand')
        before = deepcopy(self.draft)
        groups = style.groups(self.draft)
        self.assertEqual(groups[0], dict(label='中国方面', task_ids=['launch-2', 'launch-1']))
        self.assertEqual(groups[1]['label'], '美国 Rocket Lab 方面')
        self.assertEqual(style.affiliation(dict(provider='SpaceX', site_country='New Zealand'))['label'], '美国 SpaceX 方面')
        self.assertEqual(style.affiliation(dict(provider='Galactic Energy', operator_country='China'))['label'], '中国星河动力方面')
        self.assertNotIn('新西兰', style.affiliation(dict(provider='Unknown Co', site_country='New Zealand'))['label'])
        self.assertEqual(self.draft, before)

    def test_manual_focus_in_many_company_groups_does_not_force_cross_company_merge(self):
        items = [dict(base.context(n), provider=provider) for n, provider in enumerate(
            ('China Aerospace Science and Technology Corporation', 'SpaceX', 'Rocket Lab', 'Other Co'), 1)]
        draft = r.create_draft(dict(self.collection, items=items), '2026-10-20', '2026-10-29')
        draft['focus_mode'] = 'manual'
        for selection in draft['selection']:
            selection['featured'] = True
        script = r.local_script(draft, 'mock no API')
        self.assertEqual(len([s for s in script if s['kind'] == 'highlight']), 4)
        self.assertTrue(all(len(s['task_ids']) == 1 for s in script if s['task_ids']))

    def test_ll2_list_country_and_separate_location_pad_are_kept_with_links(self):
        raw = dict(id='fixture', mission=dict(name='Fixture'), net='2026-10-21T00:00:00Z',
            pad=dict(name='Space Launch Complex 4E', location=dict(name='Known spaceport', country=dict(name='Site country'))),
            launch_service_provider=dict(name='Rocket Lab', country=[dict(name='United States of America')]),
            info_urls=[dict(url='https://example.invalid/one')], vid_urls=[dict(url='https://example.invalid/video')])
        before = deepcopy(raw)
        event = launch_context(raw)
        self.assertEqual(event['operator_country'], 'United States of America')
        self.assertEqual((event['site_name'], event['pad_name']), ('Known spaceport', 'Space Launch Complex 4E'))
        self.assertEqual(style.event_context(event, self.draft)['pad_code'], 'SLC-4E')
        self.assertEqual(len(event['info_urls']), 1)
        self.assertEqual(len(event['video_urls']), 1)
        self.assertEqual(raw, before)
        raw['launch_service_provider']['country'] = dict(name='China')
        self.assertEqual(launch_context(raw)['operator_country'], 'China')

    def test_display_timezone_controls_dates_range_and_midnight_without_site_timezone(self):
        item = dict(base.context(), net='2026-10-23T19:25:00Z', site_country='United States')
        beijing = style.event_context(item, self.draft)
        utc_draft = dict(self.draft, timezone='UTC')
        utc = style.event_context(item, utc_draft)
        self.assertEqual((beijing['date'], beijing['date_reading']), ('2026-10-24', '10月24日凌晨'))
        self.assertEqual((utc['date'], utc['date_reading']), ('2026-10-23', '10月23日傍晚'))
        collection = dict(self.collection, items=[dict(item, net='2026-10-23T16:00:00Z')])
        self.assertEqual(len(r.create_draft(collection, '2026-10-24', '2026-10-24')['selection']), 1)
        self.assertEqual(len(r.create_draft(collection, '2026-10-24', '2026-10-24', 'UTC')['selection']), 0)
        self.assertFalse(r.statistics(utc_draft)['complete'])

    def test_week_opening_custom_range_future_and_scoped_closing(self):
        week = dict(self.draft, start='2026-10-20', end='2026-10-26', as_of='2026-11-01T00:00:00Z')
        self.assertEqual(style.opening(week), '先来看一下过去一周的发射任务。')
        custom = dict(week, end='2026-10-22')
        self.assertNotIn('过去一周', style.opening(custom))
        self.assertIn('2026年10月22日', style.opening(custom))
        self.assertNotIn('过去一周', style.opening(dict(week, as_of='2026-01-01T00:00:00Z')))
        self.draft['selection'][2]['included'] = False
        stats = r.statistics(self.draft)
        closing = style.transition(self.draft, stats)
        self.assertIn('本期关注的2次', closing)
        self.assertNotIn('全球', closing)
        self.assertEqual(stats['results']['success'], 2)  # Landing failed in both source records.
        self.draft['snapshot']['items'][0]['status_id'] = 987
        self.assertNotIn('均取得成功', style.transition(self.draft, r.statistics(self.draft)))

    def test_missing_pad_is_not_inferred_from_rocket_or_legacy_site(self):
        item = self.draft['snapshot']['items'][0]
        item.update(rocket='Falcon 9', site_name='A known spaceport', site='A known spaceport')
        event = style.event_context(item, self.draft)
        self.assertEqual((event['pad_name'], event['pad_code']), ('', ''))
        script = r.local_script(self.draft, 'mock no API')
        text = ''.join(s['spoken'] for s in script)
        self.assertIn('A known spaceport', text)
        self.assertNotIn('SLC-', text)
        self.assertNotIn('工位缺失', text)

    def test_explicit_payload_counts_required_in_voice_not_hidden_in_name(self):
        from tests.test_roundup_materials import ll2_material
        item = ll2_material(self.draft['snapshot']['items'][0], 'A batch of 12 satellites for a constellation.')
        item['english_name'] = 'Fictional deployment'
        item['ll2_source']['identity']['english_name'] = item['english_name']
        self.draft['snapshot']['items'][0] = item
        self.assertEqual(style.payload_count(item)[0], 12)
        raw = base.styled_generated()
        good = r.validate_generated(raw, self.draft, context=style.style_context(self.draft, self.settings))
        self.assertIn('12颗', good[1]['spoken'])
        # A sourced event reference alone does not satisfy the required spoken quantity.
        good[1]['spoken'] = '美国 Rocket Lab 方面，Fictional deployment任务发射成功。'
        with self.assertRaisesRegex(AIResponseParseError, '载荷数量'):
            style.validate_style(good, style.style_context(self.draft, self.settings))
        for description, count in [('6 Chinese military remote sensing satellites of unknown purposes.', 6),
                                   ('The spacecraft is a technology demonstration satellite.', 1)]:
            self.assertEqual(style.payload_count(ll2_material(base.context(), description))[0], count)

    def test_partial_global_claim_rejected_before_accepting_json(self):
        self.draft['selection'][2]['included'] = False
        context = style.style_context(self.draft, self.settings)
        with self.assertRaisesRegex(AIResponseParseError, '全球'):
            style.validate_style([dict(spoken='全球同期共发射2次。')], context, single=True)

    def test_order_group_attribution_and_screen_codes_are_checked(self):
        raw = base.styled_generated()
        context = style.style_context(self.draft, self.settings)
        self.assertTrue(r.validate_generated(raw, self.draft, context=context))
        swapped = deepcopy(raw)
        swapped['segments'][1:3] = swapped['segments'][2:0:-1]
        with self.assertRaisesRegex(AIResponseParseError, '排序'):
            r.validate_generated(swapped, self.draft, context=context)
        bad = deepcopy(raw)
        bad['segments'][1]['spoken'] = '根据LL2，[[event:launch-1]]'
        with self.assertRaises(AIResponseParseError):
            r.validate_generated(bad, self.draft, context=context)
        self.draft['snapshot']['items'][0]['pad_name'] = 'Launch Complex 9A'
        raw['segments'][1]['screen'] = '[[name:launch-1]]'
        with self.assertRaisesRegex(AIResponseParseError, '屏幕'):
            r.validate_generated(raw, self.draft, context=style.style_context(self.draft, self.settings))

    def test_whole_and_single_requests_use_same_editable_rules_and_event_context(self):
        self.settings.roundup_prompt = '自定义口播规则：只写短句，保留必要限定。'
        self.settings.deepseek_api_key = 'mock-key-never-live'
        seen = []
        def respond(prompt, config, **kw):
            payload = json.loads(prompt.split('\n')[-1])
            self.assertEqual(payload['style']['rules'], self.settings.roundup_prompt)
            self.assertEqual(payload['style']['events'][0]['timezone'], '北京时间')
            self.assertNotIn(self.settings.deepseek_api_key, prompt)
            result = base.styled_generated()
            if payload['current']:
                result['segments'] = [result['segments'][1]]
            kw['validator'](result)
            seen.append(payload)
            return result
        before = deepcopy(self.draft['segments'])
        with patch.object(r, 'request_json', side_effect=respond):
            r.generate_script(self.draft, self.settings, self.token)
            r.generate_script(self.draft, self.settings, self.token, single_id='focus1')
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.draft['segments'], before)

    def test_polish_removes_routine_attribution_keeps_selected_ids_and_rules(self):
        self.settings.deepseek_api_key = 'mock-key-never-live'
        self.settings.roundup_prompt = '自定义选段与整稿共用规则'
        before = deepcopy(self.draft)
        calls = []
        def respond(prompt, config, **kw):
            payload = json.loads(prompt.split('\n')[-1]); calls.append(payload)
            self.assertEqual(payload['style']['rules'], self.settings.roundup_prompt)
            if kw['module_name'] == 'roundup_polish':
                result = dict(segments=[dict(id='focus1', task_ids=['launch-1'], text='美国 Rocket Lab 方面，10月21日晚上，Electron执行Fixture mission 1任务，发射成功。')])
            else:
                result = dict(checks=[dict(id='focus1', **{k:True for k in ('facts_preserved','numbers_preserved','results_preserved','purposes_preserved','uncertainty_preserved','no_new_conclusions')})])
            kw['validator'](result); return result
        # An existing paragraph has sources; originals and unselected edits remain untouched.
        self.draft['segments'] = r.validate_generated(base.styled_generated(), self.draft)
        before = deepcopy(self.draft)
        with patch.object(polish, 'request_json', side_effect=respond):
            result = polish.polish_segments(self.draft, ['focus1'], self.settings, self.token)
        polish.apply_polish(self.draft, result)
        self.assertEqual(self.draft['segments'][2:], before['segments'][2:])
        self.assertEqual(self.draft['segments'][1]['screen'], before['segments'][1]['screen'])
        self.assertNotIn('LL2', r.narration_text(self.draft['segments'][1]))
        self.assertEqual(len(calls), 2)

    def test_semantic_date_check_rejects_legal_json_and_does_not_touch_human_edits(self):
        self.settings.deepseek_api_key = 'mock-key-never-live'
        raw = base.styled_generated()
        raw['claims'] = [dict(id='bad_day', text='第二天，Electron发射Fixture mission 1。', task_ids=['launch-1'], fact_refs=['launch-1#event'])]
        raw['segments'][1]['spoken'] = '[[group:0]][[claim:bad_day]]'
        before = deepcopy(self.draft['segments'])
        with patch.object(r, 'request_json', side_effect=[raw, dict(checks=[dict(id='bad_day', supported=False, reason='本组首项未明确日期，第二天指代不成立')])]):
            generated = r.generate_script(self.draft, self.settings, self.token)
        self.assertEqual(generated[0]['origin'], 'local_template')
        self.assertIn('指代不成立', generated[0]['generation_reason'])
        self.assertEqual(self.draft['segments'], before)

    def test_old_preset_settings_and_draft_remain_compatible_and_exports_keep_sources(self):
        preset = prompt_manager._preset_from_dict(dict(name='old', summary='摘要', filter='筛选'))
        self.assertEqual(preset.roundup, style.DEFAULT_ROUNDUP_RULE)
        with patch.object(settings_module, 'CONFIG_PATH', self.root/'settings.json'):
            settings_module.save_settings(replace(self.settings, roundup_prompt='可编辑规则'))
            self.assertEqual(settings_module.load_settings().roundup_prompt, '可编辑规则')
        original = deepcopy(self.draft['segments'])
        path = self.root/'old-draft.json';r.save_draft(self.draft, path)
        loaded = r.load_draft(path)
        self.assertEqual(loaded['segments'], original)
        loaded['segments'] = r.validate_generated(base.styled_generated(), loaded)
        out = export_package(loaded, self.root, self.token, visuals=False)
        exported = json.loads((out/'reading_map.json').read_text(encoding='utf-8'))
        self.assertTrue(exported['segments'][1]['source_refs'])
        self.assertEqual(exported['segments'][1]['task_ids'], ['launch-1'])


if __name__ == '__main__':
    unittest.main()
