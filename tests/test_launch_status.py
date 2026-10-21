"""LL2 result semantics, report propagation and editable video status overlays."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from exporters import markdown, premiere, word
from feeds import launches
from i18n import set_language
from services.cancellation import CancellationToken
from services.settings import default_settings
from services.weekly_report import _export_context, _export_reports
from tools import video_cards as video
from ui.video_cards_dialog import VideoCardsDialog
from utils.launch_status import launch_status_text, recovery_text
from utils.output_files import reserved_output

ROOT = Path(__file__).resolve().parents[1]


def raw_launch():
    return dict(id='status-fixture', name='Test Rocket | Test satellite', net='2026-10-20T11:04:00Z',
                url='https://example.invalid/launch', status=dict(id=3, name='Launch Successful'),
                mission=dict(name='试验卫星', description='Fictional mission.', orbit=dict(name='近地轨道')),
                pad=dict(name='测试发射场'), rocket=dict(configuration=dict(full_name='测试火箭'),
                launcher_stage=[dict(launcher=dict(serial_number='TEST-01'), landing=dict(
                    attempt=True, success=True, type=dict(abbrev='ASDS'),
                    landing_location=dict(abbrev='ASOG')))]))


def snapshot(item):
    return dict(protocol_version='1.0', job_id='status-fixture', report_title='Fixture',
                language='zh_CN', items=[item])


class LaunchStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language('zh_CN')
        self.addCleanup(set_language, 'zh_CN')
        (ROOT/'build').mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT/'build')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def test_official_status_ids_and_unknown_future_values(self):
        names = ['待发射', '待定', '成功', '失败', '暂停', '飞行中', '部分失败', '待确认', '载荷已部署']
        for identifier, name in enumerate(names, 1):
            self.assertEqual(launch_status_text(dict(id=identifier)), name)
        self.assertEqual(launch_status_text('Launch Successful'), '成功')
        self.assertEqual(launch_status_text(dict(id=100, name='New LL2 status')), 'New LL2 status')
        for bad in (None, {}, True, dict(id=True), dict(name=['Success'])):
            self.assertEqual(launch_status_text(bad), '未知')

    def test_recovery_boolean_and_null_semantics(self):
        raw = raw_launch()
        landing = raw['rocket']['launcher_stage'][0]['landing']
        for attempt, success, expected in [(True, True, '成功'), (True, False, '失败'),
                (True, None, '结果待确认'), (False, None, '不尝试回收'),
                (False, True, '不尝试回收'), (None, None, '未知'), (1, 1, '未知'),
                ('true', 'false', '未知')]:
            landing.update(attempt=attempt, success=success)
            self.assertEqual(recovery_text(raw), f'TEST-01: {expected} (ASDS · ASOG)')
        raw['rocket']['launcher_stage'][0]['landing'] = None
        self.assertEqual(recovery_text(raw), 'TEST-01: 未知')
        for raw in ({}, dict(rocket=None), dict(rocket=dict(launcher_stage=[])),
                    dict(rocket=dict(launcher_stage='bad'))):
            self.assertEqual(recovery_text(raw), '未知')

    def test_multiple_boosters_and_spacecraft_keep_separate_results(self):
        raw = raw_launch()
        second = copy.deepcopy(raw['rocket']['launcher_stage'][0])
        second['launcher']['serial_number'] = 'TEST-02'
        second['landing']['success'] = False
        raw['rocket']['launcher_stage'].append(second)
        raw['rocket']['spacecraft_stage'] = [dict(spacecraft=dict(name='Ship Test'), landing=dict(attempt=True, success=None))]
        text = recovery_text(raw)
        for part in ['TEST-01: 成功', 'TEST-02: 失败', '航天器 1 Ship Test: 结果待确认']:
            self.assertIn(part, text)

    def test_one_detailed_request_populates_articles_and_video_without_extra_calls(self):
        response = MagicMock()
        response.json.return_value = dict(results=[raw_launch()])
        items = []
        with patch('requests.get', return_value=response) as fetch:
            articles = launches.get_launches(video_items=items)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs['params']['mode'], 'detailed')
        self.assertEqual(items[0]['launch']['status'], '成功')
        self.assertIn('TEST-01: 成功', items[0]['launch']['recovery'])
        self.assertIn('Recovery: TEST-01: 成功', articles[0].body)
        self.assertIn('Status: 成功', articles[0].news.summary)
        fields = word._launch_fields(articles[0])
        self.assertEqual(fields['status'], '成功')
        self.assertIn('ASOG', fields['recovery'])
        self.assertEqual(fields['mission_description'], 'Fictional mission.')

    def test_network_failure_and_malformed_payload_never_create_success_records(self):
        import requests
        items = []
        with patch('requests.get', side_effect=requests.ConnectionError('offline')):
            self.assertEqual(launches.get_launches(video_items=items), [])
        self.assertIn('offline', launches.last_error())
        self.assertEqual(items, [])
        response = MagicMock(); response.json.return_value = ['invalid']
        with patch('requests.get', return_value=response):
            self.assertEqual(launches.get_launches(), [])
        self.assertIn('Unexpected', launches.last_error())

    def test_word_markdown_and_json_include_actual_statuses(self):
        from docx import Document
        article = launches._launch_to_article(raw_launch())
        item = launches._launch_video_item(raw_launch(), 'Asia/Shanghai')
        context = _export_context(default_settings(), {})
        with patch.object(word, 'OUTPUT_DIR', self.root), patch.object(markdown, 'OUTPUT_DIR', self.root), \
                patch.object(premiere, 'OUTPUT_DIR', self.root):
            doc = word.export_word([], launches=[article], launch_range='past_week')
            md = markdown.export_markdown([], launches=[article], launch_range='past_week')
            js = premiere.export_premiere([], {}, context, 'zh_CN', launches=[item])
        for text in ('\n'.join(p.text for p in Document(doc).paragraphs), md.read_text(encoding='utf-8')):
            self.assertIn('发射状态', text)
            self.assertIn('回收状态', text)
            self.assertIn('TEST-01: 成功 (ASDS · ASOG)', text)
        exported = json.loads(js.read_text(encoding='utf-8'))['launches'][0]
        self.assertEqual(exported['launch'], item['launch'])
        self.assertEqual(video.load_snapshot(js)['items'][0]['launch']['status'], '成功')

    def test_markdown_dispatch_and_disabled_launch_range(self):
        settings = default_settings()
        articles = [launches._launch_to_article(raw_launch())]
        with patch('services.weekly_report.export_markdown') as export:
            _export_reports([], {}, {}, _export_context(settings, {}), articles, settings,
                            True, False, CancellationToken())
        self.assertEqual(export.call_args.kwargs['launches'], articles)
        with patch.object(markdown, 'OUTPUT_DIR', self.root):
            path = markdown.export_markdown([], launches=articles, launch_range='disabled')
        self.assertNotIn('TEST-01', path.read_text(encoding='utf-8'))

    def test_english_statuses_and_video_labels(self):
        set_language('en_US')
        item = launches._launch_video_item(raw_launch(), 'UTC')
        self.assertEqual(item['launch']['status'], 'Successful')
        self.assertIn('TEST-01: Successful', item['launch']['recovery'])
        card = video.make_card(item, 1, 'en_US')
        self.assertEqual(card['status_lines'], ['Launch successful', 'Recovery successful'])
        self.assertEqual(card['badge_states'], ['success', 'success'])

    def test_legacy_snapshot_unknown_overlay_and_reject_invalid_fields(self):
        item = launches._launch_video_item(raw_launch(), 'UTC')
        for key in ['status', 'recovery']:
            item['launch'].pop(key)
        video.validate_snapshot(snapshot(item))
        self.assertEqual(video.make_card(item, 1)['status_lines'], ['发射未知', '回收未知'])
        for key in ['status', 'recovery']:
            for value in [None, False, 3, []]:
                broken = copy.deepcopy(item); broken['launch'][key] = value
                with self.assertRaises(ValueError):
                    video.validate_snapshot(snapshot(broken))
                with self.assertRaises(ValueError), patch.object(premiere, 'OUTPUT_DIR', self.root):
                    premiere.export_premiere([], {}, _export_context(default_settings(), {}), 'zh_CN', launches=[broken])

    def test_top_right_badges_animate_and_preserve_transparent_canvas(self):
        item = launches._launch_video_item(raw_launch(), 'UTC')
        item['launch']['background_opacity'] = 35
        card = video.make_card(item, 1)
        x, y = card['status_position']
        self.assertEqual(x+card['status_panel'].width, video.W-48)
        self.assertEqual(card['status_panel'].height, 62)
        start = video.render_frame(card, 0, 12)
        self.assertIsNone(start.crop((x, y, video.W, 150)).getbbox())
        frame = video.render_frame(card, 5, 12)
        self.assertEqual(frame.getpixel((x+30, y+31)), (45, 183, 26, 255))
        self.assertEqual(frame.getpixel((x+80, y+9))[3], 185)
        self.assertEqual(frame.getpixel((x+80, y-2))[3], 0)
        self.assertEqual(frame.getpixel((1200, 500))[3], 0)
        self.assertEqual(frame.getpixel((20, 500))[3], 89)
        self.assertEqual(frame.getpixel((x+80, y+2))[3], 255)
        self.assertEqual(video.render_frame(card, 11.99, 12).getpixel((x+30, y+31))[3], 0)

    def test_badge_short_labels_never_infer_success_from_missing_or_mixed_results(self):
        cases = [('', '回收未知', 'unknown'), ('TEST: 结果待确认 (ASDS)', '回收待确认', 'pending'),
                 ('TEST: 失败', '回收失败', 'failure'), ('TEST: 不尝试回收', '不回收', 'none'),
                 ('A: 成功; B: 失败', '回收部分失败', 'partial'),
                 ('A: 成功; B: 未知', '回收待确认', 'pending'),
                 ('A: 成功; B: 不尝试回收', '回收成功', 'success'),
                 ('成功的级段: 未知 (成功的地点)', '回收未知', 'unknown'),
                 ('未知长文本 '*1000, '回收未知', 'unknown')]
        for value, label, state in cases:
            with self.subTest(value=value[:40]):
                self.assertEqual(video.launch_badge_labels(dict(status='成功', recovery=value))[1], (label, state))
        for value, expected in [('失败', 'failure'), ('部分失败', 'partial'), ('飞行中', 'flight'),
                                ('载荷已部署', 'flight'), ('未知', 'unknown')]:
            self.assertEqual(video.launch_badge_labels(dict(status=value))[0][1], expected)
        panel = video.make_status_badges(dict(status='成功', recovery='成功'))
        gap_x = panel['status_panel'].width//2
        # Both equal-length Chinese labels have a transparent 24 px gap.
        self.assertEqual(panel['status_panel'].getpixel((gap_x, 31))[3], 0)

    def test_editor_saves_both_status_fields_without_touching_original(self):
        item = launches._launch_video_item(raw_launch(), 'UTC')
        source = self.root/'source.json'
        source.write_text(json.dumps(snapshot(item), ensure_ascii=False), encoding='utf-8')
        original = source.read_bytes()
        dialog = VideoCardsDialog(self.root)
        self.addCleanup(dialog.reject)
        dialog.load_snapshot(source)
        self.assertEqual(dialog.launch_fields['status'].text(), '成功')
        dialog.launch_fields['status'].setText('部分失败')
        dialog.launch_fields['recovery'].setText('TEST-01: 失败')
        saved = self.root/'edited.json'
        with patch('ui.video_cards_dialog.QFileDialog.getSaveFileName', return_value=(str(saved), '')):
            dialog._save_snapshot()
        dialog.load_snapshot(saved)
        self.assertEqual(dialog.launch_fields['status'].text(), '部分失败')
        self.assertEqual(dialog.launch_fields['recovery'].text(), 'TEST-01: 失败')
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(dialog._preview_card['badge_states'], ['partial', 'failure'])

    def test_status_change_invalidates_cache_and_keeps_old_movie(self):
        item = launches._launch_video_item(raw_launch(), 'UTC')
        data = snapshot(item)
        def encode(card, directory, *args):
            with reserved_output(directory, '.launch.mov') as (final, temp):
                temp.write_bytes(card['status_panel'].tobytes()[:100])
            return final
        with patch.object(video.shutil, 'which', side_effect=lambda name:name), \
                patch.object(video, 'encode_card', side_effect=encode) as encoder:
            manifest = video.generate(data, self.root)
            first = json.loads(manifest.read_text(encoding='utf-8'))['cards'][0]
            video.generate(data, self.root)
            self.assertEqual(encoder.call_count, 1)
            item['launch']['recovery'] = 'TEST-01: 失败'
            video.generate(data, self.root)
            self.assertEqual(encoder.call_count, 2)
        latest = json.loads(manifest.read_text(encoding='utf-8'))['cards'][0]
        self.assertEqual(latest['recovery'], 'TEST-01: 失败')
        self.assertNotEqual(first['fingerprint'], latest['fingerprint'])
        self.assertNotEqual(first['file'], latest['file'])
        self.assertTrue((manifest.parent/first['file']).is_file())


if __name__ == '__main__':
    unittest.main()
