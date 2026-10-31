"""Fixed examples and mocked text/voice providers; never evidence of real sound quality."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from ai.json_utils import AIResponseParseError
from services import launch_roundup as r
from services import roundup_polish as p
from services.cancellation import TaskCancelledError
from services.roundup_audio import generate_audio, current_audio, reading_text
from services.roundup_export import export_package
from tests import test_launch_roundup as base
from tests.test_roundup_materials import ll2_material


FLAGS = ('facts_preserved','numbers_preserved','results_preserved','purposes_preserved',
         'uncertainty_preserved','no_new_conclusions')


class PolishTests(unittest.TestCase):
    setUp = base.RoundupTests.setUp

    def prepare(self):
        item = ll2_material(self.draft['snapshot']['items'][0], 'A batch of 3 satellites tests a radio receiver.')
        self.draft['snapshot']['items'][0] = item
        segment = self.draft['segments'][1]
        segment.update(spoken='LL2记录显示，该任务发射3颗卫星。LL2记录称，卫星测试无线电接收机，具体用途未说明。',
            screen='Fixture mission 1 · 3颗卫星', source_refs=[dict(r.background_facts(item)[0],task_id='launch-1')])
        self.settings.deepseek_api_key = 'mock-not-a-real-key'
        return segment

    def response(self, text='该任务发射3颗卫星。卫星测试无线电接收机，具体用途未说明。'):
        return dict(segments=[dict(id='focus1',task_ids=['launch-1'],text=text)])

    def review(self, **overrides):
        return dict(checks=[dict(id='focus1', **dict({f:True for f in FLAGS}, **overrides))])

    def transaction(self, text=None):
        response=self.response() if text is None else self.response(text)
        with patch.object(p, 'request_json', side_effect=[response,self.review()]):
            return p.polish_segments(self.draft,['focus1'],self.settings,self.token)

    def test_cache_and_model_pass_never_become_independent_or_human_confirmation(self):
        self.prepare()
        item=self.draft['snapshot']['items'][0]
        item['research']=dict(evidence_status='pending',human_status='unreviewed',reason='saved after failed search')
        state=r.material_status(item)
        self.assertTrue(state['supplemented'])
        self.assertEqual(state['source_label'],'LL2')
        self.assertFalse(state['independent_support'])
        self.assertEqual(state['human_confirmation'],'unconfirmed')
        result=self.transaction()
        p.apply_polish(self.draft,result)
        self.assertEqual(r.material_status(item),state)
        self.assertFalse(self.draft['segments'][1]['narration']['human_confirmed'])
        legacy=dict(item, ll2_source={}, research=dict(evidence_status='supported', model_review='passed'))
        legacy_state=r.material_status(legacy)
        self.assertFalse(legacy_state['supplemented'])
        self.assertFalse(legacy_state['independent_support'])
        self.assertEqual(legacy_state['human_confirmation'],'unconfirmed')

    def test_external_evidence_and_human_confirmation_are_separate(self):
        self.prepare()
        item=self.draft['snapshot']['items'][0]
        record=dict(version=1,fingerprint=r.fingerprint(item),evidence_status='supported',
            sources=[dict(source_id=0,url='https://www.nasa.gov/fixture',text='这是已匹配的公告背景原句。')],
            matches=[dict(source_id=0,summary='这是已匹配的公告背景原句。')])
        item['research']=record
        self.assertTrue(r.material_status(item)['independent_support'])
        self.assertEqual(r.material_status(item)['human_confirmation'],'unconfirmed')
        record.update(human_status='confirmed',human_fingerprint=r.fingerprint(item),manual=dict(chinese_name='人工名称'))
        self.assertEqual(r.material_status(item)['human_confirmation'],'current')
        record['human_fingerprint']='old'
        self.assertEqual(r.material_status(item)['human_confirmation'],'stale')

    def test_selected_transaction_preserves_all_facts_screens_ids_and_other_human_edits(self):
        self.prepare()
        self.draft['segments'][2].update(edited=True,spoken='其他段人工编辑，不能覆盖。')
        before=deepcopy(self.draft)
        result=self.transaction()
        self.assertEqual(self.draft,before)
        p.apply_polish(self.draft,result)
        self.assertEqual(self.draft['snapshot'],before['snapshot'])
        self.assertEqual(self.draft['segments'][2:],before['segments'][2:])
        segment=self.draft['segments'][1]
        for key in ('spoken','screen','task_ids','source_refs'):
            self.assertEqual(segment[key],before['segments'][1][key])
        self.assertNotEqual(r.narration_text(segment),segment['spoken'])
        self.assertEqual(self.draft['polish_history'][0]['updates'][0]['before'],before['segments'][1])

    def test_actual_request_boundary_has_selected_content_and_separate_fact_review(self):
        self.prepare()
        modules=[]
        def respond(prompt,config,**kw):
            self.assertNotIn(self.settings.deepseek_api_key,prompt)
            modules.append(kw['module_name'])
            if kw['module_name']=='roundup_polish':
                payload=json.loads(prompt.split('\n')[-1])
                self.assertEqual([s['id'] for s in payload['segments']],['focus1'])
                self.assertIn('sources',payload['segments'][0])
                data=self.response()
            else:
                data=self.review()
            kw['validator'](data)
            return data
        with patch.object(p,'request_json',side_effect=respond):
            self.assertEqual(len(p.polish_segments(self.draft,['focus1'],self.settings,self.token)['updates']),1)
        self.assertEqual(modules,['roundup_polish','roundup_polish_review'])

    def test_wrong_ids_numbers_lost_quantity_and_uncertainty_are_rejected(self):
        self.prepare()
        for text in ('LL2记录发射5颗卫星，具体用途未知。','LL2记录介绍卫星，具体用途未知。',
                     'LL2记录发射3颗卫星，卫星用于通信。',
                     'LL2官方已确认发射3颗卫星，用途未知。','LL2首次发射3颗卫星，用途未知。'):
            with patch.object(p,'request_json',return_value=self.response(text)):
                with self.subTest(text=text),self.assertRaises(AIResponseParseError):
                    p.polish_segments(self.draft,['focus1'],self.settings,self.token)
        bad=self.response()
        bad['segments'][0]['id']='focus2'
        with patch.object(p,'request_json',return_value=bad),self.assertRaises(AIResponseParseError):
            p.polish_segments(self.draft,['focus1'],self.settings,self.token)
        self.draft['segments'][1]['spoken'] += '这些卫星被列为军事卫星。'
        with patch.object(p,'request_json',side_effect=[self.response(), self.review(facts_preserved=False)]),self.assertRaises(AIResponseParseError):
            p.polish_segments(self.draft,['focus1'],self.settings,self.token)

    def test_semantically_changed_purpose_or_result_does_not_pass_valid_json(self):
        self.prepare()
        before=deepcopy(self.draft)
        for flag in ('results_preserved','purposes_preserved','no_new_conclusions'):
            with patch.object(p,'request_json',side_effect=[self.response(),self.review(**{flag:False})]):
                with self.subTest(flag=flag),self.assertRaises(AIResponseParseError):
                    p.polish_segments(self.draft,['focus1'],self.settings,self.token)
        self.assertEqual(self.draft,before)

    def test_failures_missing_configuration_and_cancel_keep_all_original_data(self):
        self.prepare()
        before=deepcopy(self.draft)
        for error in (OSError('mock offline'),TaskCancelledError('mock cancelled')):
            with patch.object(p,'request_json',side_effect=error),self.assertRaises(type(error)):
                p.polish_segments(self.draft,['focus1'],self.settings,self.token)
            self.assertEqual(self.draft,before)
        self.settings.deepseek_api_key=''
        with patch.object(p,'request_json') as call,self.assertRaises(ValueError):
            p.polish_segments(self.draft,['focus1'],self.settings,self.token)
        call.assert_not_called()

    def test_conflicts_cannot_be_silently_removed(self):
        segment=self.prepare()
        item=self.draft['snapshot']['items'][0]
        item['research']=dict(fingerprint=r.fingerprint(item),evidence_status='conflict')
        with patch.object(p,'request_json',side_effect=[self.response(),self.review(uncertainty_preserved=False)]):
            with self.assertRaises(AIResponseParseError):
                p.polish_segments(self.draft,['focus1'],self.settings,self.token)
        segment['spoken']='LL2记录中的3颗卫星用途存在分歧，尚待核对。'
        with patch.object(p,'request_json',side_effect=[self.response('3颗卫星用途未说明，尚待核对。'),self.review(uncertainty_preserved=False)]):
            with self.assertRaises(AIResponseParseError):
                p.polish_segments(self.draft,['focus1'],self.settings,self.token)

    def test_changed_human_edit_prevents_entire_apply_transaction(self):
        self.prepare()
        result=self.transaction()
        self.draft['segments'][1]['screen']='润色期间的人工屏幕修改'
        before=deepcopy(self.draft)
        with self.assertRaisesRegex(ValueError,'未覆盖'):
            p.apply_polish(self.draft,result)
        self.assertEqual(self.draft,before)

    def test_audio_uses_current_narration_but_keeps_completed_files_and_stales_after_fact_edit(self):
        segment=self.prepare()
        generate_audio(self.draft,self.provider,self.token,segment_id='focus1')
        old=deepcopy(segment['audio'])
        p.apply_polish(self.draft,self.transaction())
        self.assertIsNone(current_audio(self.draft,segment))
        self.assertTrue(Path(old['path']).exists())
        generate_audio(self.draft,self.provider,self.token,segment_id='focus1')
        request=self.provider.synthesize.call_args.args[0]
        self.assertEqual(request.text,r.narration_text(segment))
        self.assertEqual(segment['audio']['reading_text'],request.text)
        segment['spoken']+='事实原稿已人工修改。'
        self.assertIsNone(r.current_narration(segment))
        self.assertEqual(reading_text(self.draft,segment),segment['spoken'])
        self.assertIsNone(current_audio(self.draft,segment))

    def test_pronunciations_only_change_final_input_and_export_mapping_matches_srt(self):
        segment=self.prepare()
        p.apply_polish(self.draft,self.transaction())
        canonical,screen=segment['spoken'],segment['screen']
        self.draft['pronunciations']={'LL2':'L L 二','3颗':'三颗'}
        self.assertIn('三颗',reading_text(self.draft,segment))
        self.assertEqual(segment['spoken'],canonical)
        self.assertEqual(segment['screen'],screen)
        generate_audio(self.draft,self.provider,self.token)
        directory=export_package(self.draft,self.root,self.token,visuals=False)
        mapping=json.loads((directory/'reading_map.json').read_text(encoding='utf-8'))
        row=mapping['segments'][1]
        self.assertEqual(row['reading_text'],reading_text(self.draft,segment))
        self.assertEqual(row['screen_text'],screen)
        self.assertEqual(row['formal_names'][0]['original_name'],'Fixture mission 1')
        self.assertEqual(mapping['alignment_status'],'not_performed')
        self.assertTrue((directory/'polish_history.json').exists())
        self.assertIn(r.narration_text(segment),(directory/'paragraphs.srt').read_text(encoding='utf-8'))
        self.assertEqual(r.load_draft(directory/'draft.json')['polish_history'],self.draft['polish_history'])

    def test_old_snapshot_and_no_audio_export_keep_original_text_and_unmeasured_status(self):
        self.assertEqual(r.narration_text(self.draft['segments'][1]),self.draft['segments'][1]['spoken'])
        directory=export_package(self.draft,self.root,self.token,visuals=False)
        mapping=json.loads((directory/'reading_map.json').read_text(encoding='utf-8'))
        self.assertFalse(mapping['measured_audio'])
        self.assertFalse((directory/'paragraphs.srt').exists())
        self.assertIn('未测量',(directory/'reading_script.md').read_text(encoding='utf-8'))
        states=json.loads((directory/'material_states.json').read_text(encoding='utf-8'))
        self.assertFalse(states['launch-1']['independent_support'])

    def test_ui_keeps_fact_screen_reading_fields_and_selected_scope_separate(self):
        self.prepare()
        from PySide6.QtWidgets import QApplication,QPushButton,QAbstractItemView
        from ui.launch_roundup_dialog import LaunchRoundupDialog
        app=QApplication.instance() or QApplication([])
        with patch('ui.launch_roundup_dialog.load_existing',return_value=self.collection):
            dialog=LaunchRoundupDialog(self.settings)
        self.addCleanup(dialog.close)
        dialog.draft=deepcopy(self.draft)
        dialog._show_draft()
        dialog.segment_list.setCurrentRow(1)
        before=deepcopy(dialog.draft['segments'][1])
        dialog.narration_edit.setPlainText('LL2记录中的3颗卫星，具体用途未说明。')
        self.assertEqual(dialog.draft['segments'][1]['spoken'],before['spoken'])
        self.assertEqual(dialog.draft['segments'][1]['screen'],before['screen'])
        self.assertFalse(dialog.draft['segments'][1]['narration']['human_confirmed'])
        self.assertEqual(dialog.draft['segments'][1]['narration']['source_refs'][0]['support_review'],'manual_edit_unreviewed')
        self.assertEqual(dialog.tasks.columnCount(),12)
        self.assertEqual(dialog.segment_list.selectionMode(),QAbstractItemView.SelectionMode.ExtendedSelection)
        self.assertIn('润色选中段口播',[b.text() for b in dialog.findChildren(QPushButton)])


if __name__=='__main__':
    unittest.main()
