import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import requests
from PySide6.QtWidgets import QApplication

from i18n import set_language, tr
from services import video_download as video, video_download_state as state, bilibili_login as login
from services.cancellation import CancellationToken, TaskCancelledError
from ui.video_download_dialog import VideoDownloadDialog

BV = 'BV1wgHm6tE4J'
OTHER = 'BV1xx411c7mD'
IMAGES = dict(img_url='https://i0.hdslb.com/bfs/wbi/'+'a'*32+'.png',
              sub_url='https://i0.hdslb.com/bfs/wbi/'+'b'*32+'.png')


def jar():
    cookies = requests.cookies.RequestsCookieJar()
    cookies.set('SESSDATA', 'synthetic_private_session', domain='.bilibili.com', path='/',
                secure=True, expires=int(time.time())+3600)
    return cookies


class SearchOptionsTests(unittest.TestCase):
    def test_uid_links_duplicates_and_invalid_values(self):
        self.assertEqual(video.parse_up_ids('123, https://space.bilibili.com/456/?spm_id=1，123'), ['123','456'])
        for value in ('0', '-12', 'display name', 'https://example.com/123', 'https://space.bilibili.com/123/x',
                      ' '.join(str(i) for i in range(1,12))):
            with self.subTest(value=value), self.assertRaises(video.VideoDownloadError):
                video.parse_up_ids(value)

    def test_exact_timestamp_sort_and_unknown_last(self):
        rows = [dict(bvid=BV,title='later',mid=123,pubdate=1720010100),
                dict(bvid=OTHER,title='earlier',mid=456,pubdate=1720010000)]
        items = video._search_items(rows)
        unknown = video._search_items([dict(bvid='BV1234567890',title='unknown')])[0]
        items.append(unknown)
        self.assertEqual([v.title for v in video.sort_search_items(items)], ['earlier','later','unknown'])
        self.assertEqual([v.title for v in video.sort_search_items(items,'desc')], ['later','earlier','unknown'])
        self.assertEqual(video.sort_search_items(items,'relevance'), items)
        self.assertEqual(items[0].author_id, '123')

    def test_up_endpoint_signed_request_filters_real_uid_and_sorts(self):
        def get(session, url, cancel, **kwargs):
            cancel()
            if url.endswith('/nav'):
                return json.dumps(dict(data=dict(wbi_img=IMAGES)))
            if '/arc/search' in url:
                params = kwargs['params']
                self.assertEqual(params['mid'], '123')
                self.assertEqual(params['order'], 'pubdate')
                self.assertEqual(params['keyword'], 'mission')
                self.assertRegex(params['w_rid'], r'^[a-f0-9]{32}$')
                return json.dumps(dict(code=0,data=dict(list=dict(vlist=[
                    dict(bvid=BV,title='later',mid=123,created=1720010100,length='01:20'),
                    dict(bvid=OTHER,title='earlier',mid=123,created=1720010000,length='02:30')]))))
            return ''
        with patch.object(video, '_get', side_effect=get):
            result = video.search_videos('mission', up_ids=['123'])
        self.assertEqual([v.title for v in result.items], ['earlier','later'])
        self.assertTrue(all(v.author_id == '123' for v in result.items))
        self.assertEqual(result.items[0].duration, '02:30')
        self.assertEqual(result.warning, '')

    def test_restricted_up_fallback_reports_partial_coverage_and_never_invents_matches(self):
        def get(session, url, cancel, **kwargs):
            if url.endswith('/nav'):
                return json.dumps(dict(data=dict(wbi_img=IMAGES)))
            if '/arc/search' in url:
                return '{"code":-352,"message":"risk control"}'
            if '/search/type' in url:
                return json.dumps(dict(code=0,data=dict(result=[
                    dict(bvid=BV,title='matching',mid=123),
                    dict(bvid=OTHER,title='other uploader',mid=456)])))
            return ''
        with patch.object(video, '_get', side_effect=get):
            result = video.search_videos('mission', up_ids=['123'])
            self.assertEqual([v.id for v in result.items], [BV])
            self.assertIn('-352', result.warning)
            with self.assertRaises(video.VideoDownloadError) as error:
                video.search_videos('mission', up_ids=['999'])
            self.assertEqual(error.exception.code, 'up_search')
            self.assertIn('-352', error.exception.detail)

    def test_wbi_does_not_accept_bad_keys_or_keep_special_characters(self):
        with patch.object(video.time,'time', return_value=1700000000):
            signed = video._wbi_params(dict(keyword="a!b'c(d)*"), IMAGES)
        self.assertEqual(signed['keyword'], 'abcd')
        self.assertEqual(signed['wts'], '1700000000')
        with self.assertRaises(video.VideoDownloadError):
            video._wbi_params({}, dict(img_url='invalid',sub_url='invalid'))

    def test_saved_login_network_failure_is_not_expiry(self):
        for payload, expected in [(dict(code=-101),False),
                                  (dict(code=0,data=dict(isLogin=False)),False),
                                  (dict(code=0,data=dict(isLogin=True)),True),
                                  (dict(code=-352),None)]:
            with patch.object(login,'_get',return_value=json.dumps(payload)):
                self.assertIs(login.session_status(jar(),CancellationToken()), expected)
        with patch.object(login,'_get',side_effect=requests.ConnectionError('offline')):
            self.assertIsNone(login.session_status(jar(),CancellationToken()))
        with patch.object(login,'_get',side_effect=TaskCancelledError()):
            with self.assertRaises(TaskCancelledError):
                login.session_status(jar(),CancellationToken())


class WorkspaceStateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=Path('build').resolve())
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_session_encrypted_survives_new_process_and_logout(self):
        state.save_session(self.root, jar())
        path = state.state_path(self.root,'.bilibili-session.bin')
        self.assertNotIn(b'synthetic_private_session', path.read_bytes())
        self.assertNotIn(b'SESSDATA', path.read_bytes())
        loaded = state.load_session(self.root)
        self.assertEqual(loaded.get('SESSDATA'), 'synthetic_private_session')
        # A new Python process verifies this is independent of UI memory.
        source = "from services.video_download_state import load_session; import sys; assert load_session(sys.argv[1]).get('SESSDATA') == 'synthetic_private_session'"
        result = subprocess.run([sys.executable,'-c',source,str(self.root)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode,0, result.stderr.decode(errors='replace'))
        state.clear_session(self.root)
        self.assertIsNone(state.load_session(self.root))

    def test_encryption_failure_writes_no_plaintext(self):
        with patch.object(state,'_crypt',side_effect=video.VideoDownloadError('session_store')):
            with self.assertRaises(video.VideoDownloadError):
                state.save_session(self.root,jar())
        self.assertFalse((self.root/'video-downloads').exists())

    def test_corrupt_session_errors_and_preferences_roundtrip(self):
        folder = self.root/'video-downloads'
        folder.mkdir()
        (folder/'.bilibili-session.bin').write_bytes(b'invalid ciphertext')
        with self.assertRaises(video.VideoDownloadError) as error:
            state.load_session(self.root)
        self.assertEqual(error.exception.code,'session_store')
        state.save_preferences(self.root,['123','456'],'desc')
        self.assertEqual(state.load_preferences(self.root), dict(up_ids=['123','456'],order='desc'))
        (folder/'.search-preferences.json').write_text('broken')
        self.assertEqual(state.load_preferences(self.root), {})

    def test_state_path_cannot_escape_workspace(self):
        with self.assertRaises(video.VideoDownloadError):
            state.state_path(self.root,'../../outside.json')


class DownloadOptionsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        self.scratch = tempfile.TemporaryDirectory(dir=Path('build').resolve())
        self.root = Path(self.scratch.name)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            self.wait(dialog)
        self.scratch.cleanup()

    def create(self, items=()):
        dialog = VideoDownloadDialog(self.root, items)
        self.dialogs.append(dialog)
        return dialog

    def wait(self, dialog):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            self.app.processEvents()
            if dialog.worker is None:
                return
            time.sleep(.01)
        self.fail('Worker did not finish')

    def test_launch_mission_name_default_news_exclusion_and_date_order(self):
        items = [dict(video_type='launch',title='rocket',summary='late mission',published='2026-10-11'),
                 dict(title='news',keywords=['news keyword'],published='2026-10-09'),
                 dict(video_type='launch',title='rocket',summary='early mission',published='2026-10-10')]
        original = json.dumps(items)
        dialog = self.create(items)
        self.assertFalse(dialog.include_news.isChecked())
        self.assertEqual(dialog.tasks.count(),2)
        self.assertEqual(dialog.keyword.currentText(),'early mission')
        dialog.keyword.setEditText('custom keywords')
        dialog.order.setCurrentIndex(dialog.order.findData('desc'))
        self.assertIn('late mission',dialog.tasks.itemText(0))
        self.assertEqual(dialog.keyword.currentText(),'custom keywords')
        dialog.include_news.setChecked(True)
        self.assertEqual(dialog.tasks.count(),3)
        dialog.tasks.setCurrentIndex(2)
        self.assertEqual(dialog.keyword.currentText(),'news keyword')
        self.assertEqual(json.dumps(items),original)

    def test_uid_order_preferences_and_worker_options(self):
        dialog=self.create([dict(video_type='launch',title='rocket',summary='mission',published='2026-10-09')])
        dialog.up_ids.setText('https://space.bilibili.com/123, 456')
        dialog.order.setCurrentIndex(1)
        with patch.object(video,'search_videos',return_value=video.VideoSearchResults([])) as search:
            dialog._search()
            self.wait(dialog)
        self.assertEqual(search.call_args.kwargs['up_ids'],['123','456'])
        self.assertEqual(search.call_args.kwargs['order'],'desc')
        reopened=self.create()
        self.assertEqual(reopened.up_ids.text(),'123, 456')
        self.assertEqual(reopened.order.currentData(),'desc')
        self.assertFalse(reopened.include_news.isChecked())

    def test_add_selected_up_and_partial_warning_are_visible(self):
        dialog=self.create()
        info=video._search_items([dict(bvid=BV,title='video',mid=123)])[0]
        with patch.object(video,'search_videos',return_value=video.VideoSearchResults([info],'up','API -403')):
            dialog.keyword.setEditText('mission')
            dialog._search()
            self.wait(dialog)
        self.assertIn('前 3 页',dialog.status.text())
        self.assertIn('-403',dialog.status.text())
        dialog.results.setCurrentRow(0)
        dialog._add_up()
        self.assertEqual(dialog.up_ids.text(),'123')
        self.assertEqual(state.load_preferences(self.root)['up_ids'],['123'])

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_immediate_close_does_not_restore_hidden_session(self):
        state.save_session(self.root,jar())
        with patch.object(login,'session_status') as verify:
            dialog=self.create()
            dialog.reject()
            self.app.processEvents()
        verify.assert_not_called()
        self.assertIsNone(dialog.cookies)
        self.assertIsNone(dialog.worker)
        self.assertTrue(state.state_path(self.root,'.bilibili-session.bin').is_file())

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_close_keeps_encrypted_session_reopen_restores_and_logout_deletes(self):
        dialog=self.create()
        self.app.processEvents()
        with patch.object(login,'login_bilibili',return_value=jar()):
            dialog._login()
        path=state.state_path(self.root,'.bilibili-session.bin')
        self.assertTrue(path.is_file())
        dialog.reject()
        self.assertIsNone(dialog.cookies)
        self.assertTrue(path.is_file())
        with patch.object(login,'session_status',return_value=True):
            reopened=self.create()
            self.app.processEvents()
            self.wait(reopened)
        self.assertEqual(reopened.cookies.get('SESSDATA'),'synthetic_private_session')
        self.assertEqual(reopened.auth_status.text(),tr('video_download.logged_in'))
        reopened._login()
        self.assertFalse(path.exists())
        self.assertIsNone(reopened.cookies)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_restore_network_failure_retains_session_but_confirmed_expiry_deletes(self):
        for verified in (None,False):
            state.save_session(self.root,jar())
            with patch.object(login,'session_status',return_value=verified):
                dialog=self.create()
                self.app.processEvents()
                self.wait(dialog)
            self.assertEqual(state.state_path(self.root,'.bilibili-session.bin').exists(), verified is None)
            if verified is None:
                self.assertTrue(dialog.cookies)
                self.assertEqual(dialog.auth_status.text(),tr('video_download.login_unverified'))
            else:
                self.assertIsNone(dialog.cookies)
                self.assertEqual(dialog.status.text(),tr('video_download.session_expired'))
            dialog.reject()


if __name__ == '__main__':
    unittest.main()
