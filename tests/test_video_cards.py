"""Offline renderer checks; mock encoding tests do not verify Premiere."""
import copy
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.cancellation import CancellationToken, TaskCancelledError
try:
    from tools import video_cards as video
except ModuleNotFoundError as error:
    if error.name != 'PIL':
        raise
    video = None
from utils.output_files import reserved_output


@unittest.skipUnless(video is not None and video.FONT.is_file() and video.NUMBER_FONT.is_file(),
                     'Standalone renderer requires Pillow and the local template fonts')
class VideoCardTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1]
        (base/'build').mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=base/'build')
        binaries = patch.object(video.shutil, 'which', side_effect=lambda name: name)
        binaries.start()
        self.addCleanup(binaries.stop)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.output = self.root/'cards'
        self.snapshot = self.root/'news.json'
        self.data = json.loads((base/'plugins/premiere-uxp/example.premiere.json').read_text(encoding='utf-8'))
        self.save()

    def save(self):
        self.snapshot.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8-sig')

    def fake_encode(self, card, directory, *args):
        with reserved_output(directory, '.launch.mov' if card.get('video_type') == 'launch' else '.news.mov') as (final, temp):
            temp.write_bytes(card['layers'][2][0].tobytes()[:1000])
        return final

    def run_fake(self, **kwargs):
        with patch.object(video, 'encode_card', side_effect=self.fake_encode) as encode, redirect_stdout(io.StringIO()):
            path = video.generate(self.snapshot, self.output, **kwargs)
        return json.loads(path.read_text(encoding='utf-8')), encode.call_count, path

    def test_manifest_accepts_legacy_and_split_paths_rejects_escapes(self):
        for name in ['old.card.mov', 'news/one.news.mov', 'launches/one.launch.mov']:
            self.assertEqual(video.manifest_video_path(self.output, name), self.output/name)
        for name in ['', '../other.mov', 'news/../../other.mov', 'news//one.mov',
                     '/absolute.mov', 'D:/other.mov', 'news\\one.mov', 'other/one.mov',
                     'news/sub/one.mov', 'news/one.mov:stream']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                video.manifest_video_path(self.output, name)

    def test_legacy_basename_manifest_still_resumes_without_overwriting(self):
        payload, _, manifest = self.run_fake()
        for record in payload['cards']:
            old_path = manifest.parent/record['file']
            legacy = manifest.parent/old_path.name
            old_path.rename(legacy)
            record['file'] = legacy.name
        manifest.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        resumed, count, _ = self.run_fake()
        self.assertEqual(count, 0)
        self.assertEqual(resumed['cards'], payload['cards'])
        self.assertTrue(all((manifest.parent/r['file']).is_file() for r in resumed['cards']))

    def test_bom_chinese_protocol_validation(self):
        data = video.load_snapshot(self.snapshot)
        self.assertEqual(data['items'][0]['title'], '虚构月面传感器完成测试')
        for mutate in [lambda d: d.update(protocol_version='2.0'),
                       lambda d: d['items'][0].pop('summary'),
                       lambda d: d['items'][0].update(categories='科学'),
                       lambda d: d['items'][0].update(url='file:///secret'),
                       lambda d: d['items'].append(copy.deepcopy(d['items'][0]))]:
            with self.subTest(mutate=mutate):
                old = copy.deepcopy(self.data)
                mutate(self.data); self.save()
                with self.assertRaises(ValueError): video.load_snapshot(self.snapshot)
                self.data = old

    def test_time_order_uses_instants_stable_ties_and_unknown_dates_last(self):
        def row(identifier, published, kind='news'):
            return dict(id=identifier, published=published, video_type=kind)
        rows = [row('launch-new', '2026-10-03T10:00:00Z', 'launch'),
                row('bad', 'unknown'), row('same-a', '2026-10-02T10:00:00+08:00'),
                row('old', 'Thu, 01 Oct 2026 10:00:00 GMT'), row('same-b', '2026-10-02T02:00:00Z'),
                row('no-date', ''), row('launch-old', '2026-10-02', 'launch'),
                row('launch-no-date', '', 'launch')]
        original = copy.deepcopy(rows)
        self.assertEqual([x['id'] for x in video.sort_items(rows)],
                         ['old', 'same-a', 'same-b', 'bad', 'no-date',
                          'launch-old', 'launch-new', 'launch-no-date'])
        self.assertEqual([x['id'] for x in video.sort_items(rows, 'desc')],
                         ['same-a', 'same-b', 'old', 'bad', 'no-date',
                          'launch-new', 'launch-old', 'launch-no-date'])
        self.assertEqual(rows, original)
        with self.assertRaises(ValueError): video.sort_items(rows, 'random')

    def test_order_change_updates_numbers_manifest_and_cache_without_overwriting(self):
        self.data['items'].reverse()
        self.save()
        original = self.snapshot.read_bytes()
        ascending, count, path = self.run_fake()
        self.assertEqual(count, 3)
        ids = [row['id'] for row in ascending['cards']]
        self.assertEqual(ids, [row['id'] for row in reversed(self.data['items'])])
        self.assertEqual(ascending['order_policy'], 'video_type_then_published_asc')
        old_files = [path.parent/row['file'] for row in ascending['cards']]
        with patch.object(video, 'make_card', wraps=video.make_card) as make:
            descending, count, _ = self.run_fake(sort_order='desc')
        self.assertEqual(count, 2)  # The middle card retains its number and cached file.
        self.assertEqual([call.args[0]['id'] for call in make.call_args_list], list(reversed(ids)))
        self.assertEqual([call.args[1] for call in make.call_args_list], [1, 2, 3])
        self.assertEqual([row['id'] for row in descending['cards']], list(reversed(ids)))
        self.assertEqual([row['ordinal'] for row in descending['cards']], [1, 2, 3])
        self.assertEqual(descending['order_policy'], 'video_type_then_published_desc')
        self.assertEqual(descending['cards'][1], ascending['cards'][1])
        self.assertTrue(all(path.is_file() for path in old_files))
        self.assertEqual(self.snapshot.read_bytes(), original)
        _, count, _ = self.run_fake(sort_order='desc')
        self.assertEqual(count, 0)

    def test_wrap_chinese_english_and_explicit_newlines(self):
        font = video.ImageFont.truetype(str(video.FONT), 32)
        chinese = '中文标点与自动换行。'*10
        lines = video.wrap_text(chinese, font, 200)
        self.assertEqual(''.join(lines), chinese)
        self.assertTrue(all(font.getlength(line) <= 200 for line in lines))
        lines = video.wrap_text('SpaceWeekly orbital science\n下一段', font, math.ceil(font.getlength('SpaceWeekly'))+4)
        self.assertIn('SpaceWeekly', lines)
        self.assertEqual(lines[-1], '下一段')
        token = 'a'*100
        self.assertEqual(''.join(video.wrap_text(token, font, 100)), token)

    def test_fit_shrinks_without_cutting_text(self):
        text = '测试航天新闻自动换行与完整显示。'*10
        image, size, lines = video.fit_text(text, 410, 562, 36, 26)
        self.assertLess(size, 36)
        self.assertEqual(''.join(lines), text)
        self.assertLessEqual(image.height, 562)
        with self.assertRaisesRegex(ValueError, '不会自动截断'):
            video.fit_text(text*50, 410, 562, 36, 26)

    def test_shapes_masks_number_font_and_full_text(self):
        card = video.make_card(self.data['items'][0], 1)
        number_font = video.ImageFont.truetype(str(video.NUMBER_FONT), 82)
        self.assertTrue(number_font.getname()[0])
        if video.NUMBER_FONT.name.lower() == 'bankgothic-bold.ttf':
            self.assertEqual(number_font.getname(), ('BankGothic', 'Bold'))
        for image, x, y, direction, boundary, *_ in card['layers']:
            visible, _, _ = video.reveal_text(image, x, y, direction, boundary, 0)
            self.assertFalse(visible.width and visible.height)
            visible, px, py = video.reveal_text(image, x, y, direction, boundary, .5)
            edge = boundary+(-8 if direction == 'up' else 8)
            if direction == 'right': self.assertGreaterEqual(px, edge)
            elif direction == 'up': self.assertLessEqual(py+visible.height, edge)
            else: self.assertGreaterEqual(py, edge)
            visible, px, py = video.reveal_text(image, x, y, direction, boundary, 1)
            self.assertEqual((visible.size, px, py), (image.size, x, y))
        hold = video.render_frame(card, 4, 12)
        self.assertEqual(hold.getpixel((1200, 500))[3], 0)
        self.assertEqual(hold.getpixel((120, 970)), video.BLUE)
        self.assertIsNone(hold.getchannel('A').crop((0, 0, video.W, 812)).getbbox())
        self.assertIsNone(video.render_frame(card, 0, 12).getbbox())
        self.assertIsNone(video.render_frame(card, 11.99, 12).getbbox())

    def test_single_line_marquee_keeps_all_text_and_holds_both_ends(self):
        item = dict(self.data['items'][0])
        item['summary'] = '第一段摘要内容。第二段摘要内容。'*12
        card = video.make_card(item, 1)
        self.assertEqual(len(card['title_lines']), 1)
        self.assertEqual(len(card['summary_lines']), 1)
        self.assertEqual(card['summary_lines'][0], item['summary'])
        self.assertGreater(card['scroll_distance'], 0)
        self.assertEqual(video.scroll_offset(card, 4.5), 0)
        self.assertEqual(video.scroll_offset(card, 6), 1.5*video.SCROLL_SPEED)
        end = 4.5+card['scroll_distance']/video.SCROLL_SPEED
        self.assertEqual(video.scroll_offset(card, end+1.5), card['scroll_distance'])
        self.data['items'] = [item]; self.save()
        manifest, _, _ = self.run_fake(seconds=12)
        self.assertGreater(manifest['cards'][0]['seconds'], 12)
        self.assertGreaterEqual(manifest['cards'][0]['seconds']-3, end+1.5)
        frame = video.render_frame(card, 6, manifest['cards'][0]['seconds'])
        outside = frame.crop((video.TEXT_RIGHT+1, 942, video.W, 1030)).getcolors()
        self.assertTrue(all(color in (video.BLUE, (0, 0, 0, 0)) for _, color in outside))

    def test_order_stable_id_cache_and_single_item_revision(self):
        first, count, path = self.run_fake()
        self.assertEqual(count, 3)
        self.assertEqual([r['id'] for r in first['cards']], [i['id'] for i in self.data['items']])
        self.assertEqual([r['ordinal'] for r in first['cards']], [1, 2, 3])
        again, count, _ = self.run_fake()
        self.assertEqual(count, 0)
        self.assertEqual(again, first)
        old_file = path.parent/first['cards'][1]['file']
        self.data['items'][1]['title'] = '修改后的中文标题'; self.save()
        changed, count, _ = self.run_fake(item_id=self.data['items'][1]['id'])
        self.assertEqual(count, 1)
        self.assertNotEqual(changed['cards'][1]['file'], first['cards'][1]['file'])
        self.assertTrue(old_file.is_file())
        self.assertEqual(changed['cards'][0], first['cards'][0])
        self.assertEqual(changed['cards'][2], first['cards'][2])

    def test_multiple_selection_has_continuous_numbers_and_reports_completion(self):
        selected = {self.data['items'][0]['id'], self.data['items'][2]['id']}
        events = []
        manifest, count, _ = self.run_fake(selected_ids=selected, on_progress=events.append)
        self.assertEqual(count, 2)
        self.assertEqual([r.get('ordinal') for r in manifest['cards']], [1, None, 2])
        self.assertEqual([r['selected'] for r in manifest['cards']], [True, False, True])
        self.assertEqual(manifest['selection_policy'], 'checked_items_only')
        self.assertEqual([e['completed'] for e in events if e['status'] == 'done'], [1, 2])
        self.assertTrue(all(e['total'] == 2 for e in events))
        events.clear()
        _, count, _ = self.run_fake(selected_ids=selected, on_progress=events.append)
        self.assertEqual(count, 0)
        self.assertEqual([e['status'] for e in events], ['skipped', 'skipped'])
        with self.assertRaises(ValueError): self.run_fake(selected_ids=set())

    def test_unselected_overflow_does_not_block_selected_news(self):
        self.data['items'][0]['title'] = '超长标题'*1000
        self.save()
        manifest, count, _ = self.run_fake(selected_ids={self.data['items'][2]['id']})
        self.assertEqual(count, 1)
        self.assertFalse(manifest['cards'][0]['current'])
        self.assertEqual(manifest['cards'][2]['ordinal'], 1)

    def test_selection_change_regenerates_changed_numbers_and_retains_single_card_context(self):
        first, _, path = self.run_fake()
        ids = [item['id'] for item in self.data['items']]
        old_files = [path.parent/row['file'] for row in first['cards']]
        selected = {ids[0], ids[2]}
        changed, count, _ = self.run_fake(selected_ids=selected)
        self.assertEqual(count, 1)
        self.assertEqual([r['ordinal'] for r in changed['cards']], [1, None, 2])
        self.assertFalse(changed['cards'][1]['current'])
        self.assertEqual(changed['cards'][0]['file'], first['cards'][0]['file'])
        self.assertNotEqual(changed['cards'][2]['file'], first['cards'][2]['file'])
        self.assertTrue(all(path.is_file() for path in old_files))
        self.data['items'][2]['title'] = '保持选中编号的单卡修改'
        self.save()
        single, count, _ = self.run_fake(item_id=ids[2])
        self.assertEqual(count, 1)
        self.assertEqual([r['ordinal'] for r in single['cards']], [1, None, 2])
        self.assertEqual(single['cards'][0], changed['cards'][0])
        self.assertEqual(single['cards'][1], changed['cards'][1])
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '未在上次勾选范围内'):
            self.run_fake(item_id=ids[1])
        self.assertEqual(path.read_bytes(), before)
        only_last, count, _ = self.run_fake(selected_ids={ids[2]})
        self.assertEqual(count, 1)
        self.assertEqual([r['ordinal'] for r in only_last['cards']], [None, None, 1])
        complete, count, _ = self.run_fake()
        self.assertEqual(count, 1)  # Unchecked files are retained and reusable when their old numbers return.
        self.assertEqual([r['ordinal'] for r in complete['cards']], [1, 2, 3])

    def test_invalid_saved_selection_is_rejected_without_output_change(self):
        _, _, path = self.run_fake()
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['cards'][0]['selected'] = 'false'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '勾选状态无效'):
            self.run_fake(item_id=self.data['items'][0]['id'])
        self.assertEqual(path.read_bytes(), original)

    def test_packaged_renderer_fingerprint_uses_bundled_source(self):
        self.run_fake()
        source = Path(video.__file__)
        with patch.object(video, '__file__', str(self.root/'not-on-disk.pyc')), \
             patch.object(video, 'bundled_resource_path', return_value=source):
            _, count, _ = self.run_fake()
        self.assertEqual(count, 0)

    def test_corrupt_cached_video_regenerates_only_that_card(self):
        first, _, path = self.run_fake()
        (path.parent/first['cards'][0]['file']).write_bytes(b'corrupted')
        after, count, _ = self.run_fake()
        self.assertEqual(count, 1)
        self.assertNotEqual(first['cards'][0]['file'], after['cards'][0]['file'])

    def test_partial_selection_records_pending_cards(self):
        data, count, _ = self.run_fake(item_id=self.data['items'][1]['id'])
        self.assertEqual(count, 1)
        self.assertFalse(data['cards'][0]['current'])
        self.assertTrue(data['cards'][1]['current'])
        self.assertFalse(data['cards'][2]['current'])
        self.assertEqual(data['cards'][1]['ordinal'], 2)
        complete, count, _ = self.run_fake()
        self.assertEqual(count, 2)
        self.assertTrue(all(card['current'] for card in complete['cards']))

    def test_invalid_time_id_or_overflow_creates_no_outputs(self):
        for kwargs in [{'seconds': float('nan')}, {'seconds': 5}, {'item_id': 'not-found'}, {'sort_order': 'random'}]:
            with self.assertRaises(ValueError): video.generate(self.snapshot, self.output, **kwargs)
            self.assertFalse(self.output.exists())
        self.data['items'][0]['summary'] = '中文'*5000; self.save()
        with self.assertRaises(ValueError): video.generate(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    def test_manifest_failure_preserves_previous_outputs(self):
        first, _, manifest = self.run_fake()
        before = manifest.read_bytes()
        old_files = sorted(p.relative_to(manifest.parent).as_posix() for p in manifest.parent.rglob('*'))
        self.data['items'][0]['title'] = '新版本'; self.save()
        original = Path.replace
        def fail(path, target):
            if path.name.startswith('.manifest.'):
                raise OSError('manifest replacement failed')
            return original(path, target)
        with patch.object(Path, 'replace', fail), self.assertRaises(OSError):
            self.run_fake(item_id=self.data['items'][0]['id'])
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(sorted(p.relative_to(manifest.parent).as_posix() for p in manifest.parent.rglob('*')), old_files)

    def test_bad_manifest_path_is_rejected(self):
        _, _, path = self.run_fake()
        data = json.loads(path.read_text(encoding='utf-8'))
        data['cards'][0]['file'] = '../other.mov'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不安全'): self.run_fake()

    def test_encoder_failure_cleans_temp_reservation_and_log(self):
        process = MagicMock()
        process.stdin.write.side_effect = BrokenPipeError('failed partial write')
        process.poll.return_value = None
        with patch.object(video.subprocess, 'Popen', return_value=process):
            with self.assertRaises(RuntimeError):
                video.encode_card(video.make_card(self.data['items'][0], 1), self.output, 180, 'fake', 'fake')
        self.assertEqual(list(self.output.iterdir()), [])
        process.kill.assert_called_once()

    def test_cancel_during_encoding_keeps_old_file(self):
        self.output.mkdir(); old = self.output/'previous.mov'; old.write_bytes(b'keep')
        token = CancellationToken(); process = MagicMock()
        process.stdin.write.side_effect = lambda _: token.cancel()
        process.poll.return_value = None
        with patch.object(video.subprocess, 'Popen', return_value=process):
            with self.assertRaises(TaskCancelledError):
                video.encode_card(video.make_card(self.data['items'][0], 1), self.output, 180, 'fake', 'fake', token.check)
        self.assertEqual(list(self.output.iterdir()), [old])
        self.assertEqual(old.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
