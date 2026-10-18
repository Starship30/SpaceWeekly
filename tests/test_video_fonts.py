import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch
from PIL import ImageFont

from tools import video_cards


ROOT = Path(__file__).resolve().parents[1]


class VideoFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)

    def test_font_selection_uses_windows_directory_and_priority(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as directory:
            fonts = Path(directory) / 'Fonts'
            fonts.mkdir()
            with patch.dict(os.environ, {'WINDIR': directory}):
                (fonts / 'arialbd.ttf').write_bytes(ImageFont.load_default().font_bytes)
                self.assertEqual(video_cards._font_path('bankgothic-bold.ttf', 'arialbd.ttf'), fonts / 'arialbd.ttf')
                (fonts / 'bankgothic-bold.ttf').write_bytes(ImageFont.load_default().font_bytes)
                self.assertEqual(video_cards._font_path('bankgothic-bold.ttf', 'arialbd.ttf'), fonts / 'bankgothic-bold.ttf')

    def test_unavailable_font_is_reported_at_render_time(self):
        with self.assertRaisesRegex(ValueError, '字体.*missing-font.ttf'):
            video_cards.fit_text('2026', 400, 100, 42, 34, ROOT / 'missing-font.ttf')

    def test_corrupt_preferred_font_is_skipped(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as directory:
            fonts = Path(directory) / 'Fonts'
            fonts.mkdir()
            (fonts / 'bankgothic-bold.ttf').write_bytes(b'not a font')
            (fonts / 'arialbd.ttf').write_bytes(ImageFont.load_default().font_bytes)
            with patch.dict(os.environ, {'WINDIR': directory}):
                self.assertEqual(video_cards._font_path('bankgothic-bold.ttf', 'arialbd.ttf'), fonts / 'arialbd.ttf')

    def test_unicode_font_path_uses_memory_without_retaining_file_handles(self):
        with tempfile.TemporaryDirectory(prefix='中文 字体 ', dir=ROOT / 'build') as directory:
            path = Path(directory) / '字体.ttf'
            path.write_bytes(ImageFont.load_default().font_bytes)
            with patch.object(ImageFont, 'truetype', wraps=ImageFont.truetype) as load:
                font = video_cards._load_font(path, 24)
                self.assertIsInstance(load.call_args.args[0], BytesIO)
                self.assertGreater(font.getlength('123'), 0)
            fonts = [video_cards._load_font(path, 16) for _ in range(800)]
            self.assertEqual(len(fonts), 800)
            self.assertEqual(fonts[0].getname(), fonts[-1].getname())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows system font fallback')
    def test_clean_process_renders_intro_news_and_launch_without_bankgothic(self):
        script = '''
import json
from pathlib import Path
from unittest.mock import patch
original_read = Path.read_bytes
def read(path):
    if Path(path).name.lower() == 'bankgothic-bold.ttf':
        raise FileNotFoundError('font unavailable')
    return original_read(path)
with patch.object(Path, 'read_bytes', read):
    from tools import video_cards, video_intro
    assert video_cards.NUMBER_FONT.name.lower() != 'bankgothic-bold.ttf'
    frame = video_intro.render_frame(video_intro.prepare('2026-10-11', end_date='2026-10-17'), 3)
    assert frame.size == (1920, 1080)
    data = json.loads(Path('plugins/premiere-uxp/example.premiere.json').read_text(encoding='utf-8'))
    news = data['items'][0]
    video_cards.make_card(news, 1)
    launch = dict(news, title='测试火箭', summary='测试卫星发射任务', video_type='launch',
                  launch=dict(orbit='低地球轨道', payload='测试卫星', site='发射场', time='2026/10/17 12:00'))
    video_cards.make_card(launch, 1)
    print(video_cards.NUMBER_FONT.name)
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                text=True, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(result.stdout.strip(), ('arialbd.ttf', 'segoeuib.ttf', video_cards.FONT.name))

    def test_all_system_fonts_unavailable_uses_bundled_chinese(self):
        script = '''
from tools import video_cards, video_intro
assert video_cards.FONT == video_cards.BUNDLED_FONT
assert video_cards.NUMBER_FONT == video_cards.BUNDLED_FONT
font = video_cards._load_font(video_cards.FONT, 32)
assert bytes(font.getmask('航')) != bytes(font.getmask('天'))
frame = video_intro.render_frame(video_intro.prepare('2026-10-11', end_date='2026-10-17'), 3)
assert frame.size == (1920, 1080)
news = dict(title='航天新闻', summary='测试航天新闻摘要')
video_cards.make_card(news, 1)
video_cards.make_card(dict(news, title='测试火箭', summary='测试卫星任务', video_type='launch',
    launch=dict(orbit='低地球轨道', payload='测试卫星', site='发射场', time='2026/10/17 12:00')), 1)
print(font.getname())
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
            env=dict(os.environ, WINDIR=str(ROOT / 'build/no-system-fonts')),
            text=True, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Noto Sans CJK SC', result.stdout)
