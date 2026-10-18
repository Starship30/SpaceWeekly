import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests

from models.video_download import VideoFormat, VideoInfo
from services.cancellation import CancellationToken, TaskCancelledError
from services import video_download as video

URL = 'https://www.bilibili.com/video/BV1xx411c7mD'
CHECK_PERMISSION = video._check_saving_allowed
CARD = '''<div class="bili-video-card"><a href="//www.bilibili.com/video/BV1xx411c7mD">
<h3 title="星舰发射">星舰</h3></a><img data-src="//i0.hdslb.com/test.jpg">
<span class="up-name">作者</span><span class="duration">01:23</span>
<span class="pubdate">2026-10-11</span></div>'''


class VideoDownloadTests(unittest.TestCase):
    def setUp(self):
        build = Path(__file__).resolve().parents[1]/'build'
        build.mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=build)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        permission = patch.object(video, '_check_saving_allowed')
        permission.start()
        self.addCleanup(permission.stop)
        self.fmt = VideoFormat('30016+30232', 360, 'avc1', True)
        self.info = VideoInfo('BV1xx411c7mD', '../../CON:<星舰>?', URL, formats=[self.fmt])

    def test_valid_urls_preserve_part_and_remove_tracking(self):
        for value, expected in [
            (URL+'/?p=2&token=secret#share', URL+'?p=2'),
            ('http://m.bilibili.com/video/av123/', 'https://www.bilibili.com/video/av123'),
            (URL, URL),
        ]:
            self.assertEqual(video.normalize_video_url(value), expected)

    def test_invalid_urls(self):
        for url in ['file:///x', 'https://bilibili.com.evil/video/BV1xx411c7mD',
            'https://www.bilibili.com@evil.test/video/BV1xx411c7mD',
            'https://user:password@www.bilibili.com/video/BV1xx411c7mD',
            'https://www.bilibili.com:8080/video/BV1xx411c7mD',
            'https://www.bilibili.com:bad/video/BV1xx411c7mD',
            'https://www.bilibili.com/video/BV123', 'https://www.bilibili.com/bangumi/play/ep1',
            URL+'?p=-1', URL+'?p=2&p=3', URL+'?p=0', URL+'/../etc']:
            with self.subTest(url=url), self.assertRaises(video.VideoDownloadError):
                video.normalize_video_url(url)

    def test_short_link_never_follows_external_redirect(self):
        with patch.object(video.requests, 'Session') as session:
            response = session.return_value.__enter__.return_value.get.return_value.__enter__.return_value
            response.status_code = 302
            response.headers = {'Location': 'http://127.0.0.1/private'}
            with self.assertRaises(video.VideoDownloadError):
                video.resolve_video_url('https://b23.tv/abc123')
            session.return_value.__enter__.return_value.get.assert_called_once()
            response.headers = {'Location': URL+'?p=3&tracking=1'}
            self.assertEqual(video.resolve_video_url('https://b23.tv/abc123'), URL+'?p=3')

    def test_thumbnail_hosts_and_keyword_encoding(self):
        self.assertEqual(video.thumbnail_url('//i0.hdslb.com/a.jpg?secret=1'), 'https://i0.hdslb.com/a.jpg')
        self.assertFalse(video.thumbnail_url('http://127.0.0.1/x'))
        self.assertFalse(video.thumbnail_url('https://hdslb.com.evil/x'))
        self.assertIn('keyword=A+%26+B', video.search_page_url('A & B'))

    def test_search_strips_html_and_returns_real_fields(self):
        data = dict(code=0, data=dict(result=[dict(type='video', bvid=self.info.id,
            title='<em>星舰</em> 发射', author='NASA', duration='01:23',
            pic='//i0.hdslb.com/a.jpg', pubdate=1791676800)]))
        with patch.object(video, '_get', side_effect=['home', json.dumps(data)]):
            result = video.search_videos('星舰')
        self.assertEqual(result.method, 'api')
        item = result.items[0]
        self.assertEqual((item.title, item.author, item.duration, item.platform),
                         ('星舰 发射', 'NASA', '01:23', 'Bilibili'))
        self.assertEqual(item.url, URL)
        self.assertEqual(item.thumbnail, 'https://i0.hdslb.com/a.jpg')
        self.assertTrue(item.published)

    def test_search_page_fallback_and_deduplication(self):
        with patch.object(video, '_get', side_effect=['home', '{"code":-412}', CARD+CARD]):
            result = video.search_videos('星舰')
        self.assertEqual(result.method, 'page')
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].title, '星舰发射')

    def test_challenge_network_failure_and_empty_api_are_distinct(self):
        for data in ['{"code":-412,"message":"blocked"}', '{"code":0,"data":{"v_voucher":"x"}}', '{}']:
            with patch.object(video, '_get', side_effect=['home', data, '<html>verification</html>']):
                with self.assertRaises(video.VideoDownloadError) as error:
                    video.search_videos('星舰')
                self.assertEqual(error.exception.code, 'search')
        with patch.object(video, '_get', side_effect=requests.ConnectionError('offline')):
            with self.assertRaises(video.VideoDownloadError):
                video.search_videos('星舰')
        with patch.object(video, '_get', side_effect=['home', '{"code":0,"data":{"result":[]}}']):
            self.assertEqual(video.search_videos('星舰').items, [])

    def test_search_cancel_does_not_attempt_fallback(self):
        with patch.object(video, '_get', side_effect=TaskCancelledError()) as get:
            with self.assertRaises(TaskCancelledError):
                video.search_videos('星舰')
            self.assertEqual(get.call_count, 1)

    def test_missing_optional_dependency_and_frozen_sidecar(self):
        with patch.object(video.importlib.util, 'find_spec', return_value=None), \
                patch.object(video.shutil, 'which', return_value=None), \
                patch.object(sys, 'frozen', False, create=True):
            with self.assertRaises(video.VideoDownloadError) as error:
                video.backend_command()
            self.assertEqual(error.exception.code, 'dependency')
        executable = self.root/'yt-dlp.exe'
        executable.touch()
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(self.root/'SpaceWeekly.exe')):
            self.assertEqual(video.backend_command(), [str(executable)])

    def test_frozen_embedded_backend_and_sidecar_override(self):
        embedded = self.root/'runtime'/'video-backend'/'yt-dlp.exe'
        embedded.parent.mkdir(parents=True)
        embedded.touch()
        sidecar = self.root/'yt-dlp.exe'
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, '_MEIPASS', str(self.root/'runtime'), create=True), \
                patch.object(sys, 'executable', str(self.root/'SpaceWeekly.exe')), \
                patch.object(video.shutil, 'which', return_value=None):
            self.assertEqual(video.backend_command(), [str(embedded)])
            sidecar.touch()
            self.assertEqual(video.backend_command(), [str(sidecar)])
            sidecar.unlink()
            embedded.unlink()
            with self.assertRaises(video.VideoDownloadError) as error:
                video.backend_command()
            self.assertEqual(error.exception.code, 'dependency')

    def test_platform_saving_permission_is_required_and_paid_is_rejected(self):
        for rights, allowed in [({'download': 1}, True), ({'download': 0}, False),
                                ({'download': 1, 'pay': 1}, False), ({}, False)]:
            payload = json.dumps(dict(code=0, data=dict(rights=rights)))
            with patch.object(video, '_get', return_value=payload):
                if allowed:
                    CHECK_PERMISSION(URL, lambda: None)
                else:
                    with self.assertRaises(video.VideoDownloadError) as error:
                        CHECK_PERMISSION(URL, lambda: None)
                    self.assertEqual(error.exception.code, 'permission')

    def test_formats_exclude_drm_and_pair_audio(self):
        formats = video._formats([
            dict(format_id='a', ext='m4a', vcodec='none', acodec='aac', abr=128, url='audio'),
            dict(format_id='v', ext='mp4', vcodec='avc1', acodec='none', height=360, url='video'),
            dict(format_id='drm', ext='mp4', vcodec='avc1', acodec='aac', has_drm=True, url='x'),
            dict(format_id='bad/../../', ext='mp4', vcodec='avc1', url='x'),
            dict(format_id='muxed', ext='mp4', vcodec='avc1', acodec='aac', height=720, url='x'),
        ])
        self.assertEqual([x.selector for x in formats], ['muxed', 'v+a'])
        self.assertFalse(formats[0].requires_merge)
        self.assertTrue(formats[1].requires_merge)

    def test_preferred_format_chooses_highest_resolution_and_respects_ffmpeg(self):
        low = VideoFormat('360', 360, 'avc1')
        hd = VideoFormat('1080', 1080, 'avc1')
        uhd = VideoFormat('2160+a', 2160, 'hevc', True)
        with patch.object(video.shutil, 'which', return_value='ffmpeg'):
            self.assertEqual(video.preferred_format([low, uhd, hd]), uhd)
            self.assertEqual(video.preferred_format([VideoFormat('unknown', 0, 'avc1'), hd]), hd)
            self.assertEqual(video.preferred_format([VideoFormat('1080hevc',1080,'hevc'),hd]), hd)
        with patch.object(video.shutil, 'which', return_value=None):
            self.assertEqual(video.preferred_format([low, uhd, hd]), hd)
            self.assertIsNone(video.preferred_format([uhd]))
        self.assertIsNone(video.preferred_format([]))

    def test_parse_metadata_and_no_download_command(self):
        payload = dict(id=self.info.id, title='测试', duration=5, upload_date='20261011',
            formats=[dict(format_id='muxed', ext='mp4', vcodec='avc1', acodec='aac', url='signed-url')])
        def run(command, cancel_check, on_line, **kwargs):
            self.assertIn('--skip-download', command)
            self.assertIn('--ignore-config', command)
            on_line(json.dumps(payload))
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video, '_run_process', side_effect=run):
            info = video.parse_video(URL)
            self.assertEqual(info.title, '测试')
            self.assertEqual(info.formats[0].selector, 'muxed')
            payload['has_drm'] = True
            with self.assertRaises(video.VideoDownloadError):
                video.parse_video(URL)

    def _fake_download(self, command, cancel_check, on_line, **kwargs):
        target = Path(command[command.index('-o')+1].replace('%(ext)s', 'mp4'))
        target.write_bytes(b'\x00\x00\x00\x20ftypisom'+b'0'*100)
        on_line('SW_PROGRESS:'+json.dumps(dict(total_bytes=100, downloaded_bytes=100, speed=1024, eta=0)))
        on_line('SW_FILE:'+json.dumps(str(target)))

    def test_workspace_metadata_progress_and_duplicates(self):
        progress = []
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_verify_file'), \
                patch.object(video, '_run_process', side_effect=self._fake_download) as run:
            first = video.download_video(self.info, self.fmt, self.root, on_progress=progress.append)
            content = first.read_bytes()
            second = video.download_video(self.info, self.fmt, self.root)
            self.assertEqual(first, second)
            self.assertEqual(run.call_count, 1)
            changed = VideoFormat('30032+30232', 480, 'avc1', True)
            self.info.formats.append(changed)
            third = video.download_video(self.info, changed, self.root)
            self.assertNotEqual(first, third)
            self.assertEqual(run.call_count, 2)
            first.unlink()
            fourth = video.download_video(self.info, self.fmt, self.root)
            self.assertNotEqual(first, fourth)
            self.assertEqual(fourth.read_bytes(), content)
        self.assertEqual(second, first)
        self.assertEqual(third.read_bytes(), content)
        self.assertTrue(first.is_relative_to(self.root/'video-downloads'))
        metadata = next(data for record in (self.root/'video-downloads').glob('*/metadata.json')
                        if (data := json.loads(record.read_text(encoding='utf-8')))['file_path'] == str(first))
        self.assertEqual(metadata['status'], 'completed')
        self.assertEqual(metadata['original_url'], URL)
        self.assertEqual(metadata['file_path'], str(first))
        self.assertTrue(metadata['downloaded_at'])
        self.assertEqual(progress[0].percent, 99)
        self.assertEqual(progress[-1].phase, 'completed')
        self.assertEqual(progress[-1].percent, 100)

    def test_batch_folder_titles_collisions_and_cache_copy_preserve_old_files(self):
        root = video._download_root(self.root)
        first_folder = video._new_download_directory(root)
        second_folder = video._new_download_directory(root)
        self.assertRegex(first_folder.name, r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(second_folder.name, first_folder.name+' (2)')
        self.info.title = '星舰: 原视频标题'
        sentinel = first_folder/(video.safe_filename(self.info.title)+'.mp4')
        sentinel.write_bytes(b'existing user video')
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_verify_file'), \
                patch.object(video, '_run_process', side_effect=self._fake_download) as run:
            saved = video.download_video(self.info, self.fmt, self.root, output_directory=first_folder)
            self.assertEqual(saved.name, '星舰_ 原视频标题 (2).mp4')
            self.assertEqual(saved.parent, first_folder)
            copied = video.download_video(self.info, self.fmt, self.root, output_directory=second_folder)
            self.assertEqual(copied.name, '星舰_ 原视频标题.mp4')
            self.assertEqual(copied.parent, second_folder)
            self.assertEqual(saved.read_bytes(), copied.read_bytes())
            same = video.download_video(self.info, self.fmt, self.root, output_directory=second_folder)
            self.assertEqual(same, copied)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(sentinel.read_bytes(), b'existing user video')
        self.assertTrue(saved.is_file())

    def test_completed_legacy_video_is_copied_to_named_date_folder(self):
        old = self.root/'video-downloads'/'old-job'
        old.mkdir(parents=True)
        media = old/'media.mp4'
        media.write_bytes(b'\x00\x00\x00\x20ftypisom'+b'0'*100)
        video._save_metadata(old, dict(status='completed', original_url=URL, format_id=self.fmt.selector,
                                      file_path=str(media), title=self.info.title))
        content = media.read_bytes()
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_verify_file'), patch.object(video, '_run_process') as run:
            saved = video.download_video(self.info, self.fmt, self.root)
            run.assert_not_called()
        self.assertRegex(saved.parent.name, r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(saved.name, video.safe_filename(self.info.title)+'.mp4')
        self.assertEqual(saved.read_bytes(), content)
        self.assertEqual(media.read_bytes(), content)

    def test_metadata_failure_after_publication_removes_only_current_file(self):
        original = video._save_metadata
        def save(directory, data):
            if data['status'] == 'completed':
                raise OSError('disk full')
            original(directory, data)
        root = video._download_root(self.root)
        folder = video._new_download_directory(root)
        old = folder/(video.safe_filename(self.info.title)+'.mp4')
        old.write_bytes(b'keep existing')
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_verify_file'), \
                patch.object(video, '_save_metadata', side_effect=save), \
                patch.object(video, '_run_process', side_effect=self._fake_download):
            with self.assertRaises(OSError):
                video.download_video(self.info, self.fmt, self.root, output_directory=folder)
        self.assertEqual(list(folder.glob('*.mp4')), [old])
        self.assertEqual(old.read_bytes(), b'keep existing')

    def test_explicit_output_directory_must_stay_in_workspace(self):
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'):
            with self.assertRaises(video.VideoDownloadError) as error:
                video.download_video(self.info, self.fmt, self.root, output_directory=self.root.parent)
        self.assertEqual(error.exception.code, 'path')

    def test_cached_download_rejects_corrupt_metadata_and_files(self):
        directory = self.root/'video-downloads'/'invalid'
        directory.mkdir(parents=True)
        record = directory/'metadata.json'
        record.write_text('[]', encoding='utf-8')
        self.assertIsNone(video._completed_download(directory.parent, URL, self.fmt, lambda: None))
        record.write_text(json.dumps(dict(status='completed', original_url=URL, format_id=self.fmt.selector)), encoding='utf-8')
        (directory/'media.mp4').write_bytes(b'not a video')
        self.assertIsNone(video._completed_download(directory.parent, URL, self.fmt, lambda: None))

    def test_failed_or_cancelled_download_cleans_only_new_job(self):
        existing = self.root/'video-downloads'/'existing'
        existing.mkdir(parents=True)
        (existing/'media.mp4').write_bytes(b'keep')
        for failure in [video.VideoDownloadError('backend', 'network failed'), TaskCancelledError()]:
            def run(command, cancel_check, on_line, **kwargs):
                self._fake_download(command, cancel_check, on_line)
                raise failure
            with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                    patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                    patch.object(video, '_run_process', side_effect=run):
                with self.assertRaises(type(failure)):
                    video.download_video(self.info, self.fmt, self.root)
        self.assertEqual((existing/'media.mp4').read_bytes(), b'keep')
        metadata = [json.loads(x.read_text(encoding='utf-8'))
                    for x in (self.root/'video-downloads').glob('*/metadata.json')]
        self.assertEqual({x['status'] for x in metadata}, {'failed', 'cancelled'})
        self.assertEqual(len(list((self.root/'video-downloads').glob('*/media.mp4'))), 1)

    def test_failure_record_write_error_still_cleans_partial_media(self):
        original = video._save_metadata
        calls = []
        def save(directory, data):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('disk full')
            original(directory, data)
        def run(command, cancel_check, on_line, **kwargs):
            self._fake_download(command, cancel_check, on_line)
            raise TaskCancelledError()
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_save_metadata', side_effect=save), \
                patch.object(video, '_run_process', side_effect=run), \
                self.assertLogs(video.logger, level='WARNING'):
            with self.assertRaises(TaskCancelledError):
                video.download_video(self.info, self.fmt, self.root)
        self.assertFalse(list((self.root/'video-downloads').glob('*/media*')))

    def test_merge_phase_waits_for_both_streams(self):
        events = []
        def run(command, cancel_check, on_line, **kwargs):
            self._fake_download(command, cancel_check, on_line)
            for filename in ('video.mp4', 'audio.m4a'):
                on_line('SW_PROGRESS:'+json.dumps(dict(status='finished', filename=filename,
                    total_bytes=100, downloaded_bytes=100)))
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_verify_file'), \
                patch.object(video, '_run_process', side_effect=run):
            video.download_video(self.info, self.fmt, self.root, on_progress=events.append)
        self.assertIn('merging', [event.phase for event in events])
        self.assertEqual(events[-1].phase, 'completed')

    def test_zero_exit_without_output_is_failure(self):
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value='ffmpeg'), \
                patch.object(video, '_run_process'):
            with self.assertRaises(video.VideoDownloadError) as error:
                video.download_video(self.info, self.fmt, self.root)
            self.assertEqual(error.exception.code, 'output')
        metadata = json.loads(next((self.root/'video-downloads').glob('*/metadata.json')).read_text())
        self.assertEqual(metadata['status'], 'failed')

    def test_ffmpeg_missing_is_early_and_no_files(self):
        with patch.object(video, 'backend_command', return_value=['yt-dlp']), \
                patch.object(video.shutil, 'which', return_value=None):
            with self.assertRaises(video.VideoDownloadError) as error:
                video.download_video(self.info, self.fmt, self.root)
        self.assertEqual(error.exception.code, 'ffmpeg')
        self.assertFalse((self.root/'video-downloads').exists())

    def test_filename_and_output_path_safety(self):
        for value in ['CON', 'LPT1.txt', '../../x', 'a<>:"/\\|?*b', '...', 'x'*200]:
            name = video.safe_filename(value)
            self.assertFalse(any(char in name for char in '<>:"/\\|?*'))
            self.assertLessEqual(len(name), 166)
        with self.assertRaises(video.VideoDownloadError):
            video._verify_file(self.root.parent/'outside.mp4', self.root, lambda: None)
        with self.assertRaises(video.VideoDownloadError):
            video.download_video(self.info, VideoFormat('../../x', 0, ''), self.root)

    def test_process_reads_stdout_and_reports_nonzero_exit(self):
        lines = []
        video._run_process([sys.executable, '-c', "print('hello')"], lambda: None, lines.append, timeout=5)
        self.assertEqual(lines, ['hello'])
        with self.assertRaises(video.VideoDownloadError) as error:
            video._run_process([sys.executable, '-c', "print('network failure'); raise SystemExit(1)"],
                               lambda: None, lines.append, timeout=5)
        self.assertIn('network failure', str(error.exception))

    def test_process_cancels_while_stdout_is_idle(self):
        token = CancellationToken()
        timer = threading.Timer(.3, token.cancel)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(TaskCancelledError):
                video._run_process([sys.executable, '-c', 'import time; time.sleep(30)'],
                                   token.check, lambda line: None)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic()-started, 5)

    def test_cancel_terminates_child_processes_too(self):
        started, completed = self.root/'child-started', self.root/'child-completed'
        child_code = (f'from pathlib import Path; import time; Path({str(started)!r}).touch(); '
                      f'time.sleep(1.5); Path({str(completed)!r}).touch()')
        parent_code = (f'import subprocess,sys,time; from pathlib import Path; '
                       f'subprocess.Popen([sys.executable,"-c",{child_code!r}], '
                       'creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)); '
                       f'flag=Path({str(started)!r}); deadline=time.monotonic()+5\n'
                       'while not flag.exists() and time.monotonic()<deadline: time.sleep(.01)\n'
                       'print("ready",flush=True); time.sleep(30)')
        token = CancellationToken()
        with self.assertRaises(TaskCancelledError):
            video._run_process([sys.executable, '-c', parent_code], token.check,
                               lambda line: token.cancel() if line == 'ready' else None, timeout=10)
        self.assertTrue(started.exists())
        time.sleep(1.7)
        self.assertFalse(completed.exists())

    def test_backend_error_redacts_urls_and_credentials(self):
        text = video.safe_error('ERROR: https://x.test/?token=secret cookie=secret SESSDATA=secret')
        self.assertNotIn('secret', text)
        self.assertNotIn('https://', text)


if __name__ == '__main__':
    unittest.main()
