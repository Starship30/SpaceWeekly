import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from i18n import set_language
from models.video_download import VideoInfo,VideoFormat,VideoSearchResults
from services import video_download as video
from services.cancellation import CancellationToken,TaskCancelledError
from ui.video_download_dialog import VideoDownloadDialog

URL='https://www.bilibili.com/video/BV1wgHm6tE4J'
WINDOW=video.mission_date_range(dict(published='2026-10-09T18:00:00+08:00'))
FMT=VideoFormat('360+a',360,'avc1',True)


def candidate(name='candidate', stamp=None):
    return VideoInfo('BV1wgHm6tE4J',name,URL,formats=[FMT],author_id='123',
                     published_timestamp=WINDOW[0]+1 if stamp is None else stamp)


class MissionDateTests(unittest.TestCase):
    def test_local_calendar_window_includes_next_day_and_crosses_month(self):
        begin,end=video.mission_date_range(dict(published='2026-10-08T23:00:00Z'))
        self.assertEqual(datetime.fromtimestamp(begin,video.VIDEO_TIMEZONE).isoformat(),'2026-10-09T00:00:00+08:00')
        self.assertEqual(datetime.fromtimestamp(end,video.VIDEO_TIMEZONE).isoformat(),'2026-10-11T00:00:00+08:00')
        begin,end=video.mission_date_range(dict(published='2026-12-31T08:00:00+08:00'))
        self.assertEqual(datetime.fromtimestamp(end,video.VIDEO_TIMEZONE).date().isoformat(),'2027-01-02')
        for value in ('','10.09','invalid','9999-12-31','1900-01-01'):
            with self.subTest(value=value),self.assertRaises(video.VideoDownloadError):
                video.mission_date_range(dict(published=value))

    def test_exact_boundaries_unknown_dates_and_local_html_date(self):
        infos=[candidate('start',WINDOW[0]),candidate('last',WINDOW[1]-1),
               candidate('too early',WINDOW[0]-1),candidate('too late',WINDOW[1]),
               VideoInfo('unknown','unknown',URL,published='3小时前'),
               VideoInfo('date','HTML date',URL,published='2026-10-10')]
        self.assertEqual([v.title for v in video._date_items(infos,WINDOW)],['start','last','HTML date'])
        self.assertEqual(video._date_items(infos,None),infos)
        self.assertEqual(video.parse_up_ids('3745040463563289'),['3745040463563289'])

    def test_search_dates_display_in_same_timezone_as_mission_window(self):
        stamp=datetime.fromisoformat('2026-10-08T23:00:00+00:00').timestamp()
        info=video._search_items([dict(bvid='BV1wgHm6tE4J',title='video',pubdate=stamp)])[0]
        self.assertEqual(info.published,'2026-10-09')
        self.assertEqual(video._date_items([info],WINDOW),[info])

    def test_api_receives_range_and_locally_rejects_outside_records(self):
        def get(session,url,cancel,**kwargs):
            if url.endswith('/search/type'):
                params=kwargs['params']
                self.assertEqual(params['pubtime_begin_s'],WINDOW[0])
                self.assertEqual(params['pubtime_end_s'],WINDOW[1]-1)
                return json.dumps(dict(code=0,data=dict(result=[
                    dict(bvid='BV1wgHm6tE4J',title='match',pubdate=WINDOW[1]-1),
                    dict(bvid='BV1xx411c7mD',title='outside',pubdate=WINDOW[1]),
                    dict(bvid='BV1234567890',title='unknown')])))
            return ''
        with patch.object(video,'_get',side_effect=get):
            result=video.search_videos('mission',date_range=WINDOW)
        self.assertEqual([v.title for v in result.items],['match'])
        self.assertIn('pubtime_end_s='+str(WINDOW[1]-1),video.search_page_url('mission',date_range=WINDOW))

    def test_explicit_empty_api_without_result_is_distinct_from_challenge(self):
        for data, is_empty in [(dict(numResults=0,numPages=0),True),
                               (dict(numResults=0,numPages=0,v_voucher='challenge'),False),
                               (dict(numResults=0),False)]:
            with patch.object(video,'_get',return_value=json.dumps(dict(code=0,data=data))):
                if is_empty:
                    self.assertEqual(video._global_rows(None,'mission','asc',lambda:None,date_range=WINDOW),[])
                else:
                    with self.assertRaises(video.VideoDownloadError):
                        video._global_rows(None,'mission','asc',lambda:None,date_range=WINDOW)

    def test_up_scans_past_later_page_to_match_dates(self):
        images=dict(img_url='https://i0.hdslb.com/'+'a'*32+'.png',sub_url='https://i0.hdslb.com/'+'b'*32+'.png')
        pages=[]
        def get(session,url,cancel,**kwargs):
            if url.endswith('/nav'):
                return json.dumps(dict(data=dict(wbi_img=images)))
            if '/arc/search' in url:
                page=int(kwargs['params']['pn'])
                pages.append(page)
                rows=[dict(bvid='BV'+str(i).zfill(10),title='later',mid=123,
                           created=WINDOW[1]+1000-i) for i in range(30)] if page==1 else [
                      dict(bvid='BV1wgHm6tE4J',title='match',mid=123,created=WINDOW[0]+1)]
                return json.dumps(dict(code=0,data=dict(list=dict(vlist=rows))))
            return ''
        with patch.object(video,'_get',side_effect=get):
            result=video.search_videos('mission',up_ids=['123'],date_range=WINDOW)
        self.assertEqual(pages,[1,2])
        self.assertEqual([v.title for v in result.items],['match'])

    def test_dated_up_network_failure_is_not_empty_success(self):
        with patch.object(video,'_get',side_effect=requests.ConnectionError('offline')):
            with self.assertRaises(video.VideoDownloadError) as error:
                video.search_videos('mission',up_ids=['123'],date_range=WINDOW)
        self.assertEqual(error.exception.code,'up_search')

    def test_date_search_fetches_later_pages_and_reports_cap(self):
        def get(session,url,cancel,**kwargs):
            if url.endswith('/search/type'):
                page=kwargs['params']['page']
                rows=[dict(bvid='BV'+str(page*100+i).zfill(10),title='match',pubdate=WINDOW[0]+page)
                      for i in range(20)]
                return json.dumps(dict(code=0,data=dict(result=rows)))
            return ''
        with patch.object(video,'_get',side_effect=get) as read:
            result=video.search_videos('mission',date_range=WINDOW)
        self.assertEqual(len(result.items),100)
        self.assertIn('5-page',result.warning)
        self.assertEqual(read.call_count,6)


