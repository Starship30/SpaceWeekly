"""Roundup overview/table pages and existing transparent launch cards, PNG MOV."""
import json
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw
from tools import video_cards as cards
from utils.output_files import reserved_output


def _paste_text(canvas, text, box, maximum=42):
    x, y, width, height = box
    image, _, _ = cards.fit_text(text, width, height, maximum, 18)
    canvas.alpha_composite(image, (x, y))


def pages(draft, segment):
    from services.launch_roundup import statistics
    from services.launch_research import confirmed_values
    rows = {i['ll2_id']: i for i in draft['snapshot']['items']}
    ids = segment['task_ids']
    groups = [ids[i:i+7] for i in range(0, len(ids), 7)] or [[]]
    rendered = []
    for number, group in enumerate(groups, 1):
        canvas = Image.new('RGBA', (cards.W, cards.H), (14, 21, 42, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 1920, 14), fill=cards.BLUE)
        _paste_text(canvas, 'SPACEWEEKLY · 每周发射综述', (85, 55, 1750, 85), 56)
        from services.roundup_style import timezone_label
        _paste_text(canvas, f"{timezone_label(draft)} {draft['start']} — {draft['end']}", (85, 150, 1750, 65), 34)
        if segment['kind'] == 'overview' or not group:
            _paste_text(canvas, segment['screen'], (85, 270, 1750, 640), 48)
        else:
            _paste_text(canvas, segment['screen'], (85, 235, 1750, 90), 36)
            for index, identifier in enumerate(group):
                item = rows[identifier]
                name = confirmed_values(item.get('research', {}), item).get('chinese_name') or item['english_name']
                from utils.time import format_beijing_time
                date_text = format_beijing_time(item['net'])[:10]
                draw.rectangle((85, 335 + index*82, 1835, 410 + index*82), fill=(32, 43, 74, 255))
                _paste_text(canvas, f'{date_text}   {name} · {item.get("rocket", "")}', (110, 345 + index*82, 1700, 55), 32)
        if not statistics(draft)['complete']:
            _paste_text(canvas, '当前收录数据 · 非全球完整统计', (85, 980, 1450, 48), 26)
        if len(groups) > 1:
            _paste_text(canvas, f'{number}/{len(groups)}', (1660, 980, 170, 48), 26)
        rendered.append(canvas)
    return rendered


def render_segment(draft, segment, directory, frames, token):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('画面导出需要 FFmpeg/ffprobe；稿件仍可单独导出')
    pictures = pages(draft, segment)
    focus = None
    overlay = None
    if segment['kind'] == 'highlight' and len(segment['task_ids']) == 1:
        from services.launch_research import confirmed_values
        from utils.launch_status import launch_status_text
        from utils.time import format_beijing_time
        item = next(i for i in draft['snapshot']['items'] if i['ll2_id'] == segment['task_ids'][0])
        name = confirmed_values(item.get('research', {}), item).get('chinese_name') or item['english_name']
        focus = cards.make_launch_card(dict(title=item['rocket'], summary=name, video_type='launch',
            launch=dict(orbit=item.get('orbit', ''), payload=item.get('payload', ''), site=item.get('site', ''),
                        time=format_beijing_time(item['net']), status=launch_status_text(
                            dict(id=item.get('status_id'), name=item.get('status_name'))), recovery=item.get('recovery', ''))), 1)
        overlay = Image.new('RGBA', (cards.W, cards.H))
        ImageDraw.Draw(overlay).rounded_rectangle((750, 170, 1840, 360), radius=12, fill=cards.BLUE)
        _paste_text(overlay, segment['screen'], (780, 190, 1030, 150), 42)
    seconds = frames / cards.FPS
    with reserved_output(Path(directory), '.roundup.mov') as (final, temporary):
        process = None
        try:
            with temporary.with_suffix('.log').open('wb') as log:
                process = subprocess.Popen([ffmpeg, '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgba',
                    '-video_size', '1920x1080', '-framerate', str(cards.FPS), '-i', 'pipe:0', '-an',
                    '-c:v', 'png', '-pix_fmt', 'rgba', '-pred', 'mixed', '-threads', '4', str(temporary)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                for frame in range(frames):
                    token.check()
                    if focus:
                        duration = max(12., seconds)
                        image = cards.render_launch_frame(focus, frame/cards.FPS * duration/seconds, duration)
                        image.alpha_composite(overlay)
                    else:
                        image = pictures[min(len(pictures)-1, frame * len(pictures)//frames)]
                    process.stdin.write(image.tobytes())
                process.stdin.close()
                while process.poll() is None:
                    token.check()
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    raise RuntimeError('综述 MOV 编码失败')
            token.check()
            info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(temporary)],
                timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))['streams'][0]
            if (info.get('codec_name') != 'png' or int(info.get('nb_frames', 0)) != frames
                    or (info.get('width'), info.get('height')) != (1920, 1080)):
                raise RuntimeError('综述 MOV 格式或帧数验证失败')
            token.check()
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if process is not None and process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            temporary.with_suffix('.log').unlink(missing_ok=True)
    return final
