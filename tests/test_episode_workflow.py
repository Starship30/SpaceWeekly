"""Program schedule, cache reuse and the manual-news export contract."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch
import wave

import resources
from models.article import Article
from models.news import News
from services import episode, launch_roundup, news_voice, roundup_sync
from services.cancellation import CancellationToken
from services.settings import default_settings
from tests.test_launch_roundup import context, write_pcm


class EpisodeTests(TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(dir='build');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        workspace=patch.object(resources,'_WORKSPACE_PATH',self.root)
        workspace.start();self.addCleanup(workspace.stop)
        self.metadata=dict(number=43,date='2026-10-30',opening='')
        self.settings=replace(default_settings(),deepseek_api_key='',deepseek_model='',deepseek_base_url='')
        collection=dict(items=[context(1)],complete=True,start='2026-10-16',end='2026-10-29')
        self.launch=launch_roundup.create_draft(collection,'2026-10-16','2026-10-29')
        self.token=CancellationToken()

    def test_opening_uses_saved_date_weekday_and_actual_recap_span(self):
        text=episode.opening_text(self.metadata,self.launch,True)
        self.assertIn('10月30日，星期五',text)
        self.assertIn('第43期航天周报',text)
        named=episode.validate_episode(dict(self.metadata,name='深空周报'))
        self.assertIn('第43期深空周报',episode.opening_text(named,self.launch,True))
        self.assertEqual(episode.episode_name(dict(self.metadata,name=' ')),'航天周报')
        self.assertIn('过去两周',text)
        self.launch['start']='2026-10-20'
        self.assertIn('过去10天',episode.opening_text(self.metadata,self.launch,True))
        self.assertNotIn('发射',episode.opening_text(self.metadata,None,True))
        launch_only=episode.prepare_launch(self.launch,self.settings,self.token,has_news=False)
        self.assertFalse(any(s['kind']=='transition' for s in launch_only['segments']))

    def test_edited_spacex_cues_keep_two_saved_associations(self):
        draft=launch_roundup.create_draft(dict(items=[context(3),context(4)],complete=True),
                                         '2026-10-16','2026-10-29')
        ids=[item['ll2_id'] for item in draft['snapshot']['items']]
        original=['美国 SpaceX 方面，猎鹰9号发射任务甲。。','星舰 V3 发射任务乙（星舰 Flight 15）。']
        row=dict(id='spacex',spoken=''.join(original),task_ids=ids)
        roundup_sync.set_units(row,[dict(text=t,screen='[[claim:s]]。',display='task',task_ids=[i]) for t,i in zip(original,ids)])
        row['spoken']='美国 SpaceX 方面，猎鹰9号发射任务甲。星舰 V3 发射任务乙。'
        cues=roundup_sync.reading_units(draft,row)
        self.assertEqual([c['task_ids'] for c in cues],[[i] for i in ids])
        self.assertEqual(''.join(c['screen'] for c in cues),row['spoken'])
        row['spoken']=row['spoken'].replace('任务乙','任务丙')
        with self.assertRaisesRegex(ValueError,'待复查'):roundup_sync.reading_units(draft,row)

    def test_news_cards_slide_and_images_dissolve(self):
        from PIL import Image
        from services.roundup_audio import file_hash
        from tools import news_visuals
        assets=[]
        for color in ('red','blue'):
            path=self.root/f'{color}.png';Image.new('RGB',(100,64),color).save(path)
            assets.append(dict(path=str(path),sha256=file_hash(path)))
        titles=['新闻一','新闻二']
        first=dict(screen_item=dict(title=titles[0],summary='',image=assets[0]))
        second=dict(screen_item=dict(title=titles[1],summary='',slideshow=assets))
        clock=dict(duration_seconds=4)
        old=news_visuals.prepare_frames(first,titles,0,timeline=clock,entering=False)
        new=news_visuals.prepare_frames(second,titles,1,timeline=clock,entering=False)
        old_frame=news_visuals.frame_at(old,3)
        new_frame=news_visuals.frame_at(new,.3)
        self.assertEqual(news_visuals.slide_from(new,old_frame,new_frame,0),old_frame)
        middle=news_visuals.slide_from(new,old_frame,new_frame,.3)
        self.assertNotEqual(middle.tobytes(),new_frame.tobytes())
        self.assertEqual(middle.getpixel((1300,545)),news_visuals.BG)
        samples=[news_visuals.frame_at(new,t).getpixel((1300,350))[:3] for t in (1.8,2.25,2.8)]
        self.assertEqual(samples,[(255,0,0),(127,0,127),(0,0,255)])

    def test_news_pages_hold_six_rows_and_keep_the_active_item_visible(self):
        from tools import news_visuals
        titles=[f'第 {i+1} 条新闻' for i in range(29)]
        for index,first,count in ((0,0,6),(5,0,6),(6,6,6),(11,6,6),(23,18,6),(24,24,5),(28,24,5)):
            with self.subTest(index=index):
                rows,state=news_visuals.list_layers(titles,index)
                self.assertEqual(state['groups'],(first,count))
                self.assertEqual(len(rows),count)
                self.assertEqual(state['selector'][1],rows[index-first][1])

    def test_news_still_zoom_moves_outward_without_pixel_jumps(self):
        from PIL import Image, ImageDraw
        from tools import news_visuals
        picture=Image.new('RGBA',news_visuals.SIZE,'black')
        ImageDraw.Draw(picture).rectangle((821,231,832,242),fill='red')
        draft=dict(screen_item=dict(title='新闻',summary=''))
        for preview in (True,False):
            with self.subTest(preview=preview),patch.object(news_visuals,'load_screen_image',return_value=picture):
                frames=news_visuals.prepare_frames(draft,['新闻'],0,timeline=dict(duration_seconds=4),
                                                 entering=False,preview=preview)
                divisor=frames['divisor']
                x,y=(news_visuals.PANEL[0]+827)//divisor,(news_visuals.PANEL[1]+237)//divisor
                points=[]
                for index in range(90):
                    image=news_visuals.frame_at(frames,1+index/60)
                    marker=image.crop((x-18,y-18,x+19,y+19)).getchannel('R')
                    values=marker.tobytes();total=sum(values)
                    points.append((sum((i%marker.width)*value for i,value in enumerate(values))/total,
                                   sum((i//marker.width)*value for i,value in enumerate(values))/total))
                self.assertGreater(points[-1][0]-points[0][0],.5)
                self.assertGreater(points[0][1]-points[-1][1],.3)
                for before,after in zip(points,points[1:]):
                    self.assertGreaterEqual(after[0]-before[0],-.12)
                    self.assertLessEqual(after[1]-before[1],.12)

    @skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_music_keeps_fixed_gain_under_narration_and_starts_after_intro(self):
        import math
        import struct
        from services.episode_music import mix_timeline,preview_bed,settings
        def write_tone(path,hz,seconds,level,gate=lambda _t:True):
            with wave.open(str(path),'wb') as handle:
                handle.setparams((1,2,24000,0,'NONE','not compressed'))
                handle.writeframes(b''.join(struct.pack('<h',int(level*32767*math.sin(2*math.pi*hz*i/24000))
                    if gate(i/24000) else 0) for i in range(round(seconds*24000))))
        voice=self.root/'voice.wav';write_tone(voice,1000,6,.6,lambda t:2<=t<=2.8)
        tracks=[self.root/'一.wav',self.root/'二.wav']
        for path,hz in zip(tracks,(280,420)):write_tone(path,hz,2.5,.9)
        edition=dict(music=[str(path) for path in tracks],music_settings=dict(ducking=True,music_volume=18))
        self.assertEqual(settings(edition)['music_volume'],5)
        self.assertEqual(settings(dict(music_settings=dict(ducking=True,music_volume=12)))['music_volume'],12)
        clock=dict(duration_seconds=6,segments=[dict(start_seconds=2,speech_end_seconds=2.8,audio=True)])
        track=mix_timeline(voice,edition,clock,self.root,shutil.which('ffmpeg'),self.token,intro_seconds=.5)
        with wave.open(str(track),'rb') as wav:
            self.assertEqual((wav.getframerate(),wav.getnchannels(),wav.getnframes()),(48000,2,288000))
            frames=struct.unpack('<'+str(wav.getnframes()*2)+'h',wav.readframes(wav.getnframes()))[::2]
        def level(values,t,hz):
            start=round(t*48000);samples=values[start:start+4800]
            total=sum(sample*complex(math.cos(2*math.pi*hz*i/48000),math.sin(2*math.pi*hz*i/48000))
                      for i,sample in enumerate(samples))
            return 2*abs(total)/len(samples)/32767
        self.assertLess(level(frames,.2,280),.001)
        quiet=level(frames,1.4,280)
        self.assertGreater(quiet,.02)
        for moment in (2.0,2.35,2.85):self.assertAlmostEqual(level(frames,moment,280),quiet,delta=.001)
        self.assertGreater(level(frames,2.35,1000),.35)
        self.assertGreater(level(frames,4,420),.02)

        bed=preview_bed(edition,clock,self.token)
        with wave.open(str(bed),'rb') as wav:
            preview=struct.unpack('<'+str(wav.getnframes()*2)+'h',wav.readframes(wav.getnframes()))[::2]
        self.assertAlmostEqual(level(preview,2.2,280),level(preview,1.4,280),delta=.001)
        self.assertAlmostEqual(level(preview,2.2,280)*.05,quiet,delta=.001)
        self.assertEqual(preview_bed(edition,dict(clock,segments=[]),self.token),bed)

    @skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_mix_respects_fades_levels_no_loop_and_disabled_preflight(self):
        import math
        from array import array
        from services.episode_music import mix,settings,tracks
        ffmpeg=shutil.which('ffmpeg');voice=self.root/'voice.wav';music=self.root/'local.m4a'
        subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','sine=f=1000:d=4','-af','volume=3',str(voice)],check=True)
        subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','sine=f=400:d=2','-af','volume=3','-c:a','aac',str(music)],check=True)
        edition={**self.metadata,'music':[str(music)],'music_settings':dict(music_volume=40,voice_volume=50,
                 fade_in=.8,fade_out=.6,loop=False,ducking=False)}
        metadata=episode.validate_episode(edition)
        mixed=mix(voice,tracks(metadata),self.root,4,0,ffmpeg,self.token,options=settings(metadata))
        with wave.open(str(mixed),'rb') as pcm:
            self.assertEqual(pcm.getnframes(),192000)
            samples=array('h',pcm.readframes(pcm.getnframes()))[::2]
        def level(second,frequency):
            start=round(second*48000);window=samples[start:start+4800]
            return 2*abs(sum(value*complex(math.cos(2*math.pi*frequency*i/48000),math.sin(2*math.pi*frequency*i/48000))
                             for i,value in enumerate(window)))/len(window)/32768
        self.assertGreater(level(1,400),.08)
        self.assertLess(level(.03,400),level(1,400)/3)
        self.assertLess(level(1.90,400),level(1,400)/3)
        self.assertLess(level(2.5,400),.005)
        self.assertAlmostEqual(level(2.5,1000),.133,delta=.02)
        metadata['music_settings']['enabled']=False
        self.assertEqual(tracks(metadata),[])
        with self.assertRaisesRegex(ValueError,'音量'):
            episode.validate_episode({**metadata,'music_settings':{'music_volume':101}})

    def test_shared_review_dates_apply_at_beijing_midnight_and_reach_opening(self):
        metadata=dict(self.metadata,review_range=['2026-10-23','2026-10-29'])
        def fixture(stamp):
            return Article(News('来源','新闻',stamp,'','https://example.invalid/story'),'正文','fixture')
        self.assertTrue(episode.includes_news(metadata,fixture('2026-10-22T16:00:00Z')))
        self.assertFalse(episode.includes_news(metadata,fixture('2026-10-29T16:00:00Z')))
        self.assertIn('过去一周',episode.opening_text(metadata,self.launch,True))
        chapter=episode.chapter_draft(metadata,{},'opening','北京时间。')
        self.assertEqual(chapter['review_range'],metadata['review_range'])

    @skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_one_command_renders_program_and_reuses_only_unchanged_clips(self):
        from services.episode_video import probe
        from services import news_video
        from PIL import Image
        intro=self.root/'intro.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=green:s=320x180:r=60:d=0.5',
            '-f','lavfi','-i','sine=frequency=900:d=0.5','-c:v','libx264','-c:a','aac','-shortest',str(intro)],check=True)
        music=self.root/'local-bed.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=f=613:d=2','-af','volume=3',str(music)],check=True)
        self.metadata['music']=[str(music)]
        article=Article(News('测试媒体','SpaceX 星舰动态验证','','','',manual_id='manual:'+'a'*32),
                        '来自人工录入的完整口播素材。','manual')
        draft=news_voice.create(article,self.launch)
        footage=self.root/'动态 素材.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
            'color=green:s=320x180:r=30:d=0.5[a];color=red:s=320x180:r=30:d=1[b];'
            'color=blue:s=320x180:r=30:d=1[c];color=yellow:s=320x180:r=30:d=0.5[d];[a][b][c][d]concat=n=4:v=1:a=0',
            '-c:v','libx264',str(footage)],check=True)
        news_video.acquire(article,footage,self.token)
        asset=news_video.selected(draft['news_id'])
        news_video.trim(draft['news_id'],asset,30,120)
        draft['screen_item']['video']=news_video.selected(draft['news_id'])
        self.assertEqual(news_voice.screen_subject(draft['screen_item']['title']),'SpaceX')
        self.assertEqual(news_voice.screen_subject('深空探测器传回图像'),'')
        output=self.root/'output'
        provider=Mock(is_running=True)
        provider.synthesize.side_effect=lambda req,path,cancel_check:write_pcm(path)
        job=dict(launch=self.launch,news=[draft],articles=[article],episode=self.metadata,
                 parts=False,voice={k:self.launch[k] for k in ('tts','pronunciations','aligner')},
                 intro=intro)
        def run():
            value=episode.make_video(job,self.settings,output,provider,self.token,lambda *_:None)
            self.assertFalse(value['failed'],value['failed'])
            return Path(value['paths'][0])
        first=run()
        self.assertEqual(list(output.iterdir()),[first])
        self.assertEqual(first.name,'SpaceWeekly_2026-10-30_001.mp4')
        infos=probe(first,shutil.which('ffprobe'))
        self.assertEqual([s['codec_name'] for s in infos],['h264','aac','mov_text'])
        self.assertEqual((infos[0]['width'],infos[0]['height']),(1920,1080))
        self.assertEqual(infos[0]['r_frame_rate'],'60/1')
        timeline_path=next((self.root/'cache/roundup_playback').glob('*/timeline-*.json'))
        timeline=json.loads(timeline_path.read_text(encoding='utf-8'))
        self.assertEqual([row['id'] for row in timeline['sections']],['intro','episode-opening',
            self.launch['id'],'news-transition',draft['id'],'episode-closing'])
        self.assertEqual(timeline['lead_samples'],12000)
        with wave.open(str(timeline_path.parent/'narration.wav'),'rb') as pcm:
            self.assertTrue(any(pcm.readframes(12000)))
        transition=next(row for row in timeline['segments'] if row['segment_id']=='transition')
        news_start=next(row['start_sample'] for row in timeline['sections'] if row['id']==draft['id'])
        self.assertEqual(news_start-transition['speech_end_sample'],36000)
        def pixel(second,x,y):
            png=subprocess.check_output(['ffmpeg','-v','error','-ss',f'{second:.6f}','-i',str(first),
                                         '-frames:v','1','-c:v','png','-f','image2pipe','-'])
            return Image.open(BytesIO(png)).convert('RGB').getpixel((x,y))
        origin=news_start/timeline['sample_rate']
        import math
        from array import array
        pcm=array('h',subprocess.check_output(['ffmpeg','-v','error','-ss',str(origin+.65),'-i',str(first),
                    '-t','0.2','-map','0:a:0','-ar','24000','-ac','1','-f','s16le','-']))
        tone=2*abs(sum(value*complex(math.cos(2*math.pi*613*i/24000),math.sin(2*math.pi*613*i/24000))
                       for i,value in enumerate(pcm)))/len(pcm)/32768
        self.assertGreater(tone,.009)
        self.assertLess(tone,.02)
        before=pixel(origin+.08,900,280)
        rising=pixel(origin+.55,900,280)
        red=pixel(origin+.89,900,280)
        blue=pixel(origin+1.35,900,280)
        self.assertLess(before[0],rising[0])
        self.assertLess(rising[0],red[0])
        self.assertGreater(red[0],220)
        self.assertGreater(blue[2],220)
        self.assertGreater(pixel(origin+2.2,900,280)[2],220)
        lower_red=pixel(origin+.89,1780,880)
        lower_blue=pixel(origin+1.35,1780,880)
        self.assertGreater(lower_red[0],lower_blue[0]+6)
        self.assertGreater(lower_blue[2],lower_red[2]+6)
        self.assertEqual(pixel(origin+.89,1780,942),pixel(origin+1.35,1780,942))
        self.assertEqual(pixel(origin+1.35,100,920),pixel(origin+.89,100,920))
        self.assertLess(pixel(origin+1.35,100,920)[2],100)
        self.assertTrue(draft['segments'][0]['spoken'])
        calls=provider.synthesize.call_count
        self.assertEqual(run(),first)
        self.assertEqual(provider.synthesize.call_count,calls)
        receipts=set((self.root/'cache/episode-visuals').glob('*/clip.json'))
        self.metadata['music_settings']={'music_volume':26}
        self.assertNotEqual(run(),first)
        self.assertEqual(provider.synthesize.call_count,calls)
        self.assertEqual(set((self.root/'cache/episode-visuals').glob('*/clip.json')),receipts)
        draft['screen_item']['summary']='仅修改屏幕摘要。'
        self.assertNotEqual(run(),first)
        self.assertEqual(provider.synthesize.call_count,calls)
        # The changed news frame also becomes the outgoing picture in the next dissolve.
        self.assertEqual(len(set((self.root/'cache/episode-visuals').glob('*/clip.json'))-receipts),2)
        self.assertEqual(self.metadata['number'],43)
        subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(first),'-f','null','-'],check=True)
