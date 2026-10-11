import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from io import StringIO
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import requests
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from i18n import set_language, tr
from models.video_download import VideoInfo, VideoFormat
from services import bilibili_login as login, video_download as video
from services.cancellation import CancellationToken, TaskCancelledError
from ui.video_download_dialog import VideoDownloadDialog, BilibiliLoginDialog

KEY = '0123456789abcdef0123456789abcdef'
QR = dict(url='https://account.bilibili.com/h5/account-h5/auth/scan-web?key='+KEY, qrcode_key=KEY)
URL = 'https://www.bilibili.com/video/BV1wgHm6tE4J'


def cookies():
    jar = requests.cookies.RequestsCookieJar()
    jar.set('SESSDATA', 'test_session_secret', domain='.bilibili.com', path='/')
    jar.set('bili_jct', 'test_csrf_secret', domain='.bilibili.com', path='/')
    return jar


class BilibiliLoginTests(unittest.TestCase):
    def test_missing_qr_dependency_does_not_request_network(self):
        original = __import__
        def importing(name, *args, **kwargs):
            if name == 'qrcode':
                raise ImportError()
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=importing), patch.object(login, '_data') as request:
            with self.assertRaises(video.VideoDownloadError) as error:
                login.login_bilibili(CancellationToken(), lambda _: None, lambda _: None)
            self.assertEqual(error.exception.code, 'qr_dependency')
            request.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('qrcode'), 'optional qrcode not installed')
    def test_scan_confirm_checks_real_session_and_clears_temporary_cookies(self):
        session = requests.Session()
        states, images = [], []
        token = CancellationToken()
        responses = iter([QR, dict(code=86101), dict(code=86090), dict(code=0), dict(isLogin=True)])
        def request(*args, **kwargs):
            result = next(responses)
            if result == dict(code=0):
                session.cookies.update(cookies())
                session.cookies.set('foreign', 'discard', domain='.example.com', path='/')
            return result
        with patch.object(login, '_anonymous_session', return_value=session), \
                patch.object(login, '_data', side_effect=request), patch.object(token, 'wait', return_value=False):
            jar = login.login_bilibili(token, images.append, states.append)
        self.assertEqual(states, ['login_waiting', 'login_scanned'])
        self.assertTrue(images[0].startswith(b'\x89PNG'))
        self.assertEqual({c.name for c in jar}, {'SESSDATA', 'bili_jct'})
        self.assertEqual(len(session.cookies), 0)

    @unittest.skipUnless(importlib.util.find_spec('qrcode'), 'optional qrcode not installed')
    def test_expired_unconfirmed_and_cancelled_are_not_logged_in(self):
        for code, expected in [(86038, 'login_expired'), (999, 'login'), (0, 'login')]:
            with self.subTest(code=code), patch.object(login, '_data', side_effect=[QR, dict(code=code)]):
                with self.assertRaises(video.VideoDownloadError) as error:
                    login.login_bilibili(CancellationToken(), lambda _: None, lambda _: None)
                self.assertEqual(error.exception.code, expected)
        token = CancellationToken()
        with patch.object(login, '_data', return_value=QR) as request:
            with self.assertRaises(TaskCancelledError):
                login.login_bilibili(token, lambda _: token.cancel(), lambda _: None)
            self.assertEqual(request.call_count, 1)

    @unittest.skipUnless(importlib.util.find_spec('qrcode'), 'optional qrcode not installed')
    def test_qr_origin_and_response_schema_validation(self):
        for qr in [dict(url='https://evil.test/h5/qrcode/auth', qrcode_key=KEY),
                   dict(url=123, qrcode_key=[]), dict(url=QR['url'], qrcode_key='bad')]:
            with self.subTest(qr=qr), patch.object(login, '_data', return_value=qr):
                with self.assertRaises(video.VideoDownloadError):
                    login.login_bilibili(CancellationToken(), lambda _: self.fail('untrusted QR'), lambda _: None)

    def test_network_error_and_debug_log_do_not_expose_login_keys(self):
        with patch.object(login, '_get', side_effect=requests.ConnectionError('qrcode_key='+KEY)):
            with self.assertRaises(video.VideoDownloadError) as error:
                login._data(None, login.PASSPORT+'poll', CancellationToken())
            self.assertNotIn(KEY, str(error.exception))
        with self.assertLogs('urllib3.connectionpool', level=logging.DEBUG) as logs:
            logging.getLogger('urllib3.connectionpool').debug('GET /x/passport-login/web/qrcode/poll?qrcode_key=%s', KEY)
        self.assertNotIn(KEY, '\n'.join(logs.output))

    def test_backend_credentials_use_stdin_and_reject_foreign_cookie_domains(self):
        jar = cookies()
        args, opts = video._auth_options(jar)
        self.assertEqual(args, ['--config-locations', '-'])
        self.assertNotIn('test_session_secret', repr(args))
        self.assertIn('test_session_secret', opts['input_text'])
        self.assertEqual(video._backend_url(URL, jar), URL.replace('www.', ''))
        output = []
        video._run_process([sys.executable, '-c',
            "import sys; x=sys.stdin.read(); print('received' if 'test_session_secret' in x else 'missing')"],
            lambda: None, output.append, timeout=5, **opts)
        self.assertEqual(output, ['received'])
        with self.assertRaises(video.VideoDownloadError) as error:
            video._run_process([sys.executable, '-c',
                "import sys; print(sys.stdin.read()); sys.exit(1)"], lambda: None,
                lambda _: None, timeout=5, **opts)
        self.assertNotIn('test_session_secret', str(error.exception))
        self.assertNotIn('test_csrf_secret', str(error.exception))
        jar.set('foreign', 'secret', domain='.example.com', path='/')
        with self.assertRaises(video.VideoDownloadError):
            video._auth_options(jar)

    @unittest.skipUnless(importlib.util.find_spec('yt_dlp'), 'optional yt-dlp not installed')
    def test_ytdlp_stdin_config_scopes_auth_to_bilibili_api_and_not_cdn(self):
        import yt_dlp
        jar = cookies()
        args, opts = video._auth_options(jar)
        with patch('sys.stdin', StringIO(opts['input_text'])):
            parsed = yt_dlp.parse_options(video._common_args()+args+['--skip-download', video._backend_url(URL, jar)])
        with yt_dlp.YoutubeDL(parsed.ydl_opts) as backend:
            backend._apply_header_cookies(video._backend_url(URL, jar))
            self.assertIn('test_session_secret', backend.cookiejar.get_cookie_header('https://api.bilibili.com/'))
            self.assertFalse(backend.cookiejar.get_cookie_header('https://i0.hdslb.com/'))
            self.assertFalse(backend.cookiejar.get_cookie_header('https://example.com/'))


class BilibiliLoginDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        self.scratch = tempfile.TemporaryDirectory(dir=Path('build').resolve())
        self.root = Path(self.scratch.name)
        self.dialog = VideoDownloadDialog(self.root)
        self.app.processEvents()

    def tearDown(self):
        self.dialog.reject()
        self.pump(lambda: self.dialog.worker is None and self.dialog.login_dialog is None)
        self.scratch.cleanup()

    def pump(self, done):
        deadline = time.monotonic()+5
        while not done() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(done())

    def test_login_parse_logout_and_close_clear_memory_session(self):
        jar = cookies()
        with patch.object(login, 'login_bilibili', return_value=jar):
            self.dialog._login()
        self.assertIs(self.dialog.cookies, jar)
        self.assertEqual(self.dialog.login_button.text(), tr('video_download.logout'))
        info = VideoInfo('test', 'title', URL, formats=[VideoFormat('muxed', 360, 'avc1')])
        self.dialog.url.setText(URL)
        with patch.object(video, 'parse_video', return_value=info) as parse:
            self.dialog._parse()
            self.pump(lambda: self.dialog.worker is None)
        self.assertIn('cookies', parse.call_args.kwargs)
        self.assertIsNot(parse.call_args.kwargs['cookies'], jar)
        self.assertTrue(jar)
        self.dialog._login()
        self.assertFalse(jar)
        self.assertIsNone(self.dialog.info)
        self.assertIsNone(self.dialog.cookies)
        self.dialog.cookies = cookies()
        reference = self.dialog.cookies
        self.dialog.reject()
        self.assertFalse(reference)

    def test_parent_close_cancels_qr_without_blocking_or_retaining_racing_success(self):
        jar = cookies()
        ticks = []
        def pending(token, qr, state):
            state('login_waiting')
            token.wait(3)
            return jar  # A success delivered after cancellation must be discarded.
        timer = QTimer()
        timer.setInterval(5)
        def tick():
            ticks.append(1)
            if len(ticks) == 5:
                self.dialog.reject()
        timer.timeout.connect(tick)
        timer.start()
        with patch.object(login, 'login_bilibili', side_effect=pending):
            self.dialog._login()
        timer.stop()
        self.assertGreaterEqual(len(ticks), 5)
        self.assertIsNone(self.dialog.cookies)
        self.assertFalse(jar)
        self.assertIsNone(self.dialog.login_dialog)

    def test_missing_dependency_shows_retry_and_can_close(self):
        dialog = BilibiliLoginDialog(self.dialog)
        with patch.object(login, 'login_bilibili', side_effect=video.VideoDownloadError('qr_dependency')):
            dialog.show()
            self.app.processEvents()
            self.pump(lambda: dialog.worker is None)
        self.assertIn('qrcode', dialog.status.text())
        self.assertTrue(dialog.retry.isEnabled())
        self.assertIsNone(dialog.cookies)
        dialog.reject()


if __name__ == '__main__':
    unittest.main()
