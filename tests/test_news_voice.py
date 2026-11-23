"""News adapter contracts with fixture PCM/alignment; real model acceptance is separate."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest,wave
import json
from dataclasses import replace
from unittest.mock import Mock,patch

import resources
from ai.json_utils import AIResponseParseError,AIResponseValidationError,AITruncatedResponseError,request_json
from models.article import Article
from models.news import News,news_id
from services import news_voice as news,roundup_sync as sync,launch_roundup as launch
from services.roundup_audio import audio_key,current_audio,reading_text,generate_audio
from services.cancellation import CancellationToken,TaskCancelledError
from services.settings import default_settings


class NewsVoiceTests(unittest.TestCase):
    def setUp(self):
        Path('build').mkdir(exist_ok=True);self.temp=tempfile.TemporaryDirectory(prefix='新闻 配音 ',dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.paths=patch.object(resources,'_WORKSPACE_PATH',self.root);self.paths.start();self.addCleanup(self.paths.stop)
        self.article=Article(News('NASA','火星陨石研究','2026-10-29','这是屏幕摘要，不应自动朗读。','https://www.nasa.gov/fixture'),'2022年发现陨石。质量约800克。','fixture')
        self.draft=news.create(self.article,dict(tts=dict(python='',model='fixture-not-real-weights',speaker='vivian',style='',device='cpu',timeout=60),pronunciations={}))
        self.token=CancellationToken();self.provider=Mock();self.provider.synthesize.side_effect=self.pcm

    def pcm(self,request,path,cancel_check):
        cancel_check()
        with wave.open(str(path),'wb') as w:w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\x01\x00'*96000)

    def spoken(self):
        news.set_spoken(self.draft,'2022年发现陨石。质量约800克。');news.approve(self.draft)

    def test_summary_is_not_a_narration_and_typed_reading_keeps_source_unchanged(self):
        original=deepcopy(self.article)
        self.assertEqual(self.draft['segments'][0]['spoken'],'')
        with self.assertRaises(ValueError):news.approve(self.draft)
        self.spoken();self.assertEqual(reading_text(self.draft,self.draft['segments'][0]),'二零二二年发现陨石。质量约八百克。')
        self.assertEqual(self.article,original);self.assertEqual(self.draft['news_id'],news_id(self.article.news.url))
        self.assertNotIn('这是屏幕摘要',reading_text(self.draft,self.draft['segments'][0]))

    def test_namespace_cache_screen_edit_reading_edit_and_history(self):
        self.spoken();segment=self.draft['segments'][0]
        news.synthesize(self.draft,self.provider,None,self.token);first=deepcopy(segment['audio'])
        news.synthesize(self.draft,self.provider,None,self.token);self.assertEqual(self.provider.synthesize.call_count,1)
        self.draft['screen_item']['title']='只改画面标题';self.assertEqual(current_audio(self.draft,segment)['key'],first['key'])
        self.assertIn('news_audio',first['path']);self.assertTrue((self.root/'cache/news-drafts'/f"{self.draft['id']}.json").is_file())
        from PIL import Image
        from services.roundup_audio import file_hash
        picture=self.root/'本地 配图.png';Image.new('RGB',(96,64),'red').save(picture)
        self.draft['screen_item']['image']=dict(path=str(picture),sha256=file_hash(picture))
        self.assertIsNotNone(current_audio(self.draft,segment))
        with patch('requests.get',side_effect=AssertionError('Offline preview must stay local')):
            self.assertEqual(news.frame_at(news.prepare_frames(self.draft),3).getpixel((960,540)),(255,0,0,255))
        launch_view=dict(self.draft,content_type='roundup')
        self.assertNotEqual(audio_key(launch_view,segment),first['key'])
        news.set_spoken(self.draft,'2022年发现陨石。质量约800克，还需继续研究。')
        self.assertIsNone(current_audio(self.draft,segment));self.assertIn('过期',news.state(self.draft))
        self.assertEqual(Path(first['path']).read_bytes(),Path(self.draft['history'][0]['audio']['path']).read_bytes())
        with self.assertRaises(ValueError):news.synthesize(self.draft,self.provider,None,self.token)
        news.approve(self.draft);news.synthesize(self.draft,self.provider,None,self.token)
        self.assertEqual(self.provider.synthesize.call_count,2)

    def test_voice_settings_invalidate_cache_and_preserve_prior_record(self):
        self.spoken();news.synthesize(self.draft,self.provider,None,self.token)
        segment=self.draft['segments'][0];first=deepcopy(segment['audio'])
        self.draft['tts']['speaker']='serena'
        self.assertIsNone(current_audio(self.draft,segment))
        news.synthesize(self.draft,self.provider,None,self.token)
        self.assertEqual(self.provider.synthesize.call_count,2)
        self.assertEqual(self.draft['history'][0]['audio'],first)
        self.assertTrue(Path(first['path']).is_file())

    def test_alignment_failure_manual_calibration_and_changed_audio_review(self):
        self.spoken();news.synthesize(self.draft,self.provider,None,self.token)
        segment=self.draft['segments'][0];units=sync.reading_units(self.draft,segment)
        self.assertTrue(all(u['news_id']==self.draft['news_id'] and u['task_ids']==[] for u in units))
        provider=Mock();provider.align_batch.side_effect=RuntimeError('fixture align failed')
        self.assertTrue(sync.align_draft(self.draft,provider,self.token));self.assertIsNotNone(current_audio(self.draft,segment))
        self.assertEqual(sync.build_timeline(self.draft)['precision'],'paragraph')
        sync.save_manual(self.draft,segment,[(.1,1.4),(1.6,3.8)])
        self.assertEqual(sync.build_timeline(self.draft)['precision'],'unit')
        news.set_spoken(self.draft,'2022年发现陨石。质量约800克，来自火星。');news.approve(self.draft)
        news.synthesize(self.draft,self.provider,None,self.token)
        self.assertTrue(sync.segment_schedule(self.draft,segment)['manual_review_required'])

    def test_failed_and_cancelled_regeneration_keep_valid_old_audio(self):
        self.spoken();news.synthesize(self.draft,self.provider,None,self.token);segment=self.draft['segments'][0];old=deepcopy(segment['audio'])
        self.provider.synthesize.side_effect=RuntimeError('fixture generation failed')
        self.assertTrue(news.synthesize(self.draft,self.provider,None,self.token,force=True))
        self.assertEqual(segment['audio'],old);self.assertIn('最近生成失败',news.state(self.draft))
        self.provider.synthesize.side_effect=TaskCancelledError()
        with self.assertRaises(TaskCancelledError):news.synthesize(self.draft,self.provider,None,self.token,force=True)
        self.assertEqual(segment['audio'],old)

    def test_invalid_oral_input_is_saved_but_cannot_be_approved(self):
        news.set_spoken(self.draft,'来源：https://example.invalid')
        news.validate(self.draft)
        with self.assertRaises(ValueError):news.approve(self.draft)

    def test_proposal_requires_actual_quotes_and_rejects_new_numbers(self):
        def request(prompt,config,**kw):
            self.assertIn('区分事件时间和报道时间',prompt);value=dict(spoken='2022年发现陨石，重900克。',quotes=['2022年发现陨石。'])
            kw['validator'](value)
        with patch.object(news,'request_json',side_effect=request),self.assertRaises(AIResponseParseError):news.propose(self.article,default_settings(),self.token)
        def supported(prompt,config,**kw):
            return kw['validator'](dict(spoken='2022年发现一块质量约800克的陨石。',quotes=['2022年发现陨石。']))
        with patch.object(news,'request_json',side_effect=supported):
            self.assertIn('800克',news.propose(self.article,default_settings(),self.token)['spoken'])

    def test_proposal_uses_real_json_request_contract(self):
        settings=replace(default_settings(),deepseek_api_key='fixture-not-a-real-key',deepseek_model='fixture-model')
        response=dict(spoken='2022年发现一块质量约800克的陨石。',quotes=['2022年发现陨石。'])
        with patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(response,ensure_ascii=False))) as complete:
            result=news.propose(self.article,settings,self.token)
        self.assertEqual(result,response)
        self.assertIs(complete.call_args.kwargs['cancel_token'],self.token)
        self.assertEqual(self.draft['segments'][0]['spoken'],'')

    def test_proposal_receives_original_body_and_preserves_supplemental_quote_source(self):
        quote='实验室将样本与既有标本进行比较。'
        url='https://science.nasa.gov/fixture'
        self.article.evidence=[dict(quote=quote,url=url,title='补充原始资料')]
        response=dict(spoken='陨石重约800克，研究人员将样本与既有标本比较。',quotes=['质量约800克。',quote])
        with patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(response))) as complete:
            result=news.propose(self.article,default_settings(),self.token)
        prompt=complete.call_args.args[0]
        self.assertIn(self.article.body,prompt)
        self.assertIn(url,prompt)
        self.assertEqual(result['quote_sources'][quote]['url'],url)

    def test_proposal_accepts_title_evidence_and_repairs_an_invalid_quote(self):
        self.article.news.title='NASA资助的火星陨石研究'
        response=dict(spoken='2022年发现一块质量约800克的陨石。',quotes=[self.article.news.title,'2022年发现陨石。'])
        invalid=dict(response,quotes=['This invented sentence is not in the source'])
        with patch('ai.providers.complete_with_metadata',side_effect=[dict(content=json.dumps(value)) for value in (invalid,response)]) as complete:
            result=news.propose(self.article,default_settings(),self.token)
        self.assertEqual(result,response);self.assertEqual(complete.call_count,2)
        self.assertIn('口播稿缺少对应来源原句',complete.call_args.args[0])

    def test_proposal_accepts_typography_and_keeps_original_source_quote(self):
        self.article.body='Virgin Galactic’s spacecraft lifted off —\u00a0beneath the wings of its carrier aircraft.'
        response=dict(spoken='维珍银河的飞行器由载机携带升空。',
                      quotes=["Virgin Galactic's spacecraft lifted off — beneath the wings of its carrier aircraft."])
        with patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(response))) as complete:
            result=news.propose(self.article,default_settings(),self.token)
        self.assertEqual(result['quotes'],[self.article.body])
        self.assertEqual(complete.call_count,1)

    def test_real_failed_replies_keep_mission_numbers_separate_from_quantities(self):
        rows=json.loads((Path(__file__).parent/'fixtures/news_narration_20261104.json').read_text(encoding='utf-8'))
        for row in rows:
            source=row['source']
            article=Article(News('来源',source['title'],source['reported_at'],source['source_text'],source['source_url']),'','rss')
            for content in row['responses']:
                with self.subTest(log=row['log'],content=content):
                    with patch('ai.providers.complete_with_metadata',return_value=dict(content=content,finish_reason='stop')) as complete:
                        self.assertEqual(news.propose(article,default_settings(),self.token)['spoken'],json.loads(content)['spoken'])
                    complete.assert_called_once()
        proposal=dict(spoken='阿耳忒弥斯三号搭载3颗卫星。',quotes=[article.news.title])
        with patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(proposal),finish_reason='stop')):
            with self.assertRaisesRegex(AIResponseValidationError,'来源没有支持的数字'):news.propose(article,default_settings(),self.token)
        proposal['spoken']='阿波罗三号任务已经发射。'
        with patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(proposal))):
            with self.assertRaisesRegex(AIResponseValidationError,'来源不支持的任务编号'):news.propose(article,default_settings(),self.token)

    def test_length_finish_reason_is_logged_and_credentials_are_redacted(self):
        config=Mock(model='fixture',api_key='fixture-secret')
        with patch('ai.providers.complete_with_metadata',return_value=dict(content='{"spoken":"fixture-secret"}',finish_reason='length')):
            with self.assertRaises(AITruncatedResponseError) as error:
                request_json('诊断输入 fixture-secret',config,module_name='news_narration',max_attempts=1)
        log=error.exception.diagnostic_path.read_text(encoding='utf-8')
        self.assertIn('length',log);self.assertNotIn('fixture-secret',log)

    def test_swift_real_failed_responses_accept_dated_english_month(self):
        row=json.loads((Path(__file__).parent/'fixtures/news_narration_swift_20261107.json').read_text(encoding='utf-8'))
        source=row['source']
        article=Article(News('SpacePolicyOnline',source['title'],source['reported_at'],source['source_text'],source['source_url']),'','rss_summary')
        for response in row['responses']:
            with self.subTest(response=response),patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(response),finish_reason='stop')) as complete:
                self.assertEqual(news.propose(article,default_settings(),self.token)['spoken'],response['spoken'])
                complete.assert_called_once()
        unsupported=dict(row['responses'][0],spoken=row['responses'][0]['spoken'].replace('9月','10月'))
        with patch('ai.providers.complete_with_metadata',return_value=dict(content=json.dumps(unsupported),finish_reason='stop')):
            with self.assertRaisesRegex(AIResponseValidationError,'没有支持的数字或编号：10'):
                news.propose(article,default_settings(),self.token)
        self.assertEqual(news._dated_months('It may launch soon.'),'')
        self.assertEqual(news._dated_months('September 2025, 4 Nov 2026, Oct. 23, 2026'),'9 10 11')

    def test_cancelled_batch_preserves_finished_proposals(self):
        settings=replace(default_settings(),deepseek_api_key='fixture',deepseek_base_url='https://example.invalid',deepseek_model='fixture')
        other=deepcopy(self.article);other.news.url+='-other'
        proposal=dict(spoken='第一条口播。',quotes=[self.article.body])
        def first(*args):
            self.token.cancel()
            return proposal
        with patch.object(news,'propose',side_effect=first) as propose:
            proposals,failures,states=news.propose_missing([self.article,other],settings,self.token,set())
        self.assertEqual(proposals,{self.draft['news_id']:proposal});self.assertFalse(failures)
        self.assertEqual(states[news_id(other.news.url)],'cancelled');propose.assert_called_once()

    def test_export_uses_actual_wav_and_paragraph_subtitles_without_fake_precision(self):
        self.spoken();news.synthesize(self.draft,self.provider,None,self.token)
        output=news.export(self.draft,self.root,self.token)
        import json
        manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['subtitle_precision'],'paragraph');self.assertTrue((output/manifest['preview_video_file']).is_file())
        restored=news.load(output/'draft.json');self.assertIsNotNone(current_audio(restored,restored['segments'][0]))
        prepared=news.prepare_frames(self.draft)
        self.assertEqual(news.frame_at(prepared,3.1).tobytes(),news.frame_at(prepared,3.5).tobytes())


if __name__=='__main__':unittest.main()
