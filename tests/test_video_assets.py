"""New codecs, structured launches and saved opener templates; no Premiere host."""
import copy
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from PIL import ImageChops, ImageDraw

from exporters import premiere
from feeds.launches import _launch_video_item
from services.settings import default_settings
from services.weekly_report import _export_context, _export_reports, get_news, _localize_launch_video_items
from services.cancellation import CancellationToken, TaskCancelledError
from tools import video_cards as video, video_intro as intro
from tests import test_video_cards as render_checks
from tests.test_premiere import article
from tests import test_video_cards_dialog as dialog_checks
from ui.video_intro_dialog import VideoIntroDialog
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication
from translator.translator import Translator, TranslationResult


RAW = dict(id='fictional-launch-01', name='Fictional Rocket | Orbital Test', net='2026-10-10T12:49:00Z',
    rocket={'configuration': {'full_name': '虚构运载火箭'}},
    mission={'name': '虚构卫星任务', 'orbit': {'name': '低地球轨道'}}, pad={'name': '虚构发射场 A'})


class LaunchExportTests(unittest.TestCase):
    def test_structured_launch_reuses_one_fetch_and_raw_date(self):
        response = MagicMock(); response.json.return_value = {'results': [RAW]}
        rows = []
        with patch('requests.get', return_value=response) as fetch:
            _, articles = get_news([], launch_video_items=rows)
        fetch.assert_called_once()
        self.assertEqual(len(articles), 1)
        self.assertEqual(rows[0]['id'], 'll2:fictional-launch-01')
        self.assertEqual(rows[0]['published'], RAW['net'])
        self.assertEqual(rows[0]['launch']['time'], '2026/10/10 20:49')
        self.assertEqual(rows[0]['launch']['display_timezone'], 'Asia/Shanghai')
        root = Path(__file__).resolve().parents[1]/'build';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory, patch.object(premiere, 'OUTPUT_DIR', Path(directory)):
            context = _export_context(default_settings(), {})
            path = premiere.export_premiere([article(1)], {}, context, 'zh_CN', launches=rows)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertNotIn('video_type', data['items'][0])
            self.assertEqual(data['launches'][0]['id'], rows[0]['id'])
            self.assertNotIn('rocket', data['launches'][0])
            loaded = video.load_snapshot(path)
            self.assertEqual(len(loaded['items']), 2)
            self.assertEqual(loaded['items'][1]['video_type'], 'launch')
            self.assertNotIn('launches', loaded)
            self.assertEqual(len(data['items']), 1)
            duplicate = rows+rows
            with self.assertRaises(ValueError):
                premiere.export_premiere([], {}, context, 'zh_CN', launches=duplicate)
            self.assertEqual(len(list(Path(directory).glob('*.premiere.json'))), 1)

    def test_missing_launch_fields_do_not_invent_payload_or_date(self):
        row = _launch_video_item({'id': 'no-data', 'name': 'Test'}, 'Asia/Shanghai')
        self.assertEqual(row['launch']['payload'], '')
        self.assertEqual(row['published'], '')
        self.assertEqual(row['launch']['time'], '')

    def test_dispatch_keeps_same_launch_collection(self):
        rows = [_launch_video_item(RAW, 'Asia/Shanghai')]
        settings = replace(default_settings(), export_premiere=True)
        with patch('services.weekly_report.export_premiere') as export:
            _export_reports([], {}, {}, _export_context(settings, {}), [], settings,
                            False, False, CancellationToken(), launch_video_items=rows)
        self.assertEqual(export.call_args.kwargs['launches'], rows)

    def test_launch_translation_reuses_word_translator_and_cache(self):
        raw = copy.deepcopy(RAW)
        raw['rocket']['configuration']['full_name'] = 'Falcon 9'
        raw['mission']['name'] = 'Fictional satellite mission'
        raw['mission']['orbit']['name'] = 'Low Earth Orbit'
        raw['pad']['name'] = 'Fictional launch site'
        rows = [_launch_video_item(raw, 'Asia/Shanghai')]
        original = copy.deepcopy(rows)
        translations = {'Falcon 9': '猎鹰9号', 'Fictional satellite mission': '虚构卫星任务',
                        'Low Earth Orbit': '近地轨道', 'Fictional launch site': '虚构发射场'}
        settings = replace(default_settings(), export_premiere=True)
        ai = Mock(side_effect=lambda text, *_: translations[text])
        translator = Translator(settings=settings, mode='hybrid', ai_callable=ai, web_search=False)
        def word(*args, **kwargs):
            self.assertIs(kwargs['launch_term_translator'], translator)
            translator.translate_term('Falcon 9', 'rocket')
        with patch('translator.translator.exact_dictionary_translation', return_value=''), \
             patch('translator.translator.dictionary_replace', return_value=('', False)), \
             patch('translator.translator.get_cached_translation', return_value=None), \
             patch('translator.translator.pending_translation', return_value=''), \
             patch('translator.translator.add_pending_term'), patch('translator.translator.cache_translation'), \
             patch('services.weekly_report._launch_term_translator', return_value=translator) as factory, \
             patch('services.weekly_report.export_word', side_effect=word), \
             patch('services.weekly_report.export_premiere') as export:
            _export_reports([], {}, {}, _export_context(settings, {}), [], settings,
                            False, True, CancellationToken(), launch_video_items=rows)
        factory.assert_called_once()
        self.assertEqual(ai.call_count, 4)  # Shared mission/payload and Word rocket are cached.
        translated = export.call_args.kwargs['launches'][0]
        self.assertEqual((translated['title'], translated['summary']), ('猎鹰9号', '虚构卫星任务'))
        self.assertEqual(translated['launch']['payload'], '虚构卫星任务')
        self.assertEqual(translated['launch']['orbit'], '近地轨道')
        self.assertEqual(translated['launch']['site'], '虚构发射场')
        self.assertEqual((translated['id'], translated['published'], translated['url']),
                         (rows[0]['id'], rows[0]['published'], rows[0]['url']))
        self.assertEqual(rows, original)

    def test_translation_failure_fallback_and_cancellation(self):
        rows = [_launch_video_item(RAW, 'Asia/Shanghai')]
        rows[0]['title'] = 'Unknown rocket'
        translator = Mock()
        translator.translate_term_result.return_value = TranslationResult(
            'Unknown rocket', None, False, error_message='offline')
        with self.assertLogs('services.weekly_report', level='WARNING') as logs:
            result = _localize_launch_video_items(rows, translator, CancellationToken())
        self.assertEqual(result[0]['title'], 'Unknown rocket')
        self.assertIn('title', logs.output[0])
        token = CancellationToken()
        translator.translate_term_result.side_effect = lambda *_args, **_kw: token.cancel()
        with self.assertRaises(TaskCancelledError): _localize_launch_video_items(rows, translator, token)

    def test_disabled_exports_do_not_start_launch_translation(self):
        settings = replace(default_settings(), export_premiere=False)
        with patch('services.weekly_report._launch_term_translator') as factory, \
             patch('services.weekly_report.export_premiere') as export:
            _export_reports([], {}, {}, _export_context(settings, {}), [], settings,
                            False, False, CancellationToken(), launch_video_items=[_launch_video_item(RAW, 'Asia/Shanghai')])
        factory.assert_not_called()
        export.assert_not_called()


