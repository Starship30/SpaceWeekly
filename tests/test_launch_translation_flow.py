"""Translation flow uses fixed LL2 data and mocked AI; no live credentials required."""
from copy import deepcopy
from dataclasses import replace
import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import resources
from services import launch_research as research,launch_roundup as roundup
from services.launch_display import resolve_launch
from services.settings import default_settings
from services.cancellation import CancellationToken,TaskCancelledError
from translator.cache import cache_translation,load_translation_cache
from translator.dictionary import load_dictionary,save_dictionary
from feeds.launches import launch_context,_launch_to_article,_launch_video_item
from exporters.launches import launch_fields
from tests.test_launch_research import RAW


class LaunchTranslationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        p=patch.object(resources,'_WORKSPACE_PATH',self.root);p.start();self.addCleanup(p.stop)
        self.settings=replace(default_settings(),deepseek_api_key='fixture-only',deepseek_model='mock-model',
          web_search_enabled=False,aerospace_translation_enabled=True,aerospace_translation_mode='hybrid',
          output_dir=str(self.root/'output'))
        raw=deepcopy(RAW);raw['rocket']['configuration']['full_name']='Fixture-4B 01'
        raw['mission']['name']='Unverified Payload-4 01'
        raw['mission']['description']='The spacecraft may test 2 radio receivers.'
        self.raw=raw;self.item=launch_context(raw);self.token=CancellationToken()
        self.client=Mock(side_effect=self.answer)
        p=patch.object(research,'request_json',self.client);p.start();self.addCleanup(p.stop)

    def answer(self,prompt,config,**options):
        data=json.loads(prompt.split('\n')[-1]);self.assertEqual(data['ll2_id'],self.item['ll2_id'])
        self.assertIn('不可信引用数据',prompt)
        self.assertEqual((options['max_attempts'],config.retry_count),(1,0))
        values={k:('航天器可能测试2个无线电接收机。' if k=='description' else '测试'+v['original']) for k,v in data['fields'].items()}
        return dict(translations=values)

    def test_search_disabled_still_calls_ai_and_keeps_formal_name_original(self):
        before=deepcopy(self.item);signature=research.fingerprint(self.item)
        research.prepare_launch_translation(self.item,self.settings,self.token)
        self.client.assert_called_once();view=resolve_launch(self.item)
        self.assertEqual(view['rocket'],'测试Fixture-4B 01')
        self.assertEqual(view['name'],'测试'+before['english_name'])
        self.assertEqual(view['name_source'],'任务名参考译文（非正式名称）')
        self.assertIn('可能',view['description_translation'])
        self.assertEqual(view['field_sources']['rocket'],'AI参考译文（未核对）')
        self.assertEqual(research.fingerprint(self.item),signature)
        self.assertEqual(research.confirmed_values(self.item.get('research',{}),self.item),{})
        self.assertNotIn(self.item['english_name'],load_dictionary())
        draft=roundup.create_draft(dict(items=[self.item],complete=False),'2026-10-10','2026-10-11')
        named=roundup.task_names(draft,[self.item['ll2_id']])[0]
        self.assertEqual(named['confirmed_chinese_name'],'');self.assertEqual(named['display_name'],view['name'])

    def test_reuses_valid_results_in_fresh_snapshot_and_force_retries_one_task(self):
        research.prepare_launch_translation(self.item,self.settings,self.token)
        self.client.reset_mock();fresh=launch_context(self.raw)
        research.prepare_launch_translation(fresh,self.settings,self.token);self.client.assert_not_called()
        draft=roundup.create_draft(dict(items=[fresh],complete=False),'2026-10-10','2026-10-11')
        draft['segments']=[dict(id='manual',kind='highlight',spoken='人工文字保留',screen='人工屏幕',task_ids=[fresh['ll2_id']],source_refs=[],edited=True,audio=None)]
        before=deepcopy(draft['segments'])
        roundup.prepare_translations(draft,self.settings,self.token,recheck_id=fresh['ll2_id'])
        self.client.assert_called_once();self.assertEqual(draft['segments'],before)

    def test_combined_completion_reviews_sources_before_translating_and_keeps_manual_settings(self):
        draft=roundup.create_draft(dict(items=[deepcopy(self.item)],complete=False),'2026-10-10','2026-10-11')
        calls=[];service=Mock()
        def reviewed(item,**kw):
            calls.append('source')
            return dict(version=1,fingerprint=research.fingerprint(item),evidence_status='supported',chinese_name='来源中的正式测试名',
                        sources=[{'url':'https://www.nasa.gov/fixture'}],matches=[{'source_id':0}])
        service.review.side_effect=reviewed
        def translated(prompt,config,**kw):
            calls.append('translation');self.assertNotIn('english_name',json.loads(prompt.split('\n')[-1])['fields'])
            return self.answer(prompt,config,**kw)
        self.client.side_effect=translated
        disabled=replace(self.settings,web_search_enabled=False,aerospace_translation_enabled=False,aerospace_translation_mode='dictionary')
        roundup.prepare_materials(draft,disabled,self.token,service=service,hydrate=lambda i,*_:i)
        self.assertEqual(calls,['source','translation'])
        self.assertEqual(resolve_launch(draft['snapshot']['items'][0])['name'],'来源中的正式测试名')
        self.assertFalse(disabled.aerospace_translation_enabled);self.assertFalse(disabled.web_search_enabled)

    def test_legacy_guess_is_retried_and_confirmed_terms_and_explicit_empty_win(self):
        path=self.root/'translator/translation_cache.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'Fixture-4B 01':dict(translation='旧错误译文',source='llm',confidence=.9)}),encoding='utf-8')
        research.prepare_launch_translation(self.item,self.settings,self.token)
        self.assertEqual(resolve_launch(self.item)['rocket'],'测试Fixture-4B 01')
        terms=load_dictionary();terms['Fixture-4B 01']=dict(translation='人工确认名称',human_status='confirmed');save_dictionary(terms)
        self.assertEqual(resolve_launch(self.item)['rocket'],'人工确认名称')
        self.item.update(edition_version=2,edition_fields={'rocket':''})
        self.assertEqual(resolve_launch(self.item)['rocket'],'')

    def test_bad_number_model_and_decimal_suffix_leave_only_failed_fields_original(self):
        for original,wrong in [('Fixture-4B 01','测试4B'),('Soyuz 2.1b Fregat-M','联盟2.1 Fregat-M')]:
            self.item['rocket']=original
            def bad(prompt,*_,**kw):
                values=self.answer(prompt,_[0],**kw);values['translations']['rocket']=wrong;return values
            self.client.side_effect=bad
            research.prepare_launch_translation(self.item,self.settings,self.token,force=True)
            self.assertEqual(self.item['field_translations']['rocket']['status'],'failed')
            self.assertNotEqual(resolve_launch(self.item)['rocket'],wrong)
            self.assertIn('可能',resolve_launch(self.item)['description_translation'])

    def test_missing_api_failure_and_cancellation_are_not_success(self):
        research.prepare_launch_translation(self.item,replace(self.settings,deepseek_api_key=''),self.token)
        self.client.assert_not_called();self.assertEqual(self.item['field_translations']['rocket']['status'],'failed')
        self.client.side_effect=OSError('fixture offline')
        research.prepare_launch_translation(self.item,self.settings,self.token)
        self.assertIn('offline',self.item['field_translations']['rocket']['error'])
        self.client.side_effect=TaskCancelledError('fixture cancel')
        with self.assertRaises(TaskCancelledError):research.prepare_launch_translation(self.item,self.settings,self.token)

    def test_human_cache_cannot_be_silently_overwritten(self):
        path=self.root/'translator/translation_cache.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'Fixture-4B 01':dict(translation='人工译名',status='human_confirmed')}),encoding='utf-8')
        cache_translation('Fixture-4B 01','自动译名',term_type='rocket',source='ai_field',source_url='',confidence=0)
        self.assertEqual(load_translation_cache()['Fixture-4B 01']['translation'],'人工译名')
        research.prepare_launch_translation(self.item,self.settings,self.token,force=True)
        self.assertEqual(resolve_launch(self.item)['rocket'],'人工译名')
        self.item['field_translations']['rocket']=dict(original='Fixture-4B 01',translation='测试Fixture-4B 01',type='rocket',status='candidate',validation_version=1)
        self.assertEqual(resolve_launch(self.item)['rocket'],'人工译名')
        self.assertEqual(resolve_launch(self.item)['field_sources']['rocket'],'人工确认的术语缓存')

    def test_standard_groups_and_codes_have_chinese_labels_without_inventing_payload_names(self):
        for original,expected in [('Starlink Group 7-31 (Starship Flight 22)','星链第7-31组（星舰第22次飞行）'),
                                  ('SatNet LEO Group 45','卫星互联网低轨第45组'),('USSF-999','USSF-999任务')]:
            item=deepcopy(self.item);item['english_name']=original
            research.prepare_launch_translation(item,self.settings,self.token)
            self.assertEqual(resolve_launch(item)['name'],expected)
            self.assertEqual(research.confirmed_values(item.get('research',{}),item),{})

    def test_model_cannot_drop_description_uncertainty_or_change_returned_field_ids(self):
        def definite(prompt,config,**kw):
            data=self.answer(prompt,config,**kw);data['translations']['description']='航天器测试2个无线电接收机。';return data
        self.client.side_effect=definite
        research.prepare_launch_translation(self.item,self.settings,self.token)
        self.assertEqual(self.item['field_translations']['description']['status'],'failed')
        self.assertEqual(resolve_launch(self.item)['description_translation'],'')
        self.client.side_effect=lambda *_args,**_kw:dict(translations={'english_name':'猜测名称'})
        research.prepare_launch_translation(self.item,self.settings,self.token,force=True)
        self.assertEqual(self.item['field_translations']['rocket']['status'],'failed')
        self.assertNotEqual(resolve_launch(self.item)['name'],'猜测名称')
        self.assertEqual(research.confirmed_values(self.item.get('research',{}),self.item),{})

    def test_weekly_preparation_shares_translation_and_exports_are_offline(self):
        from services.weekly_report import _prepare_launch_research,_localize_launch_video_items
        article=_launch_to_article(self.raw);video=_launch_video_item(self.raw,'Asia/Shanghai');before=deepcopy(video)
        articles,videos=_prepare_launch_research([article],[video],self.settings,self.token,None)
        self.client.assert_called_once();self.assertEqual(video,before)
        with patch.object(research,'request_json',side_effect=AssertionError('offline export')):
            fields=launch_fields(articles[0]);result=_localize_launch_video_items(videos,None,self.token)[0]
        self.assertEqual(fields['rocket'],'测试Fixture-4B 01');self.assertEqual(result['title'],fields['rocket'])
        self.assertIn('AI，未独立核对',fields['mission_description'])
        self.assertEqual(result['summary'],'测试'+self.item['english_name'])

    def test_refresh_and_explicit_translation_apply_results_without_touching_paragraphs(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QDate
        from ui.launch_roundup_dialog import LaunchRoundupDialog
        app=QApplication.instance() or QApplication([]);widget=LaunchRoundupDialog(self.settings)
        self.addCleanup(widget.deleteLater)
        widget._loading=True
        widget.start.setDate(QDate(2026,10,10));widget.end.setDate(QDate(2026,10,11));widget._loading=False
        collection=dict(items=[deepcopy(self.item)],start='2026-10-10',end='2026-10-11',complete=False,reason='fixture',pages=1)
        with patch('ui.launch_roundup_dialog.refresh_collection',return_value=collection):
            widget._refresh()
            self.wait(app,widget)
        self.assertIn('field_translations',widget.draft['snapshot']['items'][0]);self.client.assert_called_once()
        widget.draft['segments']=[dict(id='manual',kind='highlight',spoken='人工文字',screen='人工屏幕',task_ids=[self.item['ll2_id']],source_refs=[],edited=True,audio=None)]
        widget._show_draft();widget.tasks.setCurrentCell(0,2);before=deepcopy(widget.draft['segments'])
        widget._translate_fields(True);self.wait(app,widget)
        self.assertEqual(widget.draft['segments'],before);self.assertIsNone(widget.tts_provider.process)

    def wait(self,app,widget):
        deadline=time.monotonic()+5
        while widget.worker is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        self.assertIsNone(widget.worker)

if __name__=='__main__':unittest.main()
