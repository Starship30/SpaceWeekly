"""Render SpaceWeekly protocol 1.0 snapshots into standalone transparent cards."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont
from services.cancellation import TaskCancelledError, no_cancel
from resources import bundled_resource_path
from utils.output_files import reserved_output
from utils.time import parse_datetime

W, H, FPS = 1920, 1080, 30
WHITE, BLUE = (230, 230, 230, 255), (40, 19, 153, 255)
FONT = Path('C:/Windows/Fonts/msyhbd.ttc')
NUMBER_FONT = Path('C:/Windows/Fonts/bankgothic-bold.ttf')
MASK_PADDING = 8
TEXT_LEFT, TEXT_RIGHT = 258, 1830
SCROLL_SPEED = 80  # pixels/second at 1080p; hold both ends before leaving.


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_snapshot(path: Path) -> dict:
    return validate_snapshot(json.loads(path.read_text(encoding='utf-8-sig')))


def validate_snapshot(data: dict) -> dict:
    if not isinstance(data, dict) or data.get('protocol_version') != '1.0':
        raise ValueError('仅支持 SpaceWeekly protocol_version 1.0')
    for key in ('job_id', 'report_title', 'language'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f'缺少非空字符串 {key}')
    if 'launches' in data:
        if not isinstance(data['launches'], list) or not isinstance(data.get('items'), list):
            raise ValueError('items 和 launches 必须为数组')
        data = dict(data)
        data['items'] = data['items']+data.pop('launches')
    if not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('items 必须是非空数组')
    ids = set()
    for index, item in enumerate(data['items'], 1):
        if not isinstance(item, dict):
            raise ValueError(f'第 {index} 条必须是对象')
        if item.get('video_type', 'news') not in ('news', 'launch'):
            raise ValueError(f'第 {index} 条素材类型无效')
        if item.get('video_type') == 'launch':
            if not isinstance(item.get('launch'), dict) or any(
                    not isinstance(item['launch'].get(key), str) for key in ('orbit', 'payload', 'site', 'time')):
                raise ValueError(f'第 {index} 条发射任务缺少轨道、载荷、地点或时间字符串')
        for key in ('id', 'title', 'summary', 'source', 'published', 'url'):
            if not isinstance(item.get(key), str):
                raise ValueError(f'第 {index} 条缺少字符串 {key}')
        for key in ('id', 'title', 'source', 'url'):
            if not item[key].strip():
                raise ValueError(f'第 {index} 条 {key} 为空')
        if not re.fullmatch(r'https?://[^\s/]+(?:/[^\s]*)?', item['url'], re.IGNORECASE):
            raise ValueError(f'第 {index} 条 URL 无效')
        if item['id'] in ids:
            raise ValueError(f'重复新闻 ID：{item["id"]}')
        ids.add(item['id'])
        if not isinstance(item.get('categories'), list) or any(
                not isinstance(x, str) or not x.strip() for x in item['categories']):
            raise ValueError(f'第 {index} 条 categories 必须是字符串列表')
    return data


def card_ordinals(items, selected_ids=None):
    counts = {'news': 0, 'launch': 0}
    ordinals = []
    for item in items:
        if selected_ids is not None and item['id'] not in selected_ids:
            ordinals.append(None)
            continue
        kind = item.get('video_type', 'news')
        counts[kind] += 1
        ordinals.append(counts[kind])
    return ordinals


def sort_items(items, sort_order='asc'):
    if sort_order not in ('asc', 'desc'):
        raise ValueError('排序须为 asc 或 desc')

    def key(item):
        published = parse_datetime(item['published'])
        timestamp = published.timestamp() if published is not None else 0
        return (item.get('video_type') == 'launch', published is None,
                -timestamp if sort_order == 'desc' else timestamp)

    return sorted(items, key=key)


def launch_time_text(value):
    return re.sub(r'\s*(?:UTC|GMT)(?:\s*[+-]\s*\d{1,2}(?::?\d{2})?)?\s*$', '', value,
                  flags=re.IGNORECASE).strip()


def wrap_text(text: str, font, width: int) -> list[str]:
    lines = []
    # Keep normal English words together; an overlong word/URL falls back to characters.
    for paragraph in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = ''
        for token in re.findall(r'[A-Za-z0-9]+(?:[\x27-][A-Za-z0-9]+)*|[^A-Za-z0-9]', paragraph):
            if line and font.getlength(line+token) > width:
                lines.append(line.rstrip())
                line = ''
            for char in token:
                if font.getlength(line+char) > width:
                    if not line:
                        raise ValueError('单个字符超出文本区域')
                    lines.append(line.rstrip())
                    line = ''
                if line or not char.isspace():
                    line += char
        lines.append(line.rstrip())
    return lines


def fit_text(text: str, width: int, height: int, maximum: int, minimum: int,
             font_path: Path = FONT) -> tuple[Image.Image, int, list[str]]:
    for size in range(maximum, minimum-1, -2):
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap_text(text, font, width)
        advance = size+10
        boxes = [font.getbbox(line) for line in lines]
        top = min((box[1] for box in boxes), default=0)
        bottom = max((i*advance+box[3] for i, box in enumerate(boxes)), default=0)
        used_height = bottom-top+4
        if used_height <= height and all(box[2]-box[0]+4 <= width for box in boxes):
            image = Image.new('RGBA', (width, max(1, used_height)))
            draw = ImageDraw.Draw(image)
            for i, line in enumerate(lines):
                draw.text((2, 2-top+i*advance), line, font=font, fill=WHITE)
            return image, size, lines
    raise ValueError(f'文本超出区域：最低字号 {minimum} 仍放不下；请缩短快照中的文本，不会自动截断')


def make_card(item: dict, ordinal: int) -> dict:
    if item.get('video_type') == 'launch':
        return make_launch_card(item, ordinal)
    number, _, _ = fit_text(f'{ordinal:02d}', 148, 52, 82, 62, NUMBER_FONT)
    heading, _, _ = fit_text('SPACEWEEKLY', 148, 30, 18, 18)
    title_text = re.sub(r'\s+', ' ', item['title']).strip()
    summary_text = re.sub(r'\s+', ' ', item['summary']).strip()
    title, title_size, title_lines = fit_text(title_text, TEXT_RIGHT-TEXT_LEFT, 58, 50, 26)
    if len(title_lines) != 1:
        raise ValueError('标题必须能在一行内完整显示，请缩短标题')
    font = ImageFont.truetype(str(FONT), 34)
    summary_width = max(4, math.ceil(font.getlength(summary_text))+8)
    if summary_width > 32768:
        raise ValueError('摘要超过单卡滚动上限（32768px），请缩短展示摘要')
    summary, summary_size, summary_lines = fit_text(summary_text, summary_width, 56, 34, 34)
    return {'layers': [(number, 72, 872, 'up', 932, .70, .35, 1.20),
                       (heading, 72, 958, 'down', 939, .95, .35, 1.45),
                       (title, TEXT_LEFT, 926-title.height, 'up', 934, 1.40, .55, 2.15),
                       (summary, TEXT_LEFT, 962, 'down', 941, 2.10, .85, 3.0)],
            'title_font_size': title_size, 'summary_font_size': summary_size,
            'title_lines': title_lines, 'summary_lines': summary_lines,
            'scroll_distance': max(0, summary.width-(TEXT_RIGHT-TEXT_LEFT))}


def card_frames(card, seconds):
    if not math.isfinite(seconds) or not 6 <= seconds <= 600:
        raise ValueError('时长须为 6 至 600 秒')
    frames = math.floor(seconds*FPS+.5)
    if card.get('video_type') == 'launch':
        return max(frames, 12*FPS)
    return max(frames, math.ceil((9+card['scroll_distance']/SCROLL_SPEED)*FPS)) if card['scroll_distance'] else frames


def smooth(t: float, start: float, duration: float) -> float:
    p = max(0., min(1., (t-start)/duration))
    return p*p*(3-2*p)


def reveal_text(image, x, y, direction, boundary, progress):
    boundary += -MASK_PADDING if direction == 'up' else MASK_PADDING
    if direction == 'right':
        x = round(x-(1-progress)*(x+image.width-boundary))
        left = max(0, min(image.width, boundary-x))
        return image.crop((left, 0, image.width, image.height)), max(x, boundary), y
    if direction == 'up':
        y = round(y+(1-progress)*(boundary-y))
        bottom = max(0, min(image.height, boundary-y))
        return image.crop((0, 0, image.width, bottom)), x, y
    y = round(y-(1-progress)*(y+image.height-boundary))
    top = max(0, min(image.height, boundary-y))
    return image.crop((0, top, image.width, image.height)), x, max(y, boundary)


def scroll_offset(card, t):
    return min(card['scroll_distance'], max(0, (t-4.5)*SCROLL_SPEED))


def render_frame(card: dict, t: float, seconds: float) -> Image.Image:
    if card.get('video_type') == 'launch':
        return render_launch_frame(card, t, seconds)
    canvas = Image.new('RGBA', (W, H))
    draw = ImageDraw.Draw(canvas)
    offset = round(-W*(1-smooth(t, 0, .53))+W*smooth(t, seconds-.8, .67))
    draw.rectangle((48+offset, 812, 1872+offset, 1032), fill=BLUE)
    line = smooth(t, .5, .55)*(1-smooth(t, seconds-1.3, .35))
    if line > 0:
        draw.rounded_rectangle((72, 932, 72+148*line, 939), 3, fill=WHITE)
    line = smooth(t, 1.3, .35)*(1-smooth(t, seconds-2.1, .3))
    if line > 0:
        draw.rounded_rectangle((TEXT_LEFT, 934, TEXT_LEFT+(TEXT_RIGHT-TEXT_LEFT)*line, 941), 3, fill=WHITE)
    line = smooth(t, .6, .35)*(1-smooth(t, seconds-1.1, .35))
    if line > 0:
        draw.rounded_rectangle((232, 844, 238, 844+164*line), 3, fill=WHITE)
    for index, (image, x, y, direction, boundary, start, duration, end_offset) in enumerate(card['layers']):
        progress = smooth(t, start, duration)*(1-smooth(t, seconds-end_offset, duration))
        if progress > 0:
            visible, px, py = reveal_text(image, x, y, direction, boundary, progress)
            if index == 3:
                px -= round(scroll_offset(card, t))
                left = max(0, TEXT_LEFT-px)
                right = min(visible.width, TEXT_RIGHT-px)
                visible = visible.crop((left, 0, max(left, right), visible.height))
                px = max(px, TEXT_LEFT)
            if visible.width and visible.height:
                canvas.alpha_composite(visible, (px, py))
    return canvas


def make_launch_card(item, ordinal):
    layers = []
    def layer(text, x, y, width, height, maximum, minimum, direction, boundary, start, duration, end, font=FONT):
        image, size, lines = fit_text(text or '待确认', width, height, maximum, minimum, font)
        layers.append((image, x, y, direction, boundary, start, duration, end))
        return size, lines
    layer(f'{ordinal:02d}', 46, 38, 148, 52, 82, 62, 'up', 97, .70, .35, 1.55, NUMBER_FONT)
    layer('LAUNCH MISSION', 48, 120, 370, 32, 25, 25, 'down', 104, .95, .35, 1.8)
    title_size, title_lines = layer(item['title'], 46, 174, 395, 60, 54, 30, 'up', 243, 1.4, .4, 2.05)
    summary_size, summary_lines = layer(item['summary'], 46, 260, 410, 66, 34, 22, 'down', 250, 1.4, .4, 2.2)
    for i, (label, key) in enumerate([('目标轨道', 'orbit'), ('任务载荷', 'payload'),
                                     ('发射地点', 'site'), ('发射时间', 'time')]):
        y = 347+i*166
        width = math.floor(456+170*(y+54)/H-18-72)
        layer(label, 72, y, width, 44, 34, 34, 'right', 60, 2.1, .6, 2.85)
        text = launch_time_text(item['launch'][key]) if key == 'time' else item['launch'][key]
        layer(text, 72, y+54, width, 80, 38, 24, 'right', 60, 2.85, 1, 3.75)
    return dict(video_type='launch', layers=layers, title_font_size=title_size, title_lines=title_lines,
                summary_font_size=summary_size, summary_lines=summary_lines, scroll_distance=0)


def render_launch_frame(card, t, seconds):
    canvas = Image.new('RGBA', (W, H))
    draw = ImageDraw.Draw(canvas)
    slope = 170/H
    panel = [(456+slope*(-110), -110), (456+slope*(-110)-800, -110+800*slope),
             (456+slope*1220-800, 1220+800*slope), (456+slope*1220, 1220)]
    bar = [(456+slope*(-110), -110), (558+slope*(-110), -110-102*slope),
           (558+slope*1100, 1100-102*slope), (456+slope*1100, 1100)]
    entry = smooth(t, 0, .53)
    exit = smooth(t, seconds-.8, .67)
    for points, dy, color in [(panel, 1166*(1-entry)+1220*exit, BLUE),
                               (bar, -1066*(1-entry)-1150*exit, WHITE)]:
        draw.polygon([(round(x+slope*dy), round(y+dy)) for x, y in points], fill=color)
    line = smooth(t, .5, .55)*(1-smooth(t, seconds-1.3, .35))
    if line:
        draw.rounded_rectangle((48, 97, 48+365*line, 104), 3, fill=WHITE)
    line = smooth(t, 1.3, .35)*(1-smooth(t, seconds-2.1, .3))
    if line:
        draw.rounded_rectangle((48, 243, 48+118*line, 250), 3, fill=WHITE)
    for i in range(4):
        line = smooth(t, 1.7, .35)*(1-smooth(t, seconds-2.3-i*.12, .35))
        if line:
            y = 347+i*166
            draw.rounded_rectangle((48, y, 60, y+92*line), 6, fill=WHITE)
    for image, x, y, direction, boundary, start, duration, end in card['layers']:
        progress = smooth(t, start, duration)*(1-smooth(t, seconds-end, duration))
        if progress:
            visible, px, py = reveal_text(image, x, y, direction, boundary, progress)
            if visible.width and visible.height:
                canvas.alpha_composite(visible, (px, py))
    return canvas


def verify_video(path: Path, frames: int, seconds: float, ffmpeg: str, ffprobe: str,
                 encoding='png'):
    info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-select_streams', 'v:0',
        *(['-show_frames'] if encoding == 'png' else []),
        '-show_entries', 'stream=codec_name,width,height,pix_fmt,nb_frames:format=duration:frame=key_frame',
        '-of', 'json', str(path)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
    stream = info['streams'][0]
    codec, pixels = ('png', ('rgba',)) if encoding == 'png' else ('prores', ('yuva444p10le', 'yuva444p12le'))
    if ((stream['width'], stream['height']) != (W, H) or stream['codec_name'] != codec
            or stream.get('pix_fmt') not in pixels
            or int(stream['nb_frames']) != frames or abs(float(info['format']['duration'])-seconds) > .05):
        raise RuntimeError('编码后的视频格式、帧数或时长不正确')
    if encoding == 'png' and (len(info.get('frames', [])) != frames or
                                   any(frame.get('key_frame') != 1 for frame in info['frames'])):
        raise RuntimeError('PNG MOV 必须全部为独立关键帧，拒绝 Delta 帧')
    for x, y, expected in [(1200, 500, 0), (120, 970, 255)]:
        alpha = subprocess.check_output([ffmpeg, '-v', 'error', '-ss', '4', '-i', str(path),
            '-frames:v', '1', '-vf', f'alphaextract,crop=2:2:{x}:{y}', '-pix_fmt', 'gray',
            '-f', 'rawvideo', 'pipe:1'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if alpha != bytes([expected])*4:
            raise RuntimeError('编码后的透明通道检查失败')


def encode_card(card, directory, frames, ffmpeg, ffprobe, cancel_check=no_cancel, on_frame=None,
                encoding='png'):
    if encoding not in ('png', 'prores'):
        raise ValueError('未知透明视频编码')
    codec_args = (['-c:v', 'png', '-pix_fmt', 'rgba', '-pred', 'mixed', '-compression_level', '6'] if encoding == 'png' else
                  ['-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16'])
    seconds = frames/FPS
    suffix = '.launch.mov' if card.get('video_type') == 'launch' else '.news.mov'
    with reserved_output(directory, suffix) as (final, temporary):
        log_path = temporary.with_suffix('.log')
        try:
            with log_path.open('wb') as log:
                process = None
                try:
                    process = subprocess.Popen([ffmpeg, '-hide_banner', '-y', '-f', 'rawvideo',
                        '-pixel_format', 'rgba', '-video_size', f'{W}x{H}', '-framerate', str(FPS),
                        '-i', 'pipe:0', '-an', *codec_args, '-threads', '4', str(temporary)],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    for i in range(frames):
                        cancel_check()
                        process.stdin.write(render_frame(card, i/FPS, seconds).tobytes())
                        if on_frame is not None and ((i+1) % FPS == 0 or i+1 == frames):
                            on_frame((i+1)/frames)
                    process.stdin.close()
                    while process.poll() is None:
                        cancel_check()
                        try:
                            process.wait(timeout=.2)
                        except subprocess.TimeoutExpired:
                            pass
                    if process.returncode != 0:
                        raise RuntimeError('FFmpeg 编码失败')
                    cancel_check()
                    verify_video(temporary, frames, seconds, ffmpeg, ffprobe, encoding)
                    cancel_check()
                except BaseException as error:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait()
                    log.flush()
                    details = log_path.read_text(encoding='utf-8', errors='replace')[-2000:]
                    if isinstance(error, (BrokenPipeError, RuntimeError)) and not isinstance(error, TaskCancelledError):
                        raise RuntimeError(f'卡片编码或验证失败：{error}\n{details}') from error
                    raise
                finally:
                    if process is not None and process.stdin is not None:
                        try:
                            process.stdin.close()
                        except BrokenPipeError:
                            pass
        finally:
            log_path.unlink(missing_ok=True)
    return final


def reading_seconds(item):
    text = item['title']+' '+item['summary']
    chinese = len(re.findall(r'[\u3400-\u9fff]', text))
    words = len(re.findall(r'[A-Za-z0-9]+', text))
    # Rough reading estimate, not speech duration; keep the visible warning.
    return chinese/5+words/2.5


def save_manifest(path, payload, cancel_check):
    temporary = path.with_name('.manifest.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        cancel_check()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def manifest_video_path(directory: Path, name: str) -> Path:
    """Accept old basenames and the two new media folders, without escaping the job."""
    parts = name.split('/')
    if (not name or '\\' in name or ':' in name or any(p in ('', '.', '..') for p in parts)
            or len(parts) > 2 or (len(parts) == 2 and parts[0] not in ('news', 'launches'))):
        raise ValueError('执行清单含不安全的文件路径')
    path = directory/name
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError('执行清单含不安全的文件路径')
    return path


def generate(snapshot: Path | dict, output: Path, seconds=12., item_id=None, cancel_check=no_cancel,
             selected_ids=None, on_progress=None, encoding='png', sort_order='asc'):
    cancel_check()
    data = validate_snapshot(snapshot) if isinstance(snapshot, dict) else load_snapshot(snapshot)
    data = dict(data, items=sort_items(data['items'], sort_order))
    if encoding not in ('png', 'prores'):
        raise ValueError('未知透明视频编码')
    if not math.isfinite(seconds) or not 6 <= seconds <= 600:
        raise ValueError('时长须为 6 至 600 秒')
    frames = math.floor(seconds*FPS+.5)
    seconds = frames/FPS
    ids = [item['id'] for item in data['items']]
    if item_id is not None and selected_ids is not None:
        raise ValueError('不能同时指定 item_id 和 selected_ids')
    selected = set(ids) if selected_ids is None and item_id is None else set(selected_ids or [item_id])
    if not selected or not selected.issubset(ids):
        raise ValueError('没有选中新闻，或选中的 ID 不在快照中')
    total, completed = len(selected), 0

    def notify(item, status, fraction=0., message='', file=''):
        if on_progress is not None:
            on_progress(dict(id=item['id'], status=status, completed=completed, total=total,
                             fraction=fraction, message=message, file=file))
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('请将 ffmpeg 和 ffprobe 加入 PATH')
    for path in (FONT, NUMBER_FONT):
        if not path.is_file():
            raise ValueError(f'缺少字体文件：{path}')
    directory = output / hashlib.sha256(data['job_id'].encode('utf-8')).hexdigest()[:16]
    manifest_path = directory/'manifest.json'
    old = {}
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or manifest.get('job_id') != data['job_id'] or not isinstance(manifest.get('cards'), list):
            raise ValueError('执行清单无效，停止以避免误覆盖；请使用新的输出目录')
        for record in manifest['cards']:
            if not isinstance(record, dict) or not isinstance(record.get('id'), str):
                raise ValueError('执行清单中的卡片无效')
            if manifest.get('selection_policy') == 'checked_items_only' and not isinstance(record.get('selected'), bool):
                raise ValueError('执行清单中的勾选状态无效')
            if 'file' not in record and record.get('current') is False:
                if record['id'] in old:
                    raise ValueError('执行清单含重复新闻 ID')
                old[record['id']] = record
                continue
            if not all(isinstance(record.get(key), str) for key in ('fingerprint', 'file', 'sha256')):
                raise ValueError('执行清单中的已生成卡片无效')
            manifest_video_path(directory, record['file'])
            if record['id'] in old:
                raise ValueError('执行清单含重复新闻 ID')
            old[record['id']] = record
    numbered = set(ids) if selected_ids is None else selected
    # Single-card regeneration keeps the last composition's numbering.
    if item_id is not None and manifest.get('selection_policy') == 'checked_items_only':
        numbered = {identifier for identifier in ids if old.get(identifier, {}).get('selected')}
        if item_id not in numbered:
            raise ValueError('该 ID 未在上次勾选范围内；请在视频窗口勾选后生成')
    cards = []
    ordinals = card_ordinals(data['items'], numbered)
    for index, (ordinal, item) in enumerate(zip(ordinals, data['items']), 1):
        cancel_check()
        if ordinal is None:
            cards.append(None)
            continue
        try:
            cards.append(make_card(item, ordinal))
        except ValueError as error:
            if item['id'] not in selected:
                cards.append(None)
                continue
            raise ValueError(f'第 {index} 条（{item["id"]}）：{error}') from error
    frame_counts = [card_frames(card, seconds) if card is not None else None for card in cards]
    source = Path(__file__)
    if not source.is_file():
        source = bundled_resource_path('tools', 'video_cards.py')
    signature = [sha_file(source), sha_file(FONT), sha_file(NUMBER_FONT), encoding]
    fingerprints = [hashlib.sha256(json.dumps([item['id'], item['title'], item['summary'],
                    item.get('video_type', 'news'), item.get('launch'), i,
                    frames, signature], ensure_ascii=False).encode('utf-8')).hexdigest() if frames is not None else None
                    for i, item, frames in zip(ordinals, data['items'], frame_counts)]
    records = []
    for item, fingerprint, ordinal in zip(data['items'], fingerprints, ordinals):
        record = dict(old.get(item['id'], {'id': item['id']}))
        record.update(ordinal=ordinal, video_type=item.get('video_type', 'news'), selected=ordinal is not None)
        path = manifest_video_path(directory, record['file']) if 'file' in record else None
        record['current'] = (ordinal is not None and record.get('fingerprint') == fingerprint and path is not None
                             and path.is_file() and sha_file(path) == record['sha256'])
        records.append(record)
    payload = {'protocol_version': data['protocol_version'], 'job_id': data['job_id'],
               'report_title': data['report_title'], 'order_policy': f'video_type_then_published_{sort_order}',
               'numbering_policy': 'independent_news_and_launch',
               'selection_policy': 'checked_items_only',
               'fps': FPS, 'encoding': encoding, 'cards': records}
    for item, card, fingerprint, record, frames, ordinal in zip(data['items'], cards, fingerprints, records, frame_counts, ordinals):
        cancel_check()
        if item['id'] not in selected:
            continue
        card_seconds = frames/FPS
        if record.get('current'):
            print(f'SKIP {item["id"]}: {record["file"]}', flush=True)
            completed += 1
            notify(item, 'skipped', file=str(directory/record['file']))
            continue
        if card_seconds > seconds:
            message = (f'发射卡完整动画最低 {card_seconds:g} 秒' if card.get('video_type') == 'launch' else
                       f'摘要滚动自动延长至 {card_seconds:g} 秒')
            print(f'INFO {message}', flush=True)
            notify(item, 'info', message=message)
        if reading_seconds(item) > card_seconds-6:
            message = (f'阅读时间估算约 {math.ceil(reading_seconds(item))} 秒，'
                       f'完整停留仅约 {card_seconds-6:g} 秒；可增加最低时长')
            print(f'WARN {item["id"]}: {message}', flush=True)
            notify(item, 'warning', message=message)
        print(f'RENDER {item["id"]}: {item["title"]}', flush=True)
        notify(item, 'rendering')
        on_frame = (lambda fraction: notify(item, 'rendering', fraction*.95)) if on_progress else None
        folder = 'launches' if item.get('video_type') == 'launch' else 'news'
        path = encode_card(card, directory/folder, frames, ffmpeg, ffprobe, cancel_check, on_frame, encoding)
        try:
            record.clear()
            record.update(id=item['id'], ordinal=ordinal, selected=True, title=item['title'],
                          fingerprint=fingerprint, current=True, file=path.relative_to(directory).as_posix(), sha256=sha_file(path),
                          frames=frames, seconds=card_seconds, scroll_distance=card['scroll_distance'], title_font_size=card['title_font_size'],
                          summary_font_size=card['summary_font_size'], encoding=encoding,
                          video_type=item.get('video_type', 'news'))
            save_manifest(manifest_path, payload, cancel_check)
        except BaseException:
            # This path belongs to this attempt; older rendered versions remain intact.
            path.unlink(missing_ok=True)
            raise
        completed += 1
        notify(item, 'done', file=str(path))
        print(f'PASS {path}', flush=True)
    if payload != manifest:
        save_manifest(manifest_path, payload, cancel_check)
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output', type=Path, default=Path('output/video-cards'))
    parser.add_argument('--seconds', type=float, default=12., help='每卡最低时长；摘要滚动时自动延长')
    parser.add_argument('--item-id', help='仅重生成此稳定 ID 的卡片；不使用列表位置作为身份')
    parser.add_argument('--encoding', choices=['png', 'prores'], default='png')
    parser.add_argument('--order', choices=['asc', 'desc'], default='asc', help='各类按原始时间正序或倒序；默认正序')
    args = parser.parse_args()
    try:
        manifest = generate(args.snapshot, args.output, args.seconds, args.item_id,
                            encoding=args.encoding, sort_order=args.order)
    except KeyboardInterrupt:
        print('已取消；本次临时视频已清理，已完成的卡片保留。')
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f'FAIL: {error}；停止，已完成的卡片保留。')
        return 1
    print(f'清单：{manifest}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