class NewRenderTests(unittest.TestCase):
    setUp = render_checks.VideoCardTests.setUp
    save = render_checks.VideoCardTests.save
    fake_encode = render_checks.VideoCardTests.fake_encode
    run_fake = render_checks.VideoCardTests.run_fake
    def test_mixed_types_have_independent_numbers_and_cache(self):
        news = list(self.data['items'])
        first = _launch_video_item(RAW, 'Asia/Shanghai')
        second = dict(first, id='ll2:fictional-launch-02')
        self.data['items'] = [news[0], first, news[1], second, news[2]];self.save()
        with patch.object(video, 'make_card', wraps=video.make_card) as make:
            before, count, _ = self.run_fake()
        self.assertEqual(count, 5)
        self.assertEqual([call.args[1] for call in make.call_args_list], [1, 2, 3, 1, 2])
        self.assertEqual([r['ordinal'] for r in before['cards']], [1, 2, 3, 1, 2])
        self.assertEqual([r['id'] for r in before['cards']], [item['id'] for item in news]+[first['id'], second['id']])
        self.assertEqual(before['numbering_policy'], 'independent_news_and_launch')
        inserted = dict(first, id='ll2:fictional-launch-03')
        self.data['items'].insert(2, inserted);self.save()
        after, count, _ = self.run_fake(selected_ids={item['id'] for item in news})
        self.assertEqual(count, 0)
        old_news = {row['id']: row for row in before['cards'] if row['video_type'] == 'news'}
        self.assertEqual([row for row in after['cards'] if row['video_type'] == 'news'], list(old_news.values()))
        changed, count, _ = self.run_fake(selected_ids={item['id'] for item in news} | {second['id']})
        self.assertEqual(count, 1)
        self.assertEqual(next(row['ordinal'] for row in changed['cards'] if row['id'] == second['id']), 1)

    def test_sparse_mixed_selection_numbers_each_type_after_time_sort(self):
        news = list(self.data['items'])
        launches = [dict(_launch_video_item(RAW, 'Asia/Shanghai'), id=f'll2:fictional-{index}',
                         published=f'2026-10-{index+10:02d}T12:49:00Z') for index in range(3)]
        self.data['items'] = [news[2], launches[2], news[0], launches[0], news[1], launches[1]]
        self.save()
        original = self.snapshot.read_bytes()
        selected = {news[0]['id'], news[2]['id'], launches[0]['id'], launches[2]['id']}
        with patch.object(video, 'make_card', wraps=video.make_card) as make:
            manifest, count, path = self.run_fake(selected_ids=selected, sort_order='desc')
        self.assertEqual(count, 4)
        self.assertEqual([call.args[0]['id'] for call in make.call_args_list],
                         [news[2]['id'], news[0]['id'], launches[2]['id'], launches[0]['id']])
        self.assertEqual([call.args[1] for call in make.call_args_list], [1, 2, 1, 2])
        self.assertEqual([r['ordinal'] for r in manifest['cards']], [1, None, 2, 1, None, 2])
        news_files = {r['id']: r['file'] for r in manifest['cards'] if r['video_type'] == 'news' and r['selected']}
        selected.remove(launches[2]['id'])
        changed, count, _ = self.run_fake(selected_ids=selected, sort_order='desc')
        self.assertEqual(count, 1)
        self.assertEqual([r['ordinal'] for r in changed['cards']], [1, None, 2, None, None, 1])
        self.assertTrue(all(r['file'] == news_files[r['id']] for r in changed['cards'] if r['video_type'] == 'news' and r['selected']))
        self.assertTrue(all((path.parent/name).is_file() for name in news_files.values()))
        self.assertEqual(self.snapshot.read_bytes(), original)

    def test_legacy_timezone_suffix_is_hidden_without_shifting_time(self):
        row = _launch_video_item(RAW, 'Asia/Shanghai')
        raw_date = row['published']
        for suffix in ['UTC+0800', 'UTC+8', 'UTC+08:00', 'GMT-0500', 'UTC']:
            row['launch']['time'] = '2026/10/10 20:49 '+suffix
            card = video.make_card(row, 1)
            expected, _, _ = video.fit_text('2026/10/10 20:49', card['layers'][-1][0].width, 80, 38, 24)
            self.assertEqual(card['layers'][-1][0].tobytes(), expected.tobytes())
            self.assertEqual(row['published'], raw_date)
            self.assertIn(suffix, row['launch']['time'])
    def test_encoding_and_launch_details_invalidate_cache(self):
        self.data['items'] = [_launch_video_item(RAW, 'Asia/Shanghai')];self.save()
        first, count, _ = self.run_fake(seconds=6)
        self.assertEqual(count, 1)
        self.assertEqual(first['cards'][0]['seconds'], 12)
        self.assertEqual(first['cards'][0]['video_type'], 'launch')
        _, count, _ = self.run_fake(seconds=6)
        self.assertEqual(count, 0)
        changed, count, _ = self.run_fake(seconds=6, encoding='prores')
        self.assertEqual(count, 1)
        self.assertEqual(changed['encoding'], 'prores')
        self.data['items'][0]['launch']['site'] = '另一个虚构发射场';self.save()
        _, count, _ = self.run_fake(seconds=6, encoding='prores')
        self.assertEqual(count, 1)

    def test_launch_geometry_text_mask_and_validation(self):
        row = _launch_video_item(RAW, 'Asia/Shanghai')
        card = video.make_card(row, 1)
        frame = video.render_frame(card, 5, 12)
        self.assertEqual(frame.getpixel((20, 500)), video.BLUE)
        self.assertEqual(frame.getpixel((600, 500)), video.WHITE)
        self.assertEqual(frame.getpixel((1200, 500))[3], 0)
        self.assertIsNone(video.render_frame(card, 11.99, 12).getbbox())
        for image, x, y, direction, boundary, *_ in card['layers']:
            visible, _, _ = video.reveal_text(image, x, y, direction, boundary, 0)
            self.assertFalse(visible.width and visible.height)
        broken = dict(row, launch={'orbit': 'test'})
        self.data['items'] = [broken];self.save()
        with self.assertRaises(ValueError): video.load_snapshot(self.snapshot)

    def test_png_delta_frames_are_rejected(self):
        metadata = dict(streams=[dict(width=1920, height=1080, codec_name='png', pix_fmt='rgba', nb_frames='180')],
                        format={'duration':'6'}, frames=[{'key_frame': 0}]*180)
        with patch.object(video.subprocess, 'check_output', return_value=json.dumps(metadata).encode()):
            with self.assertRaisesRegex(RuntimeError, 'Delta'):
                video.verify_video(Path('fake.mov'), 180, 6, 'ffmpeg', 'ffprobe')

    def test_intro_custom_logo_name_date_and_cancel(self):
        logo = self.root/'logo.png'
        video.Image.new('RGBA', (400, 200), (200, 50, 30, 255)).save(logo)
        card = intro.prepare('2026-10-10', '我的团队周报', '自定义副标题', logo)
        self.assertEqual(card['logo'].size, (240, 120))
        self.assertEqual(card['date_text'], '2026.10.10')
        self.assertEqual(card['title'], '我的团队周报')
        frame = intro.render_frame(card, 3)
        self.assertEqual(frame.getpixel((620, 530)), (200, 50, 30, 255))
        self.assertEqual(intro.render_frame(card, 0).getpixel((620, 530)), intro.BACKGROUND)
        with self.assertRaises(ValueError): intro.prepare('2026-02-30')
        ranged = intro.prepare('2026-10-04', end_date='2026-10-10')
        self.assertEqual(ranged['date_text'], '2026.10.04 - 2026.10.10')
        with self.assertRaises(ValueError): intro.prepare('2026-10-10', end_date='2026-10-04')
        token = CancellationToken();process = MagicMock()
        process.stdin.write.side_effect = lambda _: token.cancel()
        process.poll.return_value = None
        with patch.object(intro.subprocess, 'Popen', return_value=process):
            with self.assertRaises(TaskCancelledError):
                intro.generate(self.output, '2026-10-10', cancel_check=token.check)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_intro_zoom_has_a_fixed_centre_and_three_static_motions(self):
        path = self.root/'odd-logo.png'
        video.Image.new('RGBA', (175, 240), (255, 255, 255, 255)).save(path)
        card = intro.prepare('2026-10-10', logo_path=path, logo_motion='zoom')
        centres = []
        for index in range(18, 76, 3):
            crop = intro.render_frame(card, index/intro.FPS).crop((480, 390, 760, 670)).convert('RGB')
            weight = ImageChops.subtract(crop, video.Image.new('RGB', crop.size, intro.BACKGROUND[:3])).convert('L')
            values = list(weight.getdata())
            total = sum(values)
            centres.append((sum((i % 280)*value for i, value in enumerate(values))/total,
                            sum((i // 280)*value for i, value in enumerate(values))/total))
        for axis in (0, 1):
            self.assertLess(max(c[axis] for c in centres)-min(c[axis] for c in centres), .05)
        frames = []
        for motion in intro.LOGO_MOTIONS:
            prepared = intro.prepare('2026-10-10', logo_path=path, logo_motion=motion)
            frames.append(intro.render_frame(prepared, .7).tobytes())
        self.assertEqual(len(set(frames)), 3)
        steady = intro.prepare('2026-10-10', logo_path=path)
        self.assertEqual(intro.render_frame(steady, 3).tobytes(), intro.render_frame(steady, 4).tobytes())
        with self.assertRaises(ValueError): intro.prepare('2026-10-10', logo_motion='invalid')

    def test_intro_gif_timing_disposal_transparency_loop_and_first_frame_preview(self):
        frames = []
        for colour in [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)]:
            image = video.Image.new('RGBA', (240, 240))
            ImageDraw.Draw(image).rectangle((80, 80, 160, 160), fill=colour)
            frames.append(image)
        ImageDraw.Draw(frames[0]).rectangle((0, 0, 20, 20), fill='white')
        path = self.root/'animated.gif'
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=0, disposal=2)
        card = intro.prepare('2026-10-10', logo_path=path, logo_motion='zoom')
        self.assertTrue(card['logo_animated'])
        self.assertEqual(len(card['logo_frames']), 3)
        self.assertEqual(card['logo_frames'][1].getpixel((10, 10))[3], 0)
        for actual, expected in zip(card['logo_ends'], [.1, .3, .6]):
            self.assertAlmostEqual(actual, expected)
        for t, expected in [(2.05, (255, 0, 0)), (2.15, (0, 255, 0)), (2.4, (0, 0, 255)), (2.65, (255, 0, 0))]:
            self.assertEqual(intro.render_frame(card, t).getpixel((620, 530))[:3], expected)
        preview = intro.load_logo(path, preview=True)
        self.assertTrue(preview['animated'])
        self.assertEqual(len(preview['frames']), 1)

    def test_intro_animated_logo_cancel_preserves_input(self):
        path = self.root/'cancel.gif'
        a = video.Image.new('RGBA', (40, 40), 'red')
        b = video.Image.new('RGBA', (40, 40), 'blue')
        a.save(path, save_all=True, append_images=[b], duration=100, loop=0)
        original = path.read_bytes()
        token = CancellationToken()
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 3:
                token.cancel()
            token.check()
        with self.assertRaises(TaskCancelledError): intro.load_logo(path, cancel_check=check)
        self.assertEqual(path.read_bytes(), original)

    def test_intro_video_decoder_limits_failure_and_cancellation(self):
        path = self.root/'local.mov'
        path.write_bytes(b'fake video input')
        process = MagicMock()
        process.returncode = 0
        process.poll.return_value = 0
        process.communicate.return_value = (video.Image.new('RGBA', (240, 240), 'red').tobytes()*2, None)
        with patch.object(intro.subprocess, 'Popen', return_value=process) as start:
            asset = intro.load_logo(path)
        self.assertTrue(asset['animated'])
        self.assertFalse(asset['loop'])
        self.assertEqual(len(asset['frames']), 2)
        self.assertAlmostEqual(asset['ends'][-1], 2/intro.FPS)
        command = start.call_args.args[0]
        self.assertEqual(command[command.index('-frames:v')+1], str(intro.FPS*intro.SECONDS))
        self.assertIn('color=black@0', command[command.index('-vf')+1])
        timed = dict(frames=[video.Image.new('RGBA', (4, 4), (i, 255, 0, 255)) for i in range(120)],
                     ends=[(i+1)/intro.FPS for i in range(120)], animated=True, loop=False)
        card = intro.prepare('2026-10-10', logo_data=timed)
        for index in range(60, 354, 7):
            self.assertEqual(intro.render_frame(card, index/intro.FPS).getpixel((620, 530))[0], min(index-12, 119))
        for t in [2.2, 2.25, 3, 4, 6]:
            self.assertEqual(intro.render_frame(card, t).getpixel((620, 530))[0], 119)
        process.communicate.return_value = (b'broken frame', None)
        with patch.object(intro.subprocess, 'Popen', return_value=process), self.assertRaisesRegex(ValueError, '有效画面'):
            intro.load_logo(path, preview=True)
        token = CancellationToken()
        process.poll.return_value = None
        def slow(**kwargs):
            if kwargs:
                token.cancel()
                raise intro.subprocess.TimeoutExpired('ffmpeg', .2)
            return b'', None
        process.communicate.side_effect = slow
        with patch.object(intro.subprocess, 'Popen', return_value=process), self.assertRaises(TaskCancelledError):
            intro.load_logo(path, cancel_check=token.check)
        process.kill.assert_called_once()


class NewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    setUp = dialog_checks.VideoDialogTests.setUp
    cleanup_dialog = dialog_checks.VideoDialogTests.cleanup_dialog
    def test_mixed_checked_numbering_and_sort_preserve_each_category(self):
        data = copy.deepcopy(self.dialog.data)
        for index in range(3):
            launch = dict(_launch_video_item(RAW, 'Asia/Shanghai'), id=f'll2:checked-{index}',
                          published=f'2026-10-{index+10:02d}T12:49:00Z')
            data['items'].append(launch)
        self.dialog._set_data(data, self.path)
        self.dialog.items.setCurrentRow(5)
        self.dialog.items.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.dialog.items.item(4).setCheckState(Qt.CheckState.Unchecked)
        self.assertTrue(self.dialog.items.item(2).text().startswith('新闻条 02  '))
        self.assertTrue(self.dialog.items.item(5).text().startswith('发射任务 02  '))
        self.dialog.sort_order.setCurrentIndex(1)
        self.assertEqual(self.dialog.items.currentRow(), 3)
        self.assertTrue(self.dialog.items.item(0).text().startswith('新闻条 01  '))
        self.assertTrue(self.dialog.items.item(2).text().startswith('新闻条 02  '))
        self.assertTrue(self.dialog.items.item(3).text().startswith('发射任务 01  '))
        self.assertTrue(self.dialog.items.item(5).text().startswith('发射任务 02  '))
        self.assertTrue(self.dialog.items.item(4).text().startswith('发射任务  '))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_list_preview_type_change_numbers_and_legacy_time(self):
        launch = _launch_video_item(RAW, 'Asia/Shanghai')
        launch['launch']['time'] += ' UTC+0800'
        data = copy.deepcopy(self.dialog.data)
        data['items'].append(launch)
        self.dialog._set_data(data, self.path)
        self.dialog.items.setCurrentRow(3)
        self.assertIn('01', self.dialog.items.item(3).text())
        self.assertEqual(self.dialog.launch_fields['time'].text(), '2026/10/10 20:49')
        with patch.object(video, 'make_card', wraps=video.make_card) as make:
            self.dialog._prepare_preview()
        self.assertEqual(make.call_args.args[1], 1)
        self.dialog.items.setCurrentRow(0)
        self.assertEqual(self.dialog.data['items'][3]['launch']['display_timezone'], 'Asia/Shanghai')
        self.dialog.kind.setCurrentIndex(1)
        self.assertIn('02', self.dialog.items.item(3).text())
        self.assertIn('01', self.dialog.items.item(0).text())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_launch_edit_and_selection_preserve_original_snapshot(self):
        self.dialog._check_all(False)
        self.dialog._add_launch()
        row = self.dialog.data['items'][-1]
        self.assertEqual(row['video_type'], 'launch')
        self.assertEqual(self.dialog.selected_ids(), {row['id']})
        self.dialog.title_edit.setText('虚构新火箭')
        self.dialog.launch_fields['site'].setText('虚构基地')
        self.dialog.items.setCurrentRow(0)
        self.dialog.items.setCurrentRow(3)
        self.assertEqual(self.dialog.launch_fields['site'].text(), '虚构基地')
        self.assertEqual(self.dialog.title_edit.text(), '虚构新火箭')
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.dialog.encoding.currentData(), 'png')

    def test_intro_saved_template_restores_name_logo_but_uses_current_week(self):
        output = self.root/'intro'
        logo = self.root/'mark.png';intro.default_logo().save(logo)
        dialog = VideoIntroDialog(output)
        dialog.title.setText('自定义周报')
        dialog.subtitle.setText('我的团队')
        dialog.logo.setText(str(logo))
        dialog.logo_motion.setCurrentIndex(dialog.logo_motion.findData('zoom'))
        dialog.date.setDate(QDate(2025, 1, 1))
        dialog._save_template()
        before = dialog.template.read_bytes()
        saved = json.loads(before)
        self.assertNotIn('date_text', saved)
        self.assertNotIn('end_date', saved)
        self.assertEqual(saved['logo_motion'], 'zoom')
        dialog.reject()
        reopened = VideoIntroDialog(output)
        self.assertEqual(reopened.title.text(), '自定义周报')
        self.assertEqual(reopened.subtitle.text(), '我的团队')
        self.assertEqual(reopened.logo.text(), str(logo))
        self.assertEqual(reopened.logo_motion.currentData(), 'zoom')
        self.assertEqual(reopened.date.date(), QDate.currentDate().addDays(-6))
        self.assertEqual(reopened.end_date.date(), QDate.currentDate())
        reopened.title.clear()
        with patch('ui.video_intro_dialog.QMessageBox.warning'):
            reopened._save_template()
        self.assertEqual(reopened.template.read_bytes(), before)
        reopened.reject()
        saved.pop('logo_motion')
        dialog.template.write_text(json.dumps(saved), encoding='utf-8')
        legacy = VideoIntroDialog(output)
        self.assertEqual(legacy.logo_motion.currentData(), 'fade')
        legacy.reject()

    def test_intro_animation_thumbnail_cache_and_static_motion_selection(self):
        path = self.root/'logo.gif'
        a = video.Image.new('RGBA', (40, 40), 'red')
        b = video.Image.new('RGBA', (40, 40), 'blue')
        a.save(path, save_all=True, append_images=[b], duration=100, loop=0)
        dialog = VideoIntroDialog(self.root/'intro')
        with patch.object(intro, 'load_logo', wraps=intro.load_logo) as load:
            dialog.logo.setText(str(path))
            dialog._preview()
            self.assertFalse(dialog.logo_motion.isEnabled())
            self.assertTrue(load.call_args.kwargs['preview'])
            dialog.title.setText('缩放窗口不重复解码')
            dialog._preview()
            dialog._preview(update_status=False)
            dialog._save_template()
            self.assertEqual(load.call_count, 1)
            saved = json.loads(dialog.template.read_text(encoding='utf-8'))
            self.assertEqual(saved['logo_path'], str(path))
            self.assertNotIn('logo_data', saved)
            static = self.root/'static.png'
            a.save(static)
            dialog.logo.setText(str(static))
            dialog._preview()
            self.assertTrue(dialog.logo_motion.isEnabled())
            self.assertEqual(load.call_count, 2)
        dialog.reject()

    def test_intro_close_waits_for_worker_cancellation(self):
        started = threading.Event()
        def slow_generate(*args, **kwargs):
            started.set()
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                kwargs['cancel_check']()
                time.sleep(.02)
            raise RuntimeError('test timeout')
        dialog = VideoIntroDialog(self.root/'intro')
        dialog.show()
        with patch('ui.video_intro_dialog.video_intro.generate', side_effect=slow_generate):
            dialog._start()
            self.assertTrue(started.wait(1))
            dialog.reject()
            self.assertTrue(dialog.isVisible())
            deadline = time.monotonic()+2
            while dialog.worker is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(dialog.worker)
            self.assertFalse(dialog.isVisible())
            self.assertIn('已取消', dialog.status.text())


if __name__ == '__main__':
    unittest.main()
