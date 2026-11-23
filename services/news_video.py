"""Local news footage normalized to a muted, seekable 16:9 panel."""
from functools import lru_cache
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from PIL import Image

from models.news import news_id
from resources import resource_path
from services import news_images
from services.media_process import run_media
from services.roundup_audio import file_hash
from tools.video_cards import FPS

SIZE = (1152, 648)
IMPORT_FPS = 60
FORMATS = {'.mp4', '.mov', '.m4v', '.mkv', '.webm'}


@lru_cache(maxsize=64)
def _checksum(path, size, stamp):
    return file_hash(path)


def validate_asset(asset, *, verify=True):
    if (not isinstance(asset, dict) or not isinstance(asset.get('sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', asset['sha256'])
            or type(asset.get('frames')) is not int or asset['frames'] < 1
            or not isinstance(asset.get('name'), str)
            or not isinstance(asset.get('path'), str)
            or asset.get('fps', FPS) not in (FPS, IMPORT_FPS)
            or Path(asset['path']).name != asset['sha256'] + '.mp4'):
        raise ValueError('新闻视频素材记录无效')
    frame_range(asset)
    path = Path(asset['path'])
    if not verify:
        return path
    if path.parent.resolve() != resource_path('news-images').resolve() or path.is_symlink():
        raise ValueError('新闻视频素材必须位于工作区')
    try:
        stamp = path.stat()
        if _checksum(path, stamp.st_size, stamp.st_mtime_ns) == asset['sha256']:
            return path
    except FileNotFoundError:
        pass
    raise ValueError(f"本条视频素材已缺失或被修改，请重新添加或移除：{asset['name']}")


def selected(identifier):
    data = news_images.load()
    value = data['news'].get(identifier, {}).get('video')
    if value:
        return dict(value, path=str(resource_path('news-images', value['sha256'] + '.mp4')))
    return None


def frame_range(asset):
    start, end = asset.get('in_frame', 0), asset.get('out_frame', asset['frames'])
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= asset['frames']:
        raise ValueError('视频入点必须在出点之前，且两点都应在素材范围内')
    return start, end


def trim(identifier, asset, start, end):
    frame_range(dict(asset, in_frame=start, out_frame=end))
    with news_images.LOCK:
        data = news_images.load()
        current = data['news'].get(identifier, {}).get('video')
        if not current or current['sha256'] != asset['sha256']:
            raise ValueError('当前新闻的视频素材已更换，请重新打开剪裁')
        current.update(in_frame=start, out_frame=end)
        news_images.save(data)


def acquire(article, path, token):
    from services.episode_video import probe
    from tools.video_cards import H264_CLIP_ARGS
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() not in FORMATS:
        raise ValueError('请选择有效的 MP4、MOV、M4V、MKV 或 WebM 视频')
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('添加新闻视频需要 FFmpeg 和 ffprobe')
    directory = resource_path('news-images')
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / ('.video-' + uuid4().hex + '.mp4')
    width, height = SIZE
    try:
        run_media([ffmpeg, '-v', 'error', '-xerror', '-y', '-protocol_whitelist', 'file',
                   '-i', str(source), '-map', '0:V:0', '-an', '-sn', '-dn', '-map_metadata', '-1',
                   '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={IMPORT_FPS}',
                   *H264_CLIP_ARGS, '-threads', '4', str(temporary)], token)
        streams = probe(temporary, ffprobe)
        if len(streams) != 1 or (streams[0]['width'], streams[0]['height']) != SIZE:
            raise ValueError('无法生成新闻视频画面')
        frames = int(streams[0].get('nb_frames', 0))
        if frames < 1:
            raise ValueError('新闻视频没有可播放的画面')
        checksum = file_hash(temporary)
        record = dict(sha256=checksum, frames=frames, name=source.name, fps=IMPORT_FPS)
        target = directory / (checksum + '.mp4')
        identifier = news_id(article.news.key)
        token.check()
        with news_images.LOCK:
            news_images.record(article)
            data = news_images.load()
            if not target.exists():
                temporary.replace(target)
            else:
                validate_asset(dict(record, path=str(target)))
            data.setdefault('videos', {})[checksum] = record
            data['news'][identifier]['video'] = record
            news_images.save(data)
        return record
    finally:
        temporary.unlink(missing_ok=True)


def remove(article):
    with news_images.LOCK:
        data = news_images.load()
        row = data['news'].get(news_id(article.news.key), {})
        if row.pop('video', None):
            news_images.save(data)


class VideoFrames:
    """Keep one decoded frame, with fast seeks for scrubbing and a hold at EOF."""
    def __init__(self, path, frames, size=SIZE, *, fps=FPS, preview_size=None):
        self.path, self.frames, self.size, self.fps = path, frames, preview_size or size, fps
        self.scale = ['-filter_threads','1','-vf',f'scale={self.size[0]}:{self.size[1]}'] if preview_size else []
        self.index, self.last, self.process = -1, None, None

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()
            self.process.stdout.close()
            self.process = None

    def frame(self, seconds):
        index = min(self.frames - 1, max(0, int(seconds * self.fps + 1e-7)))
        if self.index == index:
            return self.last
        # Keep sequential playback cheap when the GUI timer advances by two or three frames.
        if self.process is None or not self.index < index <= self.index + 4:
            self.close()
            ffmpeg = shutil.which('ffmpeg')
            if not ffmpeg:
                raise ValueError('播放新闻视频需要 FFmpeg')
            self.process = subprocess.Popen([ffmpeg, '-v', 'error', '-ss', f'{index / self.fps:.9f}',
                '-i', str(self.path), '-map', '0:v:0', '-an', *self.scale, '-threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.index = index - 1
        size = self.size[0] * self.size[1] * 3
        while self.index < index:
            data = self.process.stdout.read(size)
            if len(data) != size:
                self.close()
                self.index,self.last = -1,None
                raise RuntimeError('新闻视频提前结束或解码失败，请重新添加素材')
            self.index += 1
        self.last = Image.frombytes('RGB', self.size, data).convert('RGBA')
        return self.last
