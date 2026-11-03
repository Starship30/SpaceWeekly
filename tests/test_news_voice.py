"""News adapter contracts with fixture PCM/alignment; real model acceptance is separate."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest,wave
import json
from dataclasses import replace
from unittest.mock import Mock,patch

import resources
from ai.json_utils import AIResponseParseError
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
        self.assertIn('news_audio',first['path']);self.assertTrue((self.root/'output/news-drafts'/f"{self.draft['id']}.json").is_file())
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
        with patch('ai.providers.complete',return_value=json.dumps(response,ensure_ascii=False)) as complete:
            result=news.propose(self.article,settings,self.token)
        self.assertEqual(result,response)
        self.assertIs(complete.call_args.kwargs['cancel_token'],self.token)
        self.assertEqual(self.draft['segments'][0]['spoken'],'')

    def test_proposal_accepts_title_evidence_and_repairs_an_invalid_quote(self):
        self.article.news.title='NASA资助的火星陨石研究'
        response=dict(spoken='2022年发现一块质量约800克的陨石。',quotes=[self.article.news.title,'2022年发现陨石。'])
        invalid=dict(response,quotes=['This invented sentence is not in the source'])
        with patch('ai.providers.complete',side_effect=[json.dumps(invalid),json.dumps(response)]) as complete:
            result=news.propose(self.article,default_settings(),self.token)
        self.assertEqual(result,response);self.assertEqual(complete.call_count,2)
        self.assertIn('口播稿缺少对应来源原句',complete.call_args.args[0])

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
