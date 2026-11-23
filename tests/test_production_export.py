"""Production contracts; fixture PCM and mock encoders do not assess real speech quality."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
import wave

import resources
from models.article import Article
from models.news import News
from services import launch_roundup as launch,news_voice as news,roundup_sync as sync
from services.production_export import selected_roundup,produce
from services.cancellation import CancellationToken,TaskCancelledError
from services.roundup_audio import generate_audio,current_audio
from tests.test_launch_roundup import context,generated,write_pcm


class ProductionExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();p=patch.object(resources,'_WORKSPACE_PATH',self.root);p.start();self.addCleanup(p.stop)
        collection=dict(items=[context(1),context(2),context(3)],complete=True,start='2026-10-20',end='2026-10-29')
        self.draft=launch.create_draft(collection,'2026-10-20','2026-10-29')
        self.draft['segments']=launch.validate_generated(generated(),self.draft)
        self.draft['tts']['model']='fixture-model'
        self.provider=Mock(is_running=True)
        self.provider.synthesize.side_effect=lambda req,path,cancel_check:write_pcm(path)
        self.token=CancellationToken()

    def article(self,n=1):
        draft=news.create(Article(News('来源','新闻'+str(n),'2026-10-23','屏幕摘要','https://example.invalid/'+str(n)),'正文','test'),self.draft)
        news.set_spoken(draft,'这是一条已确认的新闻口播。')
        return draft

    def test_episode_loads_one_aligner_for_two_uncached_news_drafts(self):
        drafts=[self.article(1),self.article(2)]
        for draft in drafts:draft['aligner'].update(python='fixture-python',model='fixture-model')
        output=self.root/'episode.mp4';output.write_bytes(b'fixture')
        def align(jobs,token,done):
            self.assertEqual(len(jobs),2)
            self.assertEqual(len({job['id'] for job in jobs}),2)
            for job in jobs:done(job['id'],[dict(text=job['text'],start_time=.1,end_time=.5)])
        with patch('ai.qwen_aligner.QwenForcedAligner') as aligner, \
                patch('services.roundup_export.export_complete_video',return_value=output) as render:
            batch=aligner.return_value.align_batch
            batch.side_effect=align
            for _ in range(2):
                result=produce(drafts,self.root,self.provider,self.token,episode={'number':1})
                self.assertEqual(result['paths'],[str(output)],result)
                self.assertFalse(result['alignment_pending'])
            batch.assert_called_once()
            render.assert_called_once()
        self.assertTrue(all(sync.build_timeline(draft)['precision']=='unit' for draft in drafts))

    def test_checked_scope_preserves_original_and_empty_does_not_fall_back(self):
        original=deepcopy(self.draft)
        self.draft['selection'][1]['included']=False
        self.draft['segments']=[s for s in self.draft['segments'] if s['kind']!='overview']
        self.draft['segments'][-1].pop('statistics_key',None)
        view=selected_roundup(self.draft)
        self.assertEqual([s['id'] for s in view['segments']],['focus1','rest','outro'])
        self.assertEqual(self.draft['segments'][1]['id'],'focus2')
        for row in self.draft['selection']:row['included']=False
        self.assertEqual(selected_roundup(self.draft)['segments'],[])
        self.assertEqual(original['segments'][2]['id'],'focus2')

    def test_mixed_narration_blocks_export_without_erasing_history(self):
        segment=self.draft['segments'][1];segment['task_ids']=['launch-1','launch-2']
        self.draft['selection'][1]['included']=False
        launch.validate_draft(self.draft)
        with self.assertRaisesRegex(ValueError,'合并口播'):selected_roundup(self.draft)
        self.assertEqual(segment['task_ids'],['launch-1','launch-2'])

    def test_unchecked_paragraph_leaves_list_but_keeps_cached_intro_and_history(self):
        generate_audio(self.draft,self.provider,self.token)
        original=deepcopy(self.draft);self.draft['segments'][2]['included']=False
        view=selected_roundup(self.draft)
        self.assertNotIn('focus2',[s['id'] for s in view['segments']])
        self.assertEqual({s['id'] for s in view['selection'] if s['included']},{'launch-1','launch-3'})
        self.assertEqual(current_audio(view,view['segments'][0]),current_audio(original,original['segments'][0]))
        self.assertEqual(self.draft['segments'][2]['audio'],original['segments'][2]['audio'])

    def test_empty_news_blocks_full_video_but_keeps_ready_audio(self):
        drafts=[self.article(1),self.article(2)];news.set_spoken(drafts[0],'')
        with patch('services.roundup_export.export_complete_video') as encode:
            result=produce(drafts,self.root,self.provider,self.token)
        encode.assert_not_called()
        self.assertFalse(result['paths']);self.assertEqual(len(result['failed']),1)
        self.assertEqual(result['failed'][0]['id'],drafts[0]['id'])
        self.assertEqual(self.provider.synthesize.call_count,1)
        self.assertIsNotNone(current_audio(drafts[1],drafts[1]['segments'][0]))

    def test_partial_news_failure_reuses_audio_to_complete_one_mp4(self):
        drafts=[self.article(1),self.article(2)]
        def synth(req,path,cancel_check):
            if self.provider.synthesize.call_count==2:raise RuntimeError('fixture failure')
            write_pcm(path)
        self.provider.synthesize.side_effect=synth
        path=self.root/'complete.mp4';path.write_bytes(b'fixture encoder result')
        with patch('services.roundup_export.export_complete_video',return_value=path) as encode:
            first=produce(drafts,self.root,self.provider,self.token)
            self.assertFalse(first['paths']);self.assertEqual(len(first['failed']),1)
            encode.assert_not_called()
            self.provider.synthesize.side_effect=lambda req,path,cancel_check:write_pcm(path)
            second=produce(drafts,self.root,self.provider,self.token)
            self.assertEqual(second['paths'],[str(path)]);self.assertFalse(second['failed'])
            self.assertEqual(encode.call_args.args[0],drafts)
            self.assertEqual(produce(drafts,self.root,self.provider,self.token)['paths'],[str(path)])
            self.assertEqual(encode.call_count,1)
            with patch('services.news_voice.export_strip',return_value=path) as strips:
                produce(drafts,self.root,self.provider,self.token,parts=True)
                self.assertEqual(strips.call_count,2)
            self.assertEqual(encode.call_count,1)
        self.assertEqual(self.provider.synthesize.call_count,3)
        self.assertEqual(self.provider.unload.call_count,4)
        self.assertFalse(list(self.root.glob('*.wav')))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_complete_mp4_joins_launch_and_news_with_images_and_subtitles(self):
        from PIL import Image
        from services.roundup_audio import file_hash

        launch_draft=self.draft
        launch_draft['segments']=launch_draft['segments'][1:2]
        drafts=[launch_draft,self.article(2),self.article(1)]
        for draft,color in zip(drafts[1:],('red','blue')):
            picture=self.root/f'{color}.png'
            Image.new('RGB',(128,72),color).save(picture)
            draft['screen_item']['image']=dict(path=str(picture),sha256=file_hash(picture))
        result=produce(drafts,self.root,self.provider,self.token)
        self.assertFalse(result['failed'],result['failed'])
        self.assertEqual(len(result['paths']),1)
        path=result['paths'][0]
        streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',path]))['streams']
        self.assertEqual([s['codec_name'] for s in streams],['h264','aac','mov_text'])
        self.assertEqual((streams[0]['width'],streams[0]['height']),(1920,1080))
        def frame(time):
            data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(time),'-i',path,
                                          '-frames:v','1','-c:v','png','-f','image2pipe','-'])
            return Image.open(BytesIO(data)).convert('RGB')
        launch_end=sync.build_timeline(launch_draft)['duration_seconds']
        news_end=launch_end+sync.build_timeline(drafts[1])['duration_seconds']
        self.assertLess(frame(.2).getpixel((900,300))[0],240)
        self.assertGreater(frame(launch_end+1).getpixel((900,300))[0],240)
        self.assertGreater(frame(news_end+1).getpixel((900,300))[2],240)
        self.assertLess(frame(launch_end+1).getpixel((100,920))[2],100)
        subtitles=subprocess.check_output(['ffmpeg','-v','error','-i',path,'-map','0:s:0','-f','srt','-']).decode()
        self.assertIn('这是一条已确认的新闻口播。',subtitles)

    def test_failed_launch_audio_never_reports_incomplete_mp4_as_success(self):
        self.provider.synthesize.side_effect=RuntimeError('fixture failure')
        with patch('services.roundup_export.export_complete_video') as encode:
            result=produce([self.draft],self.root,self.provider,self.token)
        self.assertFalse(result['paths']);self.assertEqual(len(result['failed']),1);encode.assert_not_called()

    def test_dead_worker_not_reloaded_for_every_remaining_item(self):
        self.provider.is_running=False;self.provider.synthesize.side_effect=RuntimeError('fixture model loading failure')
        result=produce([self.article(1),self.article(2)],self.root,self.provider,self.token)
        self.assertEqual(self.provider.synthesize.call_count,1);self.assertEqual(len(result['failed']),2)

    def test_cancelled_batch_checkpoints_successful_audio(self):
        def synth(req,path,cancel_check):
            if self.provider.synthesize.call_count==2:raise TaskCancelledError()
            write_pcm(path)
        self.provider.synthesize.side_effect=synth
        result=produce([self.draft],self.root,self.provider,self.token)
        self.assertTrue(result['cancelled']);self.assertIsNotNone(current_audio(self.draft,self.draft['segments'][0]))
        saved=json.loads((self.root/'cache/production_exports/drafts'/ (self.draft['id']+'.json')).read_text(encoding='utf8'))
        self.assertEqual(saved['segments'][0]['audio'],self.draft['segments'][0]['audio'])

    def test_news_timeline_silence_animation_and_paragraph_caption_offsets(self):
        draft=self.article();news.approve(draft);generate_audio(draft,self.provider,self.token)
        schedule=sync.build_timeline(draft);row=schedule['segments'][0]
        self.assertAlmostEqual(row['start_seconds'],2.15)
        self.assertAlmostEqual(schedule['duration_seconds']-row['speech_end_seconds'],1.7)
        path=sync.timeline_audio(draft,schedule,self.token)
        with wave.open(str(path),'rb') as pcm:
            self.assertEqual(pcm.getnframes(),schedule['total_samples'])
            self.assertEqual(pcm.readframes(schedule['lead_samples']),b'\0\0'*schedule['lead_samples'])
            self.assertNotEqual(pcm.readframes(1),b'\0\0')
        prepared=news.prepare_frames(draft)
        self.assertEqual(news.frame_at(prepared,0,transparent=True).getchannel('A').getextrema(),(0,0))
        self.assertEqual(news.frame_at(prepared,schedule['duration_seconds']-1/30,transparent=True).getchannel('A').getextrema(),(0,0))
        self.assertEqual(news.frame_at(prepared,3.2,transparent=True).getchannel('A').getextrema(),(0,255))
        before=current_audio(draft,draft['segments'][0]);draft['animation_gaps']={'before_voice':.5,'after_voice':.4}
        self.assertEqual(current_audio(draft,draft['segments'][0]),before)
        self.assertAlmostEqual(sync.build_timeline(draft)['segments'][0]['start_seconds'],2.45)

    def test_news_title_voice_summary_closing_are_separate_seekable_states(self):
        draft=self.article();news.set_spoken(draft,'先讲发生的事。最后说明后续。');news.approve(draft)
        generate_audio(draft,self.provider,self.token);segment=draft['segments'][0]
        sync.save_manual(draft,segment,[(0,.18),(.22,.48)])
        clock=sync.build_timeline(draft);a=clock['news_animation']
        self.assertEqual(a['basis'],'actual_audio_end')
        self.assertLess(a['title_ready'],a['voice_start']);self.assertLess(a['voice_start'],a['summary_in'])
        self.assertAlmostEqual(a['summary_out']+a['summary_out_duration'],a['voice_end'])
        self.assertLess(a['voice_end'],a['strip_out']);self.assertLess(a['strip_out'],a['end'])
        prepared=news.prepare_frames(draft)
        from tools import video_cards
        time=a['voice_end']-.01
        late=news.frame_at(prepared,time,transparent=True).getchannel('A').tobytes()
        news.frame_at(prepared,0,transparent=True)
        self.assertEqual(news.frame_at(prepared,time,transparent=True).getchannel('A').tobytes(),late)
        draft['animation_gaps']={'summary_close_before_end':.3}
        self.assertAlmostEqual(sync.build_timeline(draft)['news_animation']['summary_out']+.125,a['voice_end'])
        self.assertIsNotNone(current_audio(draft,segment))
        draft['animation_gaps']['summary_close_before_end']=.01
        last=sync.build_timeline(draft)['news_animation']
        self.assertAlmostEqual(last['summary_out']+last['summary_out_duration'],last['voice_end'])

    def test_narrated_news_retains_original_strip_layout_and_fonts(self):
        from tools import video_cards
        draft=self.article();news.approve(draft);generate_audio(draft,self.provider,self.token)
        prepared=news.prepare_frames(draft)
        timing=prepared['timeline']['news_animation']
        # Compare the original fully revealed design at the same marquee position.
        prepared['duration']=20
        timing.update(summary_in=2.1,summary_in_duration=.85,summary_out=8,strip_out=10,background_out=10.55)
        for time in (0,.5,1.6,2.5,3.2,5):
            self.assertEqual(news.frame_at(prepared,time,transparent=True).tobytes(),
                video_cards.render_frame(prepared['card'],time,20).tobytes())

    def test_subtitle_only_edits_preserve_audio_and_calibration(self):
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        from ui.caption_editor import CaptionTable
        draft=self.article();news.approve(draft);generate_audio(draft,self.provider,self.token);segment=draft['segments'][0]
        sync.save_manual(draft,segment,[(0,.45)]);before=current_audio(draft,segment);clock=sync.build_timeline(draft)
        table=CaptionTable(segment);table.item(0,1).setText('独立编辑的字幕');table.save();table.close()
        after=sync.build_timeline(draft)
        self.assertEqual(current_audio(draft,segment),before)
        self.assertEqual(after['segments'][0]['status'],'manual')
        self.assertEqual(after['segments'][0]['units'][0]['global_start_sample'],clock['segments'][0]['units'][0]['global_start_sample'])
        self.assertEqual(after['segments'][0]['units'][0]['screen'],'独立编辑的字幕')
        self.assertTrue(segment['caption_history'])
