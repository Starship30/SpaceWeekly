import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from i18n import set_language, tr
from services.cancellation import TaskCancelledError
from tools import video_cards
from ui.video_cards_dialog import VideoCardsDialog, VideoCardsWorker
from ui.widgets.app_toolbar import AppToolBar


class VideoDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        root = Path(__file__).resolve().parents[1]
        (root/'build').mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=root/'build')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.path = self.root/'snapshot.json'
        self.original = (root/'plugins/premiere-uxp/example.premiere.json').read_bytes()
        self.path.write_bytes(self.original)
        self.dialog = VideoCardsDialog(self.root)
        self.dialog.load_snapshot(self.path)
        self.addCleanup(self.cleanup_dialog)

    def cleanup_dialog(self):
        if self.dialog.worker is not None:
            self.dialog.worker.cancel()
            self.dialog.worker.wait(3000)
            self.app.processEvents()
        self.dialog.reject()
        self.app.processEvents()

    def test_edit_switch_and_selection_keep_source_and_ids(self):
        data = copy.deepcopy(self.dialog.data)
        self.dialog.title_edit.setText('修改后的展示标题')
        self.dialog.summary_edit.setPlainText('修改后的展示摘要')
        self.dialog.items.setCurrentRow(1)
        self.assertEqual(self.dialog.data['items'][0]['title'], '修改后的展示标题')
        self.assertEqual(self.dialog.data['items'][0]['title_origin'], 'user_edit')
        self.assertEqual(self.dialog.data['items'][0]['summary_origin'], 'user_edit')
        self.assertEqual([x['id'] for x in data['items']], [x['id'] for x in self.dialog.data['items']])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.dialog._check_all(False)
        self.dialog.items.item(2).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(self.dialog.selected_ids(), {data['items'][2]['id']})
        self.dialog.items.setCurrentRow(0)
        self.assertEqual(self.dialog.title_edit.text(), '修改后的展示标题')

    def test_preview_and_marquee_duration_share_renderer(self):
        self.dialog.summary_edit.setPlainText('用于验证缓慢滚动的摘要。'*18)
        self.dialog._prepare_preview()
        self.assertGreater(self.dialog._preview_seconds, self.dialog.seconds.value())
        self.assertIsNotNone(self.dialog.preview.pixmap())
        self.dialog._toggle_preview()
        self.assertTrue(self.dialog._preview_timer.isActive())
        self.dialog._tick_preview()
        self.assertEqual(self.dialog.slider.value(), 1)
        self.dialog.reject()
        self.assertFalse(self.dialog._preview_timer.isActive())

    def test_sort_selection_preserves_edits_current_id_and_preview_number(self):
        data = copy.deepcopy(self.dialog.data)
        data['items'].reverse()
        self.dialog._set_data(data, self.path)
        self.assertEqual(self.dialog.sort_order.currentData(), 'asc')
        ids = [row['id'] for row in self.dialog.data['items']]
        self.assertEqual(ids, [row['id'] for row in reversed(data['items'])])
        current_id = ids[0]
        self.dialog.title_edit.setText('排序前编辑的标题')
        self.dialog._check_all(False)
        self.dialog.items.item(0).setCheckState(Qt.CheckState.Checked)
        self.dialog.sort_order.setCurrentIndex(1)
        self.assertEqual([row['id'] for row in self.dialog.data['items']], list(reversed(ids)))
        self.assertEqual(self.dialog.selected_ids(), {current_id})
        self.assertEqual(self.dialog.items.currentRow(), 2)
        self.assertEqual(self.dialog.title_edit.text(), '排序前编辑的标题')
        with patch.object(video_cards, 'make_card', wraps=video_cards.make_card) as make:
            self.dialog._prepare_preview()
        self.assertEqual(make.call_args.args[1], 1)
        with patch.object(VideoCardsWorker, 'start'):
            self.dialog._start()
            self.assertEqual(self.dialog.worker.sort_order, 'desc')
            self.assertEqual([row['id'] for row in self.dialog.worker.data['items']], list(reversed(ids)))
            folder = self.dialog.output_root/hashlib.sha256(self.dialog.data['job_id'].encode()).hexdigest()[:16]
            saved = json.loads((folder/'edited-snapshot.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['order_policy'], 'video_type_then_published_desc')
            self.assertEqual(saved['items'][2]['title'], '排序前编辑的标题')
            self.dialog._worker_finished()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_checkbox_changes_update_numbers_and_unchecked_preview_without_losing_edits(self):
        self.dialog.items.setCurrentRow(2)
        self.dialog.title_edit.setText('不因勾选变化丢失的标题')
        self.dialog.items.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertTrue(self.dialog.items.item(0).text().startswith('新闻条 01  '))
        self.assertTrue(self.dialog.items.item(1).text().startswith('新闻条  '))
        self.assertTrue(self.dialog.items.item(2).text().startswith('新闻条 02  '))
        with patch.object(video_cards, 'make_card', wraps=video_cards.make_card) as make:
            self.dialog.items.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(make.call_args.args[1], 1)
        self.assertEqual(self.dialog.title_edit.text(), '不因勾选变化丢失的标题')
        self.dialog.items.item(2).setCheckState(Qt.CheckState.Unchecked)
        self.assertIsNone(self.dialog._preview_card)
        self.assertEqual(self.dialog.preview.text(), tr('video.unselected_preview'))
        self.assertFalse(self.dialog.play.isEnabled())
        self.assertFalse(self.dialog.generate_button.isEnabled())
        with patch.object(self.dialog, '_prepare_preview', wraps=self.dialog._prepare_preview) as preview:
            self.dialog._check_all(True)
        preview.assert_called_once()
        self.assertTrue(self.dialog.items.item(2).text().startswith('新闻条 03  '))
        self.assertTrue(self.dialog.play.isEnabled())
        self.assertTrue(self.dialog.generate_button.isEnabled())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_start_saves_copy_and_refuses_duplicate_click(self):
        self.dialog.title_edit.setText('保存的展示标题')
        self.dialog._check_all(False)
        self.dialog.items.item(1).setCheckState(Qt.CheckState.Checked)
        with patch.object(VideoCardsWorker, 'start') as start:
            self.dialog._start()
            worker = self.dialog.worker
            self.dialog._start()
            self.assertIs(self.dialog.worker, worker)
            start.assert_called_once()
            self.assertFalse(self.dialog.inputs.isEnabled())
            self.assertEqual(worker.selected_ids, {self.dialog.data['items'][1]['id']})
            folder = self.dialog.output_root/hashlib.sha256(self.dialog.data['job_id'].encode()).hexdigest()[:16]
            saved = json.loads((folder/'edited-snapshot.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['items'][0]['title'], '保存的展示标题')
            self.assertEqual(self.path.read_bytes(), self.original)
            self.dialog._worker_finished()
            self.assertTrue(self.dialog.inputs.isEnabled())

    def test_worker_routes_success_cancellation_and_failure(self):
        data = self.dialog.data
        selected = {data['items'][1]['id']}
        worker = VideoCardsWorker(data, self.root, 12, selected, sort_order='desc')
        success, cancelled, failed = [], [], []
        worker.finished_ok.connect(success.append)
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.failed.connect(failed.append)
        with patch('ui.video_cards_dialog.video_cards.generate', return_value=self.root/'manifest.json') as generate:
            worker.run()
            self.assertEqual(generate.call_args.kwargs['selected_ids'], selected)
            self.assertEqual(generate.call_args.kwargs['sort_order'], 'desc')
            self.assertEqual(generate.call_args.args[0]['job_id'], data['job_id'])
        self.assertEqual(len(success), 1)
        with patch('ui.video_cards_dialog.video_cards.generate', side_effect=TaskCancelledError('cancel')):
            worker.run()
        self.assertEqual(cancelled, [True])
        with patch('ui.video_cards_dialog.video_cards.generate', side_effect=ValueError('bad template')):
            worker.run()
        self.assertEqual(failed, ['bad template'])

    def test_closing_waits_for_worker_cancellation(self):
        started = threading.Event()
        def slow_generate(*args, **kwargs):
            started.set()
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                kwargs['cancel_check']()
                time.sleep(.02)
            raise RuntimeError('test timeout')
        self.dialog.show()
        with patch('ui.video_cards_dialog.video_cards.generate', side_effect=slow_generate):
            self.dialog._start()
            self.assertTrue(started.wait(1))
            self.dialog.reject()
            self.assertTrue(self.dialog.isVisible())
            deadline = time.monotonic()+2
            while self.dialog.worker is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(self.dialog.worker)
            self.assertFalse(self.dialog.isVisible())
            self.assertIn('已取消', self.dialog.status.text())

    def test_toolbar_entry_and_english_strings(self):
        toolbar = AppToolBar()
        seen = []
        toolbar.video_cards_requested.connect(lambda: seen.append(True))
        action = next(a for a in toolbar.actions() if a.text() == tr('video.toolbar'))
        action.trigger()
        self.assertEqual(seen, [True])
        toolbar.set_running(True)
        self.assertFalse(action.isEnabled())
        set_language('en_US')
        self.assertEqual(tr('video.toolbar'), 'Video assets')
        self.assertNotEqual(tr('video.edits_note'), 'video.edits_note')
        self.assertEqual(tr('video.order_desc'), 'Newest first')
        set_language('zh_CN')


if __name__ == '__main__':
    unittest.main()
