"""Synchronization contract tests. PCM and aligner outputs here are fixtures, not real model acceptance."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import wave

import resources
from services import launch_roundup as r,roundup_sync as sync
from services.roundup_audio import audio_key,current_audio,file_hash,reading_text,wav_info
from services.roundup_export import export_package
from services.cancellation import CancellationToken,TaskCancelledError
from tests.test_launch_roundup import context
from tools import roundup_visuals as visuals


class RoundupSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='build');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.paths=patch.object(resources,'_WORKSPACE_PATH',self.root);self.paths.start();self.addCleanup(self.paths.stop)
        self.token=CancellationToken()
        items=[context(1),context(2)]
        for i in items:i.update(provider='China Aerospace Science and Technology Corporation')
        self.draft=r.create_draft(dict(items=items,complete=False,start='2026-10-20',end='2026-10-29'),'2026-10-20','2026-10-29')
        parts=[dict(text='中国方面，',task_ids=['launch-1','launch-2'],display='group'),
            dict(text='长征六号改发射03星，使用9A工位。',task_ids=['launch-1'],display='task'),
            dict(text='第二天凌晨，长征四号乙执行另一项任务。',task_ids=['launch-2'],display='task'),
            dict(text='两项任务均已发射。',task_ids=['launch-1','launch-2'],display='summary')]
        self.segment=dict(id='china',kind='remaining',spoken=''.join(p['text'] for p in parts),screen='中国两次发射',
            task_ids=['launch-1','launch-2'],source_refs=[],preview_seconds=4)
        sync.set_units(self.segment,parts);self.draft['segments']=[self.segment]
        self.draft['pronunciations']={'03星':'零三星','9A':'九A'}

    def audio(self,segment=None,frames=96000,rate=24000,amplitude=0):
        segment=segment or self.segment
        path=self.root/(segment['id']+'.wav')
        with wave.open(str(path),'wb') as w:
            w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(int(amplitude).to_bytes(2,'little',signed=True)*frames)
        segment['audio']=dict(path=str(path),key=audio_key(self.draft,segment),sha256=file_hash(path),
            duration_seconds=frames/rate,sample_rate=rate,sample_count=frames,reading_text=reading_text(self.draft,segment))
        return segment['audio']

    def aligned(self,segment=None):
        segment=segment or self.segment
        sound=current_audio(self.draft,segment);units=sync.reading_units(self.draft,segment)
        spans=[(.1,.4),(.7,1.4),(1.8,2.8),(3.2,3.7)]
        tokens=[dict(text=u['reading_text'],start_time=a,end_time=b) for u,(a,b) in zip(units,spans)]
        segment['alignment']=dict(version=1,key=sync.alignment_key(self.draft,segment,sound),tokens=tokens)
        return tokens

    def test_persisted_units_have_stable_identity_exact_final_text_and_explicit_scope(self):
        self.audio();before=deepcopy(self.segment['units'])
        units=sync.reading_units(self.draft,self.segment)
        self.assertEqual(''.join(u['reading_text'] for u in units),reading_text(self.draft,self.segment))
        self.assertIn('零三星',units[1]['reading_text']);self.assertNotIn('launch-1',reading_text(self.draft,self.segment))
        self.assertEqual(units[3]['task_ids'],['launch-1','launch-2'])
        path=self.root/'draft.json';r.save_draft(self.draft,path)
        self.assertEqual(r.load_draft(path)['segments'][0]['units'],before)
        self.assertEqual(self.segment['units'],before)

    def test_generation_builds_scope_from_fact_tokens_without_name_searches(self):
        generated=r.local_script(self.draft,'test fixtures')
        china=next(p for p in generated if p['task_ids'])
        self.assertEqual(china['units'][0]['display'],'group')
        tasks=[u for u in china['units'] if u['display']=='task']
        self.assertEqual({i for u in tasks for i in u['task_ids']},{'launch-1','launch-2'})
        self.assertEqual(''.join(u['text'] for u in china['units']),china['spoken'])

    def test_missing_stale_and_invalid_associations_never_guess_another_task(self):
        self.audio();self.aligned()
        self.segment['units'][1]['task_ids']=['no-such-id']
        clock=sync.segment_schedule(self.draft,self.segment)
        self.assertEqual(clock['precision'],'paragraph');self.assertIn('关联待检查',clock['message'])
        self.segment['units'][1]['task_ids']=['launch-1'];self.segment['spoken']+='编辑。'
        self.assertIn('待复查',sync.segment_schedule(self.draft,self.segment)['message'])
        self.assertEqual(self.segment['units'][1]['task_ids'],['launch-1'])
        sync.rebuild_units(self.segment)
        self.assertTrue(all(u['association_status']=='needs_review' and not u['task_ids'] for u in self.segment['units']))

    def test_replacements_may_not_cross_unit_boundaries(self):
        self.draft['pronunciations']={'中国方面，长征':'交叉边界'}
        with self.assertRaisesRegex(ValueError,'跨越'):sync.reading_units(self.draft,self.segment)

    def test_alignment_matches_entire_known_text_and_rejects_bad_times(self):
        self.audio();tokens=self.aligned();units=sync.reading_units(self.draft,self.segment)
        result=sync.map_tokens(tokens,units,96000)
        self.assertEqual(result[1]['start_sample'],16800)
        for change in [dict(start_time=0,end_time=0),dict(start_time=float('nan')),dict(start_time=-.00001),dict(end_time=4.00001),dict(end_time=99),dict(text='different'),dict(start_time=.05)]:
            bad=deepcopy(tokens)
            if change==dict(start_time=0,end_time=0):
                for item in bad:item.update(change)
            else:bad[2].update(change)
            with self.assertRaises(ValueError,msg=str(change)):sync.map_tokens(bad,units,96000)
        with self.assertRaisesRegex(ValueError,'为空'):sync.map_tokens([],units,96000)

    def test_sentence_mapping_accepts_character_tokens_without_interpolating(self):
        units=[dict(id='u1',reading_text='零三星。'),dict(id='u2',reading_text='九A工位。')]
        tokens=[dict(text=c,start_time=a,end_time=b) for c,a,b in [('零',.1,.2),('三',.23,.4),('星',.6,.8),('九',1,1.2),('A',1.3,1.6),('工',1.7,1.8),('位',1.9,2.2)]]
        spans=sync.map_tokens(tokens,units,72000)
        self.assertEqual([(s['start_sample']/24000,s['end_sample']/24000) for s in spans],[(.1,.8),(1,2.2)])

    def test_timeline_is_sample_based_and_downstream_offsets_follow_changed_audio(self):
        self.audio();self.aligned()
        second=deepcopy(self.segment);second['id']='second';second.pop('units');second.pop('alignment');self.draft['segments'].append(second)
        self.audio(second,frames=12001,rate=44100)
        table=sync.build_timeline(self.draft)
        self.assertEqual(table['segments'][1]['start_sample'],103200)
        self.assertEqual(table['total_samples'],103200+round(Fraction(12001*24000,44100))+36000)
        self.assertEqual(table['segments'][-1]['pause_samples'],36000)
        self.draft['closing_hold_seconds']=1.25
        adjusted=sync.build_timeline(self.draft)
        self.assertEqual(adjusted['total_samples'],table['total_samples']-6000)
        self.assertEqual(adjusted['segments'][-1]['speech_end_sample'],table['segments'][-1]['speech_end_sample'])
        combined=sync.join_timelines([adjusted,table])
        self.assertEqual(combined['segments'][2]['start_sample'],adjusted['total_samples'])
        self.assertEqual(combined['segments'][2]['units'][0]['global_start_sample'],
                         adjusted['total_samples']+table['segments'][0]['units'][0]['global_start_sample'])
        self.audio(frames=120000,amplitude=10)
        changed=sync.build_timeline(self.draft)
        self.assertEqual(changed['segments'][1]['start_sample'],127200)
        self.assertEqual(changed['segments'][0]['precision'],'paragraph')
        self.assertEqual(changed['segments'][1]['audio']['sha256'],table['segments'][1]['audio']['sha256'])

    def test_manual_calibration_persists_and_cannot_be_applied_to_new_audio(self):
        self.audio();self.aligned()
        spans=[(.05,.5),(.65,1.5),(1.7,2.9),(3.1,3.8)]
        sync.save_manual(self.draft,self.segment,spans)
        path=self.root/'calibrated.json';r.save_draft(self.draft,path)
        restored=r.load_draft(path)
        self.assertEqual(sync.build_timeline(restored)['segments'][0]['status'],'manual')
        self.audio(amplitude=32)
        clock=sync.segment_schedule(self.draft,self.segment)
        self.assertEqual(clock['precision'],'paragraph');self.assertIn('旧人工校准待复查',clock['message'])
        self.segment.pop('sync_manual');self.aligned()
        self.assertEqual(sync.segment_schedule(self.draft,self.segment)['status'],'automatic')

    def test_manual_bounds_overlap_order_and_count_are_validated(self):
        self.audio()
        for times in [[(0,.5)],[(0,.5),(.3,1),(1,2),(2,4)],[(0,0),(.3,1),(1,2),(2,4)],[(0,.5),(.5,1),(1,2),(2,4.01)]]:
            with self.assertRaises(ValueError):sync.save_manual(self.draft,self.segment,times)
        self.assertNotIn('sync_manual',self.segment)

    def test_cached_alignment_uses_audio_contents_final_text_and_model_config(self):
        self.audio();tokens=self.aligned();self.segment.pop('alignment')
        provider=Mock()
        provider.align_batch.side_effect=lambda jobs,token,done:done('china',tokens)
        self.assertEqual(sync.align_draft(self.draft,provider,self.token),[])
        provider.align_batch.reset_mock()
        sync.align_draft(self.draft,provider,self.token);provider.align_batch.assert_not_called()
        key=self.segment['alignment']['key'];self.audio(amplitude=12)
        self.assertNotEqual(sync.alignment_key(self.draft,self.segment,current_audio(self.draft,self.segment)),key)
        self.assertEqual(sync.segment_schedule(self.draft,self.segment)['status'],'pending')
        self.aligned();self.draft['aligner']['language']='English'
        self.assertEqual(sync.segment_schedule(self.draft,self.segment)['status'],'pending')

    def test_failed_and_cancelled_alignment_keep_audio_and_partial_success(self):
        sound=self.audio();before=Path(sound['path']).read_bytes()
        provider=Mock();provider.align_batch.side_effect=RuntimeError('缺少模型')
        errors=sync.align_draft(self.draft,provider,self.token)
        self.assertTrue(errors);self.assertEqual(Path(sound['path']).read_bytes(),before)
        self.assertEqual(sync.segment_schedule(self.draft,self.segment)['precision'],'paragraph')
        tokens=self.aligned();self.segment.pop('alignment')
        def cancel(jobs,token,done):done('china',tokens);raise TaskCancelledError()
        provider.align_batch.side_effect=cancel
        with self.assertRaises(TaskCancelledError):sync.align_draft(self.draft,provider,self.token)
        self.assertEqual(sync.segment_schedule(self.draft,self.segment)['status'],'automatic')
        self.assertTrue(list((self.root/'cache/roundup_alignment').rglob('alignment.json')))

    def test_renderer_uses_unit_starts_multi_highlights_and_stable_silence_hold(self):
        self.audio();self.aligned()
        prepared=visuals.prepare_frames(self.draft,self.segment)
        self.assertEqual(prepared['starts'],[0,.7,1.8,3.2])
        self.assertEqual(prepared['scenes'][0]['focus_id'],None)
        self.assertEqual(prepared['scenes'][1]['focus_id'],'launch-1')
        self.assertEqual(prepared['scenes'][2]['focus_id'],'launch-2')
        self.assertEqual(len(prepared['scenes'][3]['selectors']),1)
        self.assertEqual(visuals.frame_at(prepared,4).tobytes(),visuals.frame_at(prepared,7).tobytes())
        # Seeking generates the whole state directly, independent of any earlier frame calls.
        expected=visuals.frame_at(prepared,1.9).tobytes()
        visuals.frame_at(prepared,.2);visuals.frame_at(prepared,3.9)
        self.assertEqual(visuals.frame_at(prepared,1.9).tobytes(),expected)

    def test_pending_audio_has_one_summary_not_average_task_durations(self):
        self.audio()
        prepared=visuals.prepare_frames(self.draft,self.segment)
        self.assertEqual(prepared['starts'],[0]);self.assertIsNone(prepared['scenes'][0]['focus_id'])
        self.assertEqual(len(prepared['scenes'][0]['selectors']),1)

    def test_task_selector_expands_and_contracts_to_contiguous_summary_without_exiting(self):
        self.audio();self.aligned()
        prepared=visuals.prepare_frames(self.draft,self.segment)
        first,last,summary=prepared['scenes'][1:]
        self.assertIsNotNone(summary['selector'])
        union=(first['selector'][0],first['selector'][1],last['selector'][2],last['selector'][3])
        self.assertEqual(summary['selector'],union)
        x=visuals.LIST_RIGHT-80
        for before,after in ((last,summary),(summary,last),(summary,first)):
            for progress in (0,.1,.25,.5,.75,.9,1):
                canvas=after['image'].copy()
                visuals._slide_selector(canvas,before,after,progress)
                eased=visuals.cards.smooth(progress,0,1)
                bounds=tuple(round(a+(b-a)*eased) for a,b in zip(before['selector'],after['selector']))
                self.assertEqual(canvas.getpixel((x,(bounds[1]+bounds[3])//2)),(37,51,111,255))
                # No selection may travel below the two associated task rows.
                self.assertEqual(canvas.crop((visuals.LIST_LEFT,union[3]+1,visuals.LIST_RIGHT+1,visuals.BODY_BOTTOM+1)).tobytes(),
                    after['image'].crop((visuals.LIST_LEFT,union[3]+1,visuals.LIST_RIGHT+1,visuals.BODY_BOTTOM+1)).tobytes())

    def test_exported_timeline_subtitles_and_wav_share_exact_samples(self):
        self.audio();self.aligned()
        output=export_package(self.draft,self.root,self.token,visuals=False)
        table=json.loads((output/'timeline.json').read_text(encoding='utf-8'))
        manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['subtitle_precision'],'unit');self.assertEqual(manifest['sync_version'],sync.VERSION)
        self.assertEqual({k:v for k,v in table.items() if k!='playback_audio'},sync.build_timeline(self.draft))
        self.assertEqual(table['playback_audio']['sha256'],file_hash(output/manifest['narration_file']))
        self.assertEqual(wav_info(output/manifest['narration_file']),(table['total_samples'],24000))
        srt=(output/manifest['subtitle_file']).read_text(encoding='utf-8')
        self.assertIn('00:00:00,700 --> 00:00:01,400',srt);self.assertIn('03星',srt);self.assertNotIn('零三星',srt)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg unavailable')
    def test_resampling_is_done_before_alignment_and_uses_exact_concat_count(self):
        self.audio(frames=176401,rate=44100)
        path=sync.canonical_audio(current_audio(self.draft,self.segment),self.token)
        self.assertEqual(wav_info(path),(96001,24000))
        table=sync.build_timeline(self.draft);merged=sync.timeline_audio(self.draft,table,self.token)
        self.assertEqual(wav_info(merged),(table['total_samples'],24000))
        before=file_hash(merged)
        data=bytearray(merged.read_bytes());data[-2:]=b'\x12\x00';merged.write_bytes(data)
        self.assertNotEqual(file_hash(merged),before)
        self.assertEqual(file_hash(sync.timeline_audio(self.draft,table,self.token)),before)

    def test_isolated_worker_contract_and_load_failure_do_not_report_success(self):
        from ai.qwen_aligner import QwenForcedAligner
        model=self.root/'model';model.mkdir();(model/'config.json').write_text('{}')
        worker=self.root/'fake_worker.py'
        worker.write_text("import json,sys\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\nPath(r['status']).write_text(json.dumps(dict(state='fatal',error='fixture missing dependency'))+'\\n',encoding='utf-8')\n")
        callback=Mock();provider=QwenForcedAligner(dict(python=sys.executable,model=str(model)))
        with patch('ai.qwen_aligner.bundled_resource_path',return_value=worker),self.assertRaisesRegex(RuntimeError,'fixture missing'):
            provider.align_batch([dict(id='one',audio='unused',text='fixture')],self.token,callback)
        callback.assert_not_called()

    def test_alignment_cancel_confirms_worker_exit(self):
        from ai.qwen_aligner import QwenForcedAligner
        import threading
        model=self.root/'model';model.mkdir();(model/'config.json').write_text('{}')
        worker=self.root/'slow_worker.py';worker.write_text('import time\ntime.sleep(30)\n')
        processes=[];real_popen=subprocess.Popen
        def start(*args,**kwargs):
            process=real_popen(*args,**kwargs);processes.append(process);return process
        timer=threading.Timer(.25,self.token.cancel);timer.start();self.addCleanup(timer.cancel)
        provider=QwenForcedAligner(dict(python=sys.executable,model=str(model)))
        with patch('ai.qwen_aligner.bundled_resource_path',return_value=worker),patch('ai.qwen_aligner.subprocess.Popen',side_effect=start),self.assertRaises(TaskCancelledError):
            provider.align_batch([dict(id='one',text='fixture',audio='unused')],self.token,Mock())
        self.assertTrue(processes);self.assertIsNotNone(processes[0].poll())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg unavailable')
    def test_default_export_frame_clock_matches_preview_with_fractional_segment_start(self):
        from PIL import Image
        self.audio();self.aligned()
        prefix=dict(id='prefix',kind='transition',task_ids=[],spoken='机械测试引导。',screen='机械测试引导。',source_refs=[],preview_seconds=.1)
        self.draft['segments'].insert(0,prefix);self.audio(prefix,frames=2500)
        table=sync.build_timeline(self.draft);row=table['segments'][1]
        prepared=visuals.prepare_frames(self.draft,self.segment)
        output=export_package(self.draft,self.root,self.token)
        manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        mov=output/manifest['segments'][1]['visual_file']
        first_frame=(row['start_sample']*30+23999)//24000
        flags=dict(creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for frame in (0,3,9,22,55,98,119):
            png=self.root/f'frame-{frame}.png'
            subprocess.run(['ffmpeg','-v','error','-y','-i',str(mov),'-vf',f'select=eq(n\\,{frame})','-frames:v','1',str(png)],check=True,**flags)
            expected=visuals.frame_at(prepared,(first_frame+frame)/30-row['start_seconds']).convert('RGBA')
            self.assertEqual(Image.open(png).convert('RGBA').tobytes(),expected.tobytes())
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output/manifest['preview_video_file'])],**flags))
        self.assertEqual({s['codec_type'] for s in info['streams']},{'video','audio'})
        for stream in info['streams']:
            self.assertLessEqual(abs(float(stream['start_time'])),1/30)
            self.assertLessEqual(abs(float(stream['duration'])-table['duration_seconds']),1/30)
        self.assertEqual(int(next(s for s in info['streams'] if s['codec_type']=='video')['nb_frames']),
            (table['total_samples']*30+23999)//24000)


if __name__=='__main__':unittest.main()
