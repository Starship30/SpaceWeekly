"""Reading context and event-only editing; fixtures are not live mission validation."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from services import launch_roundup as r, roundup_style as style, roundup_polish as polish
from services.roundup_audio import speech_text, reading_text, current_audio, generate_audio
from services.roundup_sync import reading_units, set_units, segment_schedule
from tests import test_launch_roundup as base
from tests.test_roundup_materials import ll2_material


class ReadingTests(unittest.TestCase):
    setUp = base.RoundupTests.setUp

    def prepare(self):
        a = dict(base.context(1, net='2026-10-23T10:15:00Z'), english_name='Test 50-03',
                 rocket='Test rocket A', provider='CASC', site_name='Test site A', pad_name='Launch Complex 9A')
        b = dict(base.context(2, net='2026-10-23T19:25:00Z'), english_name='Test 53-07 to 09/56-19 to 21',
                 rocket='Test rocket B', provider='CASC', site_name='Test site B', pad_name='Launch Area 94 (SLS-2 / 603)')
        a = ll2_material(a, 'The spacecraft is a remote sensing satellite.')
        b = ll2_material(b, '6 remote sensing satellites of unknown purposes.')
        for item,amount in ((a,1),(b,6)):
            item['payload']=item['description'];item['payloads']=[dict(name=item['english_name'],amount=amount)]
        entries = {'Test 50-03':dict(translation='测试五十号03星'),
                   'Test 53-07 to 09/56-19 to 21':dict(translation='测试五十三号07—09星、测试五十六号19—21星'),
                   'Test site A':dict(translation='测试甲发射中心'), 'Test site B':dict(translation='测试乙发射中心')}
        mock = patch('services.launch_display.load_dictionary', return_value=entries)
        mock.start();self.addCleanup(mock.stop)
        self.draft = r.create_draft(dict(self.collection, items=[a,b]), '2026-10-23', '2026-10-29')
        self.draft['pronunciations'] = {'LC-9A':'九 A '}
        self.draft['tts']['model'] = 'fixture-model'
        self.draft['segments'] = r.local_script(self.draft, 'fixture')
        return self.draft['segments'][1]

    def test_dates_identifiers_pads_ranges_have_distinct_rules(self):
        text = '10月23日，测试五十号03星，测试号07—09星，94号工位，9A工位。'
        result = speech_text(text,{})
        self.assertEqual(result,'十月二十三日，测试五十号零三星，测试号零七至零九星，九十四号工位，九 A 工位。')
        self.assertEqual(speech_text('2026年10月23日',{}),'二零二六年十月二十三日')
        untouched = '猎鹰9号 Block 5，Long March 6A，v1.03，0.6B，1.5吨，A-03，2026-10-23，2月30日（历史别名）'
        self.assertEqual(speech_text(untouched,{}),untouched.replace('1.5吨','一点五吨'))

    def test_news_year_time_quantity_and_percentage_are_contextual(self):
        self.assertEqual(speech_text('2022年，10月29日10:05，800克，41亿年，增长3.5%，0.6B，v1.03，Falcon 9 Block 5。',{}),
            '二零二二年，十月二十九日十点零五分，八百克，四十一亿年，增长百分之三点五，0.6B，v1.03，Falcon 9 Block 5。')

    def test_user_readings_win_once_and_non_narrative_input_is_rejected(self):
        self.assertEqual(speech_text('03星 10月23日 ABC ABCD', {'03星':'自定读法','ABCD':'AB','AB':'C'}),
                         '自定读法 十月二十三日 CC AB')
        for text in ['[[task:secret]]','来源：https://example.invalid','<speak>句子</speak>', '任务ID：secret']:
            with self.assertRaises(ValueError):speech_text(text,{})

    def test_natural_group_keeps_formal_screen_and_explicit_ids(self):
        segment = self.prepare()
        text = segment['spoken']
        self.assertIn('中国方面，10月23日傍晚',text)
        self.assertIn('第二天凌晨',text)
        self.assertIn('测试五十三号和测试五十六号的两组卫星',text)
        self.assertIn('一箭六星',text)
        self.assertEqual(text.count('成功'),1)
        for unwanted in ('星卫星','一颗卫星','SLS-2','603','Launch Area'):
            self.assertNotIn(unwanted,text)
        self.assertIn('07—09星',segment['screen'])
        self.assertIn('94 工位',segment['screen'])
        self.assertIn('SLS-2',self.draft['snapshot']['items'][1]['pad_name'])
        units = reading_units(self.draft,segment)
        self.assertEqual(''.join(u['reading_text'] for u in units),reading_text(self.draft,segment))
        self.assertEqual(units[0]['task_ids'],['launch-1','launch-2'])
        self.assertEqual(units[-1]['task_ids'],['launch-1','launch-2'])
        self.assertIn('九十四号工位',reading_text(self.draft,segment))

    def test_ambiguous_simplification_and_unsupported_payload_stay_conservative(self):
        self.prepare()
        b = self.draft['snapshot']['items'][1]
        original = deepcopy(b)
        extra = dict(base.context(3), english_name='测试五十三号10星')
        self.draft['snapshot']['items'].append(extra)
        self.draft['selection'].append(dict(id='launch-3',included=True,featured=False,group='',assets=[]))
        event = style.event_context(b,self.draft)
        self.assertIn('07—09星',event['spoken_payload'])
        self.assertEqual(b,original)
        b['payloads']=[];b['payload'] = 'Unlinked satellite assertion'
        self.assertEqual(style.event_context(b,self.draft)['spoken_payload'],'')

    def test_name_refresh_keeps_existing_unit_ids_and_task_associations(self):
        segment=self.prepare()
        before=[(u['id'],u['task_ids']) for u in segment['units']]
        with patch.object(r,'load_dictionary',return_value={'Test rocket A':dict(translation='测试甲号火箭')}):
            r.sync_names(self.draft)
        self.assertIn('测试甲号火箭',segment['spoken'])
        units=reading_units(self.draft,segment)
        self.assertEqual([(u['id'],u['task_ids']) for u in units],before)

    def test_proposal_never_overwrites_manual_text_until_applied_and_invalidates_clock(self):
        segment = self.prepare()
        segment['spoken'] = '人工原稿，测试五十号03星卫星，发射一颗卫星。'
        segment['edited'] = True
        set_units(segment,[dict(text=segment['spoken'],task_ids=['launch-1','launch-2'],display='summary')])
        generate_audio(self.draft,self.provider,self.token)
        self.assertIsNotNone(current_audio(self.draft,segment))
        segment['alignment'] = dict(key='old',tokens=[])
        segment['sync_manual'] = dict(basis='old',spans=[])
        before = deepcopy(self.draft)
        proposal = polish.organize_segments(self.draft,[segment['id']])
        self.assertEqual(self.draft,before)
        polish.apply_polish(self.draft,proposal)
        for key in ('spoken','screen','task_ids','source_refs','audio','alignment','sync_manual'):
            self.assertEqual(segment[key],before['segments'][1][key])
        self.assertIsNone(current_audio(self.draft,segment))
        self.assertTrue(segment_schedule(self.draft,segment)['manual_review_required'])
        self.assertNotIn('星卫星',r.narration_text(segment))
        self.assertEqual(self.draft['snapshot'],before['snapshot'])
        self.assertEqual(self.draft['polish_history'][-1]['updates'][0]['before'],before['segments'][1])
        with self.assertRaises(ValueError):polish.apply_polish(self.draft,proposal)

    def test_cross_company_dates_reset_and_single_rewrite_uses_same_rule(self):
        self.prepare()
        self.draft['snapshot']['items'][1]['provider'] = 'SpaceX'
        parts = style.narration_parts(self.draft,['launch-1','launch-2'])
        text = ''.join(p['text'] for p in parts)
        self.assertIn('美国 SpaceX 方面，10月24日凌晨',text)
        self.assertNotIn('第二天',text)
        segment = self.draft['segments'][1]
        rewritten = r.local_script(self.draft,'fixture',single_id=segment['id'])[0]
        self.assertEqual(rewritten['spoken'],text)

    def test_polish_multi_task_units_cannot_be_guessed_or_reassigned(self):
        for row in [dict(text='甲。乙。'),dict(text='甲。乙。',units=[dict(text='甲。乙。',task_ids=['launch-1'],display='task')]),
                    dict(text='甲。',units=[dict(text=3,task_ids=[],display='transition')])]:
            with self.assertRaises(ValueError):polish._polish_units(row,['launch-1','launch-2'])
        parts = [dict(text='甲。',task_ids=['launch-1'],display='task'),dict(text='乙。',task_ids=['launch-1','launch-2'],display='summary')]
        self.assertEqual(polish._polish_units(dict(text='甲。乙。',units=parts),['launch-1','launch-2']),parts)

    def test_reading_rule_only_invalidates_affected_audio_and_legacy_keys_upgrade(self):
        from services.roundup_audio import audio_key
        self.prepare()
        generate_audio(self.draft,self.provider,self.token)
        segment=self.draft['segments'][1]
        segment['audio']['key']=audio_key(self.draft,segment,legacy_rules=True)
        calls=self.provider.synthesize.call_count
        generate_audio(self.draft,self.provider,self.token)
        self.assertEqual(self.provider.synthesize.call_count,calls)
        self.assertEqual(segment['audio']['key'],audio_key(self.draft,segment))
        self.draft['pronunciations']['unused brand']='无关读法'
        self.assertTrue(all(current_audio(self.draft,s) for s in self.draft['segments']))
        self.draft['pronunciations']['Test rocket A']='自定型号读法'
        self.assertIsNone(current_audio(self.draft,segment))
        self.assertIsNotNone(current_audio(self.draft,self.draft['segments'][0]))
        self.assertIsNotNone(current_audio(self.draft,self.draft['segments'][-1]))

    def test_orbit_is_sourced_target_shared_once_and_keeps_association(self):
        segment=self.prepare()
        for item in self.draft['snapshot']['items']:item['orbit']='近地轨道'
        original=deepcopy(self.draft)
        event=style.event_context(self.draft['snapshot']['items'][0],self.draft)
        self.assertEqual((event['orbit'],event['orbit_basis']),('近地轨道','mission_target'))
        proposed=polish.organize_segments(self.draft,[segment['id']])
        self.assertEqual(self.draft,original)
        after=proposed['updates'][0]['after'];text=r.narration_text(after)
        self.assertEqual(text.count('近地轨道'),1)
        self.assertIn('任务目标都是近地轨道',text)
        summary=next(u for u in reading_units(self.draft,after) if '近地轨道' in u['text'])
        self.assertEqual((summary['display'],summary['task_ids']),('summary',['launch-1','launch-2']))
        self.assertNotIn('送入',text);self.assertNotIn('太阳同步',text)
        self.assertIn('目标轨道：近地轨道',r.local_script(self.draft,'fixture')[1]['screen'])

    def test_missing_or_different_orbits_are_never_generalized_to_other_tasks(self):
        self.prepare();a,b=self.draft['snapshot']['items']
        a['orbit']='近地轨道';b['orbit']='Unknown'
        parts=style.narration_parts(self.draft,['launch-1','launch-2'])
        stated=[p for p in parts if '近地轨道' in p['text']]
        self.assertEqual([p['task_ids'] for p in stated],[['launch-1']])
        self.assertEqual(style.event_context(b,self.draft)['orbit'],'')
        b['orbit']='太阳同步轨道';b['status_id']=4
        parts=style.narration_parts(self.draft,['launch-1','launch-2'])
        stated=next(p for p in parts if '太阳同步轨道' in p['text'])
        self.assertEqual(stated['task_ids'],['launch-2'])
        self.assertIn('目标是',stated['text']);self.assertIn('失败',stated['text']);self.assertNotIn('入轨',stated['text'])


if __name__ == '__main__':unittest.main()
