"""LL2 payload descriptions stay distinct from mission names and fit on video."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from docx import Document
from exporters import markdown, premiere, word
from feeds import launches
from i18n import set_language
from services.cancellation import CancellationToken, TaskCancelledError
from services.settings import default_settings
from services.video_download import mission_search_keywords
from services.weekly_report import _export_context, _localize_launch_video_items
from tools import video_cards as video
from ui.video_cards_dialog import VideoCardsDialog
from tests.test_video_assets import RAW


class LaunchPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        self.addCleanup(set_language, 'zh_CN')
        Path('build').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path('build').resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = copy.deepcopy(RAW)
        self.raw['mission']['description'] = 'A fictional Earth observation satellite.'

    def test_mission_description_fallback_preserves_name_and_search_keyword(self):
        self.raw['rocket']['payloads'] = []
        item = launches._launch_video_item(self.raw, 'Asia/Shanghai')
        self.assertEqual(item['summary'], RAW['mission']['name'])
        self.assertEqual(item['launch']['payload'], '')
        self.assertNotEqual(item['summary'], item['launch']['payload'])
        self.assertEqual(mission_search_keywords(item)[0], RAW['mission']['name'])

    def test_context_keeps_explicit_reuse_and_booster_recovery_separate_from_capsules(self):
        self.raw['rocket']['configuration']['reusable']=True
        self.raw['rocket']['launcher_stage']=[dict(launcher=dict(serial_number='TEST'),landing=dict(attempt=True,success=None))]
        self.raw['rocket']['spacecraft_stage']=[dict(spacecraft=dict(name='Capsule'),landing=dict(attempt=True,success=True))]
        context=launches.launch_context(self.raw)
        self.assertTrue(context['rocket_reusable'])
        self.assertEqual(context['rocket_recovery'],'TEST: 结果待确认')
        self.assertIn('成功',context['recovery'])
        self.raw['rocket']['configuration']['reusable']=False
        self.assertFalse(launches.launch_context(self.raw)['rocket_reusable'])
        del self.raw['rocket']['configuration']['reusable']
        self.assertNotIn('rocket_reusable',launches.launch_context(self.raw))

    def test_explicit_payload_records_use_amount_and_description_or_type(self):
        self.raw['rocket']['payloads'] = [
            dict(amount=3, payload=dict(name='FictionalSat', description='Weather observations.')),
            dict(amount=1, payload=dict(name='Fictional Probe', type=dict(name='Scientific probe')))]
        item = launches._launch_video_item(self.raw, 'UTC')
        self.assertEqual(item['launch']['payload'],
                         'FictionalSat; Fictional Probe')
        from services.launch_display import resolve_launch
        self.assertEqual(resolve_launch(item['launch_context'])['payload_count'],4)
        self.assertNotIn(self.raw['mission']['description'], item['launch']['payload'])

    def test_missing_or_malformed_payload_never_falls_back_to_mission_name(self):
        for flights in (None, {}, [], [None, 'bad', dict(payload=None)],
                        [dict(payload=dict(name=[], description={}, type='bad'))]):
            self.raw['rocket']['payloads'] = flights
            with self.subTest(flights=flights):
                self.assertEqual(launches._launch_video_item(self.raw, 'UTC')['launch']['payload'],
                                 '')
        for description in (None, '', {}, []):
            self.raw['mission']['description'] = description
            self.assertEqual(launches._launch_video_item(self.raw, 'UTC')['launch']['payload'], '')
        self.raw['rocket']['payloads'] = [dict(amount=True, payload=dict(name='Test', description='Test'))]
        self.assertEqual(launches._launch_video_item(self.raw, 'UTC')['launch']['payload'], 'Test')

    def test_json_and_reports_keep_complete_description(self):
        description = ('First paragraph.\n\n' + 'Additional payload details. ' * 15).strip()
        self.raw['mission']['description'] = description
        item = launches._launch_video_item(self.raw, 'UTC')
        before = copy.deepcopy(item)
        with patch.object(premiere, 'OUTPUT_DIR', self.root), patch.object(word, 'OUTPUT_DIR', self.root), \
                patch.object(markdown, 'OUTPUT_DIR', self.root):
            path = premiere.export_premiere([], {}, _export_context(default_settings(), {}), 'zh_CN', launches=[item])
            article = launches._launch_to_article(self.raw)
            doc = word.export_word([], launches=[article])
            md = markdown.export_markdown([], launches=[article])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['launches'][0]['launch']['payload'], '')
        self.assertIn('Additional payload details.', md.read_text(encoding='utf-8'))
        self.assertIn('Additional payload details.', '\n'.join(p.text for p in Document(doc).paragraphs))
        loaded = video.load_snapshot(path)['items'][0]
        video.make_card(loaded, 1)
        self.assertEqual(loaded['launch']['payload'], '')
        self.assertEqual(item, before)

    def test_payload_only_elides_to_fit_without_changing_short_text(self):
        for text in ('载荷用途说明。' * 30, 'A fictional satellite with several scientific instruments. ' * 20):
            with self.subTest(text=text[:20]):
                image, size, lines = video.fit_payload_text(text, 430, 80, 38, 24)
                self.assertLessEqual(image.height, 80)
                self.assertLessEqual(len(lines), 2)
                self.assertTrue(lines[-1].endswith('…'))
        plain, _, lines = video.fit_payload_text('测试卫星', 430, 80, 38, 24)
        exact, _, _ = video.fit_text('测试卫星', 430, 80, 38, 24)
        self.assertEqual(lines, ['测试卫星'])
        self.assertEqual(plain.tobytes(), exact.tobytes())
        with self.assertRaises(ValueError):
            video.fit_text('必须完整显示的其他字段' * 100, 430, 80, 38, 24)

    def test_description_localization_retains_original_on_empty_and_cancels(self):
        self.raw['rocket']['payloads']=[dict(amount=1,payload=dict(name='Fictional satellite'))]
        item = launches._launch_video_item(self.raw, 'UTC')
        translator = Mock()
        translator.settings.language='zh_CN'
        translator.summarize_payload.return_value = ''
        result = _localize_launch_video_items([item], translator, CancellationToken())[0]
        self.assertEqual(result['launch']['payload'], item['launch']['payload'])
        translator.summarize_payload.assert_not_called()
        token = CancellationToken();token.cancel()
        with self.assertRaises(TaskCancelledError):
            _localize_launch_video_items([item], translator, token)

    def test_editor_saves_full_payload_and_keeps_original_json(self):
        self.raw['mission']['description'] = 'First paragraph.\n\n' + 'Full details. ' * 30
        item = launches._launch_video_item(self.raw, 'UTC')
        source = self.root / 'source.json'
        source.write_text(json.dumps(dict(protocol_version='1.0', job_id='payload', report_title='Fixture',
                                          language='zh_CN', items=[item]), ensure_ascii=False), encoding='utf-8')
        original = source.read_bytes()
        dialog = VideoCardsDialog(self.root)
        self.addCleanup(dialog.reject)
        dialog.load_snapshot(source)
        self.assertIsNotNone(dialog._preview_card)
        self.assertTrue(dialog.launch_fields['payload'].toolTip())
        dialog.launch_fields['orbit'].setText('另一个测试轨道')
        saved = self.root / 'edited.json'
        with patch('ui.video_cards_dialog.QFileDialog.getSaveFileName', return_value=(str(saved), '')):
            dialog._save_snapshot()
        self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['items'][0]['launch']['payload'],
                         item['launch']['payload'])
        self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
