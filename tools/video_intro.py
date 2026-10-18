"""Minimal dated opener; local logo input, no news fetching or host integration."""
import argparse
from bisect import bisect_right
import json
import shutil
import subprocess
import tempfile
import time
from datetime import date
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from services.cancellation import no_cancel
from utils.output_files import reserved_output
from tools.video_cards import W, H, FONT, NUMBER_FONT, fit_text, smooth

FPS, SECONDS = 60, 7
BACKGROUND = (9, 14, 25, 255)
LOGO_MOTIONS = ('fade', 'zoom', 'rise')
VIDEO_LOGOS = ('.mp4', '.mov', '.m4v')


def default_logo():
    logo = Image.new('RGBA', (240, 240))
    draw = ImageDraw.Draw(logo)
    draw.ellipse((34, 34, 206, 206), outline=(227, 235, 255), width=3)
    draw.arc((12, 62, 228, 178), 160, 350, fill=(129, 160, 255), width=5)
    draw.polygon([(120, 60), (132, 105), (177, 120), (132, 135),
                  (120, 180), (108, 135), (63, 120), (108, 105)], fill=(227, 235, 255))
    return logo


def _video_logo(path, preview, cancel_check):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('视频 Logo 需要 ffmpeg。Windows 便携包请完整解压并保留 _internal/ffmpeg/；源码运行请将 ffmpeg 加入 PATH。')
    count = 1 if preview else FPS*SECONDS
    filters = ('scale=240:240:force_original_aspect_ratio=decrease:flags=lanczos,'
               'setsar=1,format=rgba,pad=240:240:(ow-iw)/2:(oh-ih)/2:color=black@0,'
               f'fps={FPS}')
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([ffmpeg, '-hide_banner', '-v', 'error', '-nostdin',
            '-i', str(path), '-t', str(SECONDS), '-vf', filters, '-frames:v', str(count),
            '-an', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic()+(10 if preview else 30)
        try:
            while True:
                cancel_check()
                if time.monotonic() > deadline:
                    raise ValueError('视频 Logo 读取超时，请使用较短或较小的本地素材')
                try:
                    raw, _ = process.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode != 0:
                log.seek(0, 2)
                log.seek(max(0, log.tell()-2000))
                raise ValueError('视频 Logo 解码失败：'+log.read().decode('utf-8', errors='replace'))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    size = 240*240*4
    if not raw or len(raw) % size or len(raw) > count*size:
        raise ValueError('视频 Logo 没有有效画面')
    frames = []
    for offset in range(0, len(raw), size):
        cancel_check()
        frames.append(Image.frombytes('RGBA', (240, 240), raw[offset:offset+size]))
    return dict(frames=frames, ends=[(i+1)/FPS for i in range(len(frames))], animated=True, loop=False)


def load_logo(logo_path=None, preview=False, cancel_check=no_cancel):
    cancel_check()
    if not logo_path:
        return dict(frames=[default_logo()], ends=[1.], animated=False, loop=False)
    path = Path(logo_path)
    if not path.is_file():
        raise ValueError('Logo 必须是存在的本地文件')
    if path.suffix.lower() in VIDEO_LOGOS:
        return _video_logo(path, preview, cancel_check)
    frames, ends, elapsed = [], [], 0.
    with Image.open(path) as image:
        animated = getattr(image, 'is_animated', False)
        index = 0
        while elapsed < SECONDS:
            cancel_check()
            try:
                image.seek(index)
            except EOFError:
                break
            # Pillow composes GIF disposal frames before conversion to RGBA.
            frames.append(ImageOps.contain(ImageOps.exif_transpose(image).convert('RGBA'),
                                          (240, 240), Image.Resampling.LANCZOS))
            elapsed += max(.01, float(image.info.get('duration') or 100)/1000)
            ends.append(elapsed)
            if preview or not animated:
                break
            index += 1
    return dict(frames=frames, ends=ends, animated=animated, loop=animated)


def prepare(date_text, title='航天周报', subtitle='SPACEWEEKLY', logo_path=None, end_date=None,
            logo_motion='fade', logo_data=None, cancel_check=no_cancel):
    cancel_check()
    if logo_motion not in LOGO_MOTIONS:
        raise ValueError('无效的静态 Logo 动效')
    start = date.fromisoformat(date_text)
    formatted = start.strftime('%Y.%m.%d')
    if end_date is not None:
        end = date.fromisoformat(end_date)
        if end < start:
            raise ValueError('结束日期不能早于开始日期')
        formatted += ' - '+end.strftime('%Y.%m.%d')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('片头标题不能为空')
    heading, _, _ = fit_text(title, 880, 110, 84, 40)
    label, _, _ = fit_text(subtitle, 880, 35, 24, 20)
    stamp, _, _ = fit_text(formatted, 880, 62, 42, 34, NUMBER_FONT)
    asset = logo_data if logo_data is not None else load_logo(logo_path, cancel_check=cancel_check)
    return {'logo': asset['frames'][0], 'logo_frames': asset['frames'], 'logo_ends': asset['ends'],
            'logo_animated': asset['animated'], 'logo_loop': asset['loop'], 'logo_motion': logo_motion,
            'heading': heading, 'label': label, 'date': stamp,
            'date_text': formatted, 'title': title, 'subtitle': subtitle}


def render_frame(card, t):
    frame = Image.new('RGBA', (W, H), BACKGROUND)
    graphics = Image.new('RGBA', (W, H))
    fade = smooth(t, .15, .8)*(1-smooth(t, 6.1, .75))
    logo_progress = smooth(t, .2, 1.1)
    elapsed = max(0, t-.2)
    if card['logo_loop']:
        elapsed %= card['logo_ends'][-1]
    # Float rounding on the frame grid must not repeat the preceding frame.
    index = min(bisect_right(card['logo_ends'], elapsed+1e-9), len(card['logo_frames'])-1)
    logo = card['logo_frames'][index]
    motion = 'fade' if card['logo_animated'] else card['logo_motion']
    scale = .90+.10*logo_progress if motion == 'zoom' else 1.
    y = 140+18*(1-logo_progress) if motion == 'rise' else 140.
    # Fixed canvas and fractional sampling keep odd/even sizes from shifting the centre.
    resized = logo.transform((280, 280), Image.Transform.AFFINE,
        (1/scale, 0, logo.width/2-140/scale, 0, 1/scale, logo.height/2-y/scale),
        resample=Image.Resampling.BICUBIC)
    graphics.alpha_composite(resized, (480, 390))
    draw = ImageDraw.Draw(graphics)
    line = smooth(t, .65, .9)
    draw.line((790, 530-round(112*line), 790, 530+round(112*line)), fill=(99, 119, 157), width=2)
    for name, x, y, start in [('label', 850, 416, .8), ('heading', 850, 474, 1.0), ('date', 850, 591, 1.45)]:
        progress = smooth(t, start, .85)
        layer = card[name].copy()
        layer.putalpha(layer.getchannel('A').point(lambda value: round(value*progress)))
        graphics.alpha_composite(layer, (x+round(26*(1-progress)), y))
    graphics.putalpha(graphics.getchannel('A').point(lambda value: round(value*fade)))
    frame.alpha_composite(graphics)
    return frame


def generate(output, date_text, title='航天周报', subtitle='SPACEWEEKLY', logo_path=None,
             cancel_check=no_cancel, on_progress=None, end_date=None, logo_motion='fade'):
    cancel_check()
    card = prepare(date_text, title, subtitle, logo_path, end_date=end_date,
                   logo_motion=logo_motion, cancel_check=cancel_check)
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('未找到 ffmpeg 或 ffprobe。Windows 便携包请完整解压并保留 _internal/ffmpeg/；源码运行请将两者加入 PATH。')
    with reserved_output(Path(output), '.intro.mp4') as (final, temporary):
        log_path = temporary.with_suffix('.log')
        process = None
        try:
            with log_path.open('wb') as log:
                process = subprocess.Popen([ffmpeg, '-hide_banner', '-y', '-f', 'rawvideo',
                    '-pixel_format', 'rgba', '-video_size', f'{W}x{H}', '-framerate', str(FPS),
                    '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-threads', '4', str(temporary)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                for i in range(FPS*SECONDS):
                    cancel_check()
                    process.stdin.write(render_frame(card, i/FPS).tobytes())
                    if on_progress and (i+1) % FPS == 0:
                        on_progress((i+1)/(FPS*SECONDS))
                process.stdin.close()
                while process.poll() is None:
                    cancel_check()
                    try:
                        process.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode != 0:
                    raise RuntimeError(log_path.read_text(encoding='utf-8', errors='replace')[-2000:])
            cancel_check()
            info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name,width,height,nb_frames,r_frame_rate:format=duration',
                '-of', 'json', str(temporary)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
            stream = info['streams'][0]
            if (stream['codec_name'] != 'h264' or (stream['width'], stream['height']) != (W, H)
                    or int(stream['nb_frames']) != FPS*SECONDS or stream['r_frame_rate'] != '60/1'
                    or abs(float(info['format']['duration'])-SECONDS) > .05):
                raise RuntimeError('片头编码后的格式、帧数或时长不正确')
            cancel_check()
        finally:
            if process is not None and process.poll() is None:
                process.kill(); process.wait()
            if process is not None and process.stdin is not None:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            log_path.unlink(missing_ok=True)
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=(date.today()-timedelta(days=6)).isoformat(), help='开始日期')
    parser.add_argument('--end-date', default=date.today().isoformat(), help='结束日期（含当天）')
    parser.add_argument('--title', default='航天周报')
    parser.add_argument('--subtitle', default='SPACEWEEKLY')
    parser.add_argument('--logo', type=Path)
    parser.add_argument('--logo-motion', choices=LOGO_MOTIONS, default='fade')
    parser.add_argument('--output', type=Path, default=Path('output/video-intro'))
    args = parser.parse_args()
    print(generate(args.output, args.date, args.title, args.subtitle, args.logo,
                   end_date=args.end_date, logo_motion=args.logo_motion))
