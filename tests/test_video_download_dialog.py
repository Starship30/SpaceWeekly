import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton
from i18n import set_language, tr
from models.video_download import DownloadProgress, VideoFormat, VideoInfo, VideoSearchResults
from services.cancellation import TaskCancelledError
from services.video_download import VideoDownloadError
from ui.video_cards_dialog import VideoCardsDialog
from ui.video_download_dialog import VideoDownloadDialog, VideoDownloadWorker
from ui.theme import ThemeManager

URL = 'https://www.bilibili.com/video/BV1xx411c7mD'


class VideoDownloadDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        build = Path(__file__).resolve().parents[1]/'build'
        build.mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=build)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.fmt = VideoFormat('muxed', 360, 'avc1')
        self.info = VideoInfo('BV1xx411c7mD', '星舰发射', URL, 'NASA', '5', '', '20261011', formats=[self.fmt])
        self.dialog = VideoDownloadDialog(self.root,
            [dict(video_type='launch', title='长征十号', summary='载人登月测试任务', published='2026-10-11')])
        self.addCleanup(self.cleanup_dialog)

    def cleanup_dialog(self):
        self.dialog.reject()
        self.wait_worker()
        self.app.processEvents()
        set_language('zh_CN')

    def wait_worker(self):
        deadline = time.monotonic()+5
        while self.dialog.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.dialog.worker)

    def parse(self):
        self.dialog.url.setText(URL)
        with patch('ui.video_download_dialog.service.parse_video', return_value=self.info):
            self.dialog._parse()
            self.wait_worker()

    def test_json_keywords_search_selection_and_parse(self):
        self.assertEqual(self.dialog.keyword.currentText(), '载人登月测试任务')
        self.assertIn('载人登月测试任务', self.dialog.tasks.itemText(0))
        with patch('ui.video_download_dialog.service.search_videos',
                   return_value=VideoSearchResults([self.info])):
            self.dialog._search()
            self.wait_worker()
        self.assertEqual(self.dialog.results.count(), 1)
        self.dialog.results.setCurrentRow(0)
        self.assertEqual(self.dialog.url.text(), URL)
        self.parse()
        self.assertIn('星舰发射', self.dialog.details.text())
        self.assertEqual(self.dialog.formats.currentData(), self.fmt)
        self.assertFalse(self.dialog.download.isEnabled())
        self.dialog.rights.setChecked(True)
        self.assertTrue(self.dialog.download.isEnabled())
        self.dialog.url.setText(URL+'?p=2')
        self.assertIsNone(self.dialog.info)
        self.assertFalse(self.dialog.download.isEnabled())

    def test_parse_defaults_to_highest_quality_and_keeps_manual_selection(self):
        hd = VideoFormat('1080+a',1080,'avc1',True)
        highest = VideoFormat('2160+a',2160,'hevc',True)
        self.info.formats = [self.fmt,highest,hd]
        with patch('ui.video_download_dialog.shutil.which',return_value='ffmpeg'):
            self.parse()
            self.assertEqual(self.dialog.formats.currentData(),highest)
            self.dialog.rights.setChecked(True)
            with patch('ui.video_download_dialog.service.download_video',
                       return_value=self.root/'highest.mp4') as download:
                self.dialog._download()
                self.wait_worker()
            self.assertEqual(download.call_args.args[1],highest)
            self.dialog.formats.setCurrentIndex(0)
            self.assertEqual(self.dialog.formats.currentData(),self.fmt)
            self.assertTrue(self.dialog.download.isEnabled())

    def test_default_quality_skips_formats_requiring_missing_ffmpeg(self):
        hd = VideoFormat('1080',1080,'avc1')
        highest = VideoFormat('2160+a',2160,'hevc',True)
        self.info.formats = [highest,self.fmt,hd]
        with patch('ui.video_download_dialog.shutil.which',return_value=None):
            self.parse()
            self.dialog.rights.setChecked(True)
            self.assertEqual(self.dialog.formats.currentData(),hd)
            self.assertTrue(self.dialog.download.isEnabled())

    def test_qthread_keeps_event_loop_responsive_and_prevents_double_start(self):
        self.parse()
        self.dialog.rights.setChecked(True)
        heartbeat, threads = [], []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: heartbeat.append(True))
        def download(info, fmt, workspace, cancel_check, on_progress):
            threads.append(threading.get_ident())
            self.assertEqual(workspace, self.root)
            on_progress(DownloadProgress('downloading', 50, 1024*1024, 1))
            time.sleep(.15)
            return self.root/'media.mp4'
        timer.start()
        with patch('ui.video_download_dialog.service.download_video', side_effect=download) as run:
            self.dialog._download()
            self.dialog._download()
            self.wait_worker()
            self.assertEqual(run.call_count, 1)
        timer.stop()
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertGreater(len(heartbeat), 3)
        self.assertEqual(self.dialog.progress_bar.value(), 100)
        self.assertEqual(self.dialog.downloaded_file, self.root/'media.mp4')

    def test_cancel_and_close_wait_for_download_worker(self):
        self.parse()
        self.dialog.rights.setChecked(True)
        started = threading.Event()
        def slow(*args, cancel_check, on_progress):
            started.set()
            while True:
                cancel_check()
                time.sleep(.01)
        self.dialog.show()
        with patch('ui.video_download_dialog.service.download_video', side_effect=slow):
            self.dialog._download()
            self.assertTrue(started.wait(1))
            self.dialog.reject()
            self.assertTrue(self.dialog.isVisible())
            self.wait_worker()
        self.assertFalse(self.dialog.isVisible())
        self.assertIn('已取消', self.dialog.status.text())
        self.assertIsNone(self.dialog.downloaded_file)
        self.assertFalse(self.dialog.open_file.isEnabled())

    def test_search_failure_clears_stale_results(self):
        self.dialog.results.addItem('old result')
        with patch('ui.video_download_dialog.service.search_videos',
                   side_effect=VideoDownloadError('search', 'HTTP 412')):
            self.dialog._search()
            self.wait_worker()
        self.assertEqual(self.dialog.results.count(), 0)
        self.assertIn('HTTP 412', self.dialog.status.text())
        self.assertIn('搜索失败', self.dialog.status.text())
        self.assertFalse(self.dialog.download.isEnabled())

    def test_missing_optional_backend_has_localized_hint(self):
        self.dialog.url.setText(URL)
        with patch('ui.video_download_dialog.service.backend_command', side_effect=VideoDownloadError('dependency')):
            self.dialog._parse()
            self.wait_worker()
        self.assertIn('yt-dlp', self.dialog.status.text())
        self.assertIn('pip install', self.dialog.status.text())
        self.assertIsNone(self.dialog.info)

    def test_missing_ffmpeg_disables_merge_format(self):
        self.info.formats = [VideoFormat('v+a', 720, 'avc1', True)]
        with patch('ui.video_download_dialog.shutil.which', return_value=None):
            self.parse()
            self.dialog.rights.setChecked(True)
            self.assertFalse(self.dialog.download.isEnabled())
            self.assertIn('FFmpeg', self.dialog.formats.currentText())

    def test_worker_failure_and_cancel_do_not_emit_success(self):
        for error in (VideoDownloadError('backend', 'failed'), TaskCancelledError()):
            worker = VideoDownloadWorker('parse', URL, self.root)
            successes, failures, cancellations = [], [], []
            worker.result.connect(successes.append)
            worker.failed.connect(failures.append)
            worker.cancelled.connect(lambda: cancellations.append(True))
            with patch('ui.video_download_dialog.service.parse_video', side_effect=error):
                worker.run()
            self.assertEqual(successes, [])
            self.assertEqual(len(failures)+len(cancellations), 1)

    def test_existing_entry_pins_workspace_and_preserves_snapshot(self):
        with patch('ui.video_cards_dialog.workspace_path', return_value=self.root):
            cards = VideoCardsDialog(self.root/'output')
        source = Path(__file__).resolve().parents[1]/'plugins/premiere-uxp/example.premiere.json'
        original = source.read_bytes()
        cards.load_snapshot(source)
        cards.output_root = self.root/'elsewhere'
        seen = []
        def capture(dialog):
            seen.append(dialog.workspace)
            self.assertIs(cards.download_dialog, dialog)
            self.assertEqual(dialog.tasks.count(), 0)
            dialog.include_news.setChecked(True)
            self.assertTrue(dialog.keyword.currentText())
            return 0
        with patch.object(VideoDownloadDialog, 'exec', capture):
            button = next(b for b in cards.findChildren(QPushButton) if b.text() == tr('video_download.title'))
            button.click()
        self.assertEqual(seen, [self.root])
        self.assertIsNone(cards.download_dialog)
        self.assertEqual(source.read_bytes(), original)
        cards.reject()

    def test_all_translations_and_both_themes(self):
        root = Path(__file__).resolve().parents[1]
        zh = json.loads((root/'language/zh_CN.json').read_text(encoding='utf-8'))
        en = json.loads((root/'language/en_US.json').read_text(encoding='utf-8'))
        keys = {key for key in zh if key.startswith('video_download.')}
        self.assertEqual(keys, {key for key in en if key.startswith('video_download.')})
        self.assertGreater(len(keys), 40)
        theme = ThemeManager(self.app)
        for language in ('zh_CN', 'en_US'):
            set_language(language)
            for mode in ('light', 'dark'):
                theme.apply(mode)
                dialog = VideoDownloadDialog(self.root)
                dialog.show()
                self.app.processEvents()
                self.assertEqual(dialog.windowTitle(), tr('video_download.title'))
                self.assertFalse(any('video_download.' in b.text() for b in dialog.findChildren(QPushButton)))
                self.assertGreater(dialog.results.height(), 80)
                dialog.reject()


if __name__ == '__main__':
    unittest.main()