class DemoTests(unittest.TestCase):
    def test_demo_skips_denied_candidate_prefers_highest_quality_and_downloads_one(self):
        second=candidate('second')
        highest=VideoFormat('2160+a',2160,'hevc',True)
        second.formats=[VideoFormat('180',180,'vp9'),FMT,highest,VideoFormat('720+a',720,'avc1',True)]
        results=VideoSearchResults([candidate('first'),second])
        progress,found,parsed=[],[],[]
        with patch.object(video,'search_videos',return_value=results) as search, \
             patch.object(video,'parse_video',side_effect=[video.VideoDownloadError('permission'),second]), \
             patch.object(video.shutil,'which',return_value='ffmpeg'), \
             patch.object(video,'download_video',return_value=Path('media.mp4')) as download:
            path=video.demo_download('mission',WINDOW,Path('workspace'),up_ids=['123'],
                on_progress=progress.append,on_search=found.append,on_video=parsed.append)
        self.assertEqual(path,Path('media.mp4'))
        search.assert_called_once_with('mission',unittest.mock.ANY,None,['123'],'asc',WINDOW)
        self.assertEqual(download.call_count,1)
        self.assertEqual(download.call_args.args[:2],(second,highest))
        self.assertEqual(parsed,[(second,highest)])
        self.assertEqual(found,[results])
        self.assertEqual([event.phase for event in progress],['searching','parsing','parsing'])

    def test_no_date_match_or_network_failure_never_downloads(self):
        for effect in (VideoSearchResults([]),video.VideoDownloadError('search','HTTP 412')):
            with patch.object(video,'search_videos',side_effect=effect if isinstance(effect,Exception) else None,
                              return_value=effect),patch.object(video,'download_video') as download:
                with self.assertRaises(video.VideoDownloadError):
                    video.demo_download('mission',WINDOW,Path('workspace'))
                download.assert_not_called()

    def test_parse_date_or_uploader_mismatch_never_downloads_and_tries_only_three(self):
        for parsed in (candidate(stamp=WINDOW[1]),candidate()):
            parsed.author_id='999'
            with patch.object(video,'search_videos',return_value=VideoSearchResults([candidate()]*4)), \
                 patch.object(video,'parse_video',return_value=parsed) as parse, \
                 patch.object(video,'download_video') as download:
                with self.assertRaises(video.VideoDownloadError) as error:
                    video.demo_download('mission',WINDOW,Path('workspace'),up_ids=['123'])
                self.assertEqual(error.exception.code,'demo_candidates')
                self.assertEqual(parse.call_count,3)
                download.assert_not_called()

    def test_cancel_after_search_does_not_parse_or_download(self):
        token=CancellationToken()
        with patch.object(video,'search_videos',return_value=VideoSearchResults([candidate()])), \
             patch.object(video,'parse_video') as parse,patch.object(video,'download_video') as download:
            with self.assertRaises(TaskCancelledError):
                video.demo_download('mission',WINDOW,Path('workspace'),cancel_check=token.check,
                                    on_search=lambda _:token.cancel())
            parse.assert_not_called()
            download.assert_not_called()


class BatchDemoTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=Path('build').resolve())
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.items = [
            dict(video_type='launch', summary='later mission', published='2026-10-10T01:00:00+08:00'),
            dict(video_type='news', title='must not download', published='2026-10-09'),
            dict(video_type='launch', summary='earlier mission', published='2026-10-09T01:00:00+08:00'),
            dict(video_type='launch', title='invalid mission', published='')
        ]

    def test_all_launches_own_dates_failures_continue_and_folder_is_shared(self):
        started, finished = [], []
        def run(keyword, window, workspace, *args, output_directory=None, **options):
            if keyword == 'earlier mission':
                raise video.VideoDownloadError('date_no_match')
            path = output_directory/'original title.mp4'
            path.write_bytes(b'completed fixture')
            return path
        with patch.object(video, 'demo_download', side_effect=run) as download:
            results = video.demo_download_tasks(self.items, self.root, up_ids=['123'],
                        on_task_started=started.append, on_task_finished=finished.append)
        self.assertEqual([row['keyword'] for row in results],
                         ['earlier mission', 'later mission', 'invalid mission'])
        self.assertEqual([row['error'].code if row['error'] else None for row in results],
                         ['date_no_match', None, 'task_date'])
        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args_list[0].args[1], WINDOW)
        self.assertEqual(download.call_args_list[1].args[1],
                         video.mission_date_range(self.items[0]))
        self.assertEqual(download.call_args_list[0].args[3], ['123'])
        self.assertEqual({row['directory'] for row in results}, {results[1]['path'].parent})
        self.assertRegex(results[1]['path'].parent.name, r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(len(started), 3)
        self.assertEqual(finished, results)

    def test_repeated_batches_number_dates_without_moving_completed_video(self):
        def run(keyword, window, workspace, *args, output_directory=None, **options):
            path = output_directory/'original.mp4'
            path.write_bytes(keyword.encode())
            return path
        with patch.object(video, 'demo_download', side_effect=run):
            first = video.demo_download_tasks(self.items[:1], self.root)[0]
            second = video.demo_download_tasks(self.items[:1], self.root)[0]
        self.assertEqual(second['directory'].name, first['directory'].name+' (2)')
        self.assertEqual(first['path'].read_bytes(), b'later mission')
        self.assertEqual(second['path'].read_bytes(), b'later mission')

    def test_cancel_after_first_task_preserves_result_and_stops_next(self):
        token = CancellationToken()
        finished = []
        def complete(value):
            finished.append(value)
            token.cancel()
        def run(keyword, window, workspace, *args, output_directory=None, **options):
            path = output_directory/'original.mp4'
            path.write_bytes(b'keep')
            return path
        with patch.object(video, 'demo_download', side_effect=run) as download:
            with self.assertRaises(TaskCancelledError):
                video.demo_download_tasks(self.items[:3], self.root, cancel_check=token.check,
                                          on_task_finished=complete)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['path'].read_bytes(), b'keep')

    def test_no_launches_no_directory_or_network(self):
        with patch.object(video, 'demo_download') as download:
            with self.assertRaises(video.VideoDownloadError):
                video.demo_download_tasks([self.items[1]], self.root)
        download.assert_not_called()
        self.assertFalse((self.root/'video-downloads').exists())


class DemoDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        self.scratch=tempfile.TemporaryDirectory(dir=Path('build').resolve())
        self.root=Path(self.scratch.name)
        self.dialog=VideoDownloadDialog(self.root,[dict(video_type='launch',title='rocket',summary='mission',
                                                       published='2026-10-09T08:00:00+08:00')])
        self.app.processEvents()

    def tearDown(self):
        self.dialog.reject()
        self.wait()
        self.scratch.cleanup()

    def wait(self):
        deadline=time.monotonic()+5
        while self.dialog.worker is not None and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.dialog.worker)

    def test_default_window_and_unchecked_range_allow_manual_search(self):
        self.assertTrue(self.dialog.use_task_date.isChecked())
        self.assertIn('2026-10-09 至 2026-10-10',self.dialog.date_hint.text())
        self.dialog.rights.setChecked(True)
        self.assertTrue(self.dialog.demo.isEnabled())
        self.dialog.use_task_date.setChecked(False)
        self.assertFalse(self.dialog.demo.isEnabled())
        with patch.object(video,'search_videos',return_value=VideoSearchResults([])) as search:
            self.dialog._search()
            self.wait()
        self.assertIsNone(search.call_args.kwargs['date_range'])

    def test_invalid_extreme_task_date_does_not_crash_dialog(self):
        dialog=VideoDownloadDialog(self.root,[dict(video_type='launch',title='rocket',summary='mission',
                                                  published='9999-12-31T23:00:00Z')])
        dialog.rights.setChecked(True)
        self.assertTrue(dialog.demo.isEnabled())
        self.assertIn('published',dialog.date_hint.text())
        dialog.reject()
        self.app.processEvents()

    def test_demo_worker_stages_remain_responsive_and_show_final_file(self):
        self.assertFalse(self.dialog.demo.isEnabled())
        self.dialog._demo()
        self.assertIn('权限',self.dialog.status.text())
        self.dialog.rights.setChecked(True)
        ticks,threads=[],[]
        heartbeat=threading.Event()
        timer=QTimer()
        timer.setInterval(5)
        def tick():
            if self.dialog.worker is not None:
                ticks.append(1)
                heartbeat.set()
        timer.timeout.connect(tick)
        def run(**options):
            threads.append(threading.get_ident())
            self.assertEqual(len(options['items']),1)
            folder=self.root/'video-downloads'/'2026-10-11'
            folder.mkdir(parents=True)
            options['on_task_started']((1,1,options['items'][0],'mission',folder))
            options['on_search'](VideoSearchResults([candidate()]))
            options['on_video']((candidate(),FMT))
            self.assertTrue(heartbeat.wait(3))
            outcome=dict(index=1,total=1,keyword='mission',directory=folder,path=folder/'original.mp4',error=None)
            options['on_task_finished'](outcome)
            return [outcome]
        timer.start()
        with patch.object(video,'demo_download_tasks',side_effect=run) as demo:
            self.dialog.demo.trigger()
            self.dialog._demo()
            self.wait()
        timer.stop()
        self.assertEqual(demo.call_count,1)
        self.assertNotEqual(threads[0],threading.get_ident())
        self.assertGreaterEqual(len(ticks),1)
        self.assertEqual(self.dialog.results.count(),1)
        self.assertEqual(self.dialog.formats.currentData(),FMT)
        self.assertEqual(self.dialog.downloaded_file,self.root/'video-downloads'/'2026-10-11'/'original.mp4')
        self.assertEqual(self.dialog.batch_progress.value(),1)
        self.assertIn('mission',self.dialog.batch_log.toPlainText())
        self.assertIn('Demo 完成',self.dialog.status.text())
        self.assertEqual(self.dialog.progress_bar.value(),100)

    def test_current_news_or_edited_keyword_does_not_limit_batch(self):
        self.dialog.snapshot_items += [
            dict(video_type='launch', summary='second mission',published='2026-10-10'),
            dict(video_type='news',title='news item',published='2026-10-08')]
        self.dialog.include_news.setChecked(True)
        self.dialog.tasks.setCurrentIndex(0)
        self.dialog.keyword.setEditText('manual edited query')
        self.dialog.rights.setChecked(True)
        self.assertTrue(self.dialog.demo.isEnabled())
        with patch.object(video,'demo_download',side_effect=video.VideoDownloadError('date_no_match')) as run:
            self.dialog.demo.trigger()
            self.wait()
        self.assertEqual(run.call_count,2)
        self.assertEqual({call.args[0] for call in run.call_args_list}, {'mission','second mission'})
        self.assertEqual(self.dialog.batch_progress.value(),2)
        self.assertEqual(len(self.dialog.batch_outcomes),2)
        self.assertIn('成功 0 项',self.dialog.status.text())
        self.assertNotIn('Demo 完成',self.dialog.status.text())
        self.assertFalse(self.dialog.open_file.isEnabled())

    def test_last_task_failure_does_not_show_download_progress_as_success(self):
        self.dialog.rights.setChecked(True)
        saved = self.root/'original.mp4'
        saved.write_bytes(b'completed')
        self.dialog.snapshot_items.append(dict(video_type='launch',summary='second',published='2026-10-10'))
        def run(keyword, window, workspace, *args, output_directory=None, **options):
            if keyword == 'second':
                raise video.VideoDownloadError('date_no_match')
            return saved
        with patch.object(video,'demo_download',side_effect=run):
            self.dialog._demo()
            self.wait()
        self.assertEqual(self.dialog.batch_progress.value(),2)
        self.assertEqual(self.dialog.progress_bar.value(),0)
        self.assertEqual(self.dialog.downloaded_file,saved)
        self.assertIn('成功 1 项，失败 1 项',self.dialog.status.text())
        self.assertTrue(self.dialog.open_file.isEnabled())

    def test_cancelled_batch_keeps_completed_file_available(self):
        self.dialog.rights.setChecked(True)
        saved = self.root/'original.mp4'
        saved.write_bytes(b'completed')
        def run(**options):
            folder = saved.parent
            options['on_task_started']((1,2,options['items'][0],'mission',folder))
            options['on_task_finished'](dict(index=1,total=2,keyword='mission',directory=folder,
                                                path=saved,error=None))
            raise TaskCancelledError()
        self.dialog.snapshot_items.append(dict(video_type='launch',summary='second',published='2026-10-10'))
        with patch.object(video,'demo_download_tasks',side_effect=run):
            self.dialog._demo()
            self.wait()
        self.assertEqual(self.dialog.downloaded_file,saved)
        self.assertTrue(self.dialog.open_file.isEnabled())
        self.assertIn('已保存 1 项',self.dialog.status.text())
        self.assertEqual(saved.read_bytes(),b'completed')

    def test_demo_failure_and_cancellation_never_show_completed(self):
        self.dialog.rights.setChecked(True)
        for error in (video.VideoDownloadError('date_no_match'),TaskCancelledError()):
            with patch.object(video,'demo_download_tasks',side_effect=error):
                self.dialog._demo()
                self.wait()
            self.assertIsNone(self.dialog.downloaded_file)
            self.assertFalse(self.dialog.open_file.isEnabled())
            self.assertNotIn('Demo 完成',self.dialog.status.text())


if __name__=='__main__':
    unittest.main()
