"""Offline check: bundled yt-dlp downloads self-generated streams and merges them."""
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.cancellation import no_cancel
from services.video_download import _run_process

bundle = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'dist/v3.0.2-ffmpeg-fix/SpaceWeekly'
internal = bundle / '_internal'
ffmpeg = internal / 'ffmpeg/ffmpeg.exe'
ffprobe = internal / 'ffmpeg/ffprobe.exe'
backend = internal / 'video-backend/yt-dlp.exe'
assert all(path.is_file() for path in (ffmpeg, ffprobe, backend)), 'Bundled tools missing'

def run(command):
    lines = []
    _run_process([str(item) for item in command], no_cancel, lines.append, timeout=60)
    return lines

os.environ['PATH'] = os.pathsep.join([str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']])
os.environ.pop('NoDefaultCurrentDirectoryInExePath', None)
with patch.object(sys, '_MEIPASS', str(internal), create=True):
    runpy.run_path(str(ROOT / 'tools/packaging/runtime_ffmpeg.py'))

requests = []
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        requests.append(self.path)
        super().do_GET()

(ROOT / 'build').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='媒体 后端检查 ', dir=ROOT / 'build') as directory:
    base = Path(directory)
    original = Path.cwd()
    try:
        os.chdir(base)
        for name in ('ffmpeg.exe', 'ffprobe.exe'):
            (base / name).write_bytes(b'invalid working-directory executable')
        assert Path(shutil.which('ffmpeg')).samefile(ffmpeg)
        assert Path(shutil.which('ffprobe')).samefile(ffprobe)
        video, audio = base / 'video.mp4', base / 'audio.m4a'
        run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x54:r=10',
             '-t', '1', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1', video])
        run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
             '-t', '1', '-vn', '-c:a', 'aac', audio])
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(base)))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:' + str(server.server_address[1])
            info = dict(id='owned-local-fixture', title='本机 FFmpeg 集成测试', extractor='generic',
                extractor_key='Generic', webpage_url=url + '/owned-fixture', duration=1,
                formats=[
                    dict(format_id='v1', url=url+'/video.mp4', ext='mp4', protocol='http',
                         vcodec='avc1', acodec='none', width=96, height=54, fps=10),
                    dict(format_id='a1', url=url+'/audio.m4a', ext='m4a', protocol='http',
                         vcodec='none', acodec='mp4a.40.2')])
            info_path = base / 'fixture.info.json'
            info_path.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
            merged = base / '合并结果.mp4'
            log = run([backend, '--ignore-config', '--no-cache-dir', '--no-playlist',
                '--no-simulate', '--proxy', '', '--load-info-json', info_path, '-f', 'v1+a1',
                '--merge-output-format', 'mp4', '--ffmpeg-location', ffmpeg.parent,
                '--output', merged, '--verbose', '--newline'])
            transcript = '\n'.join(log)
            assert '[Merger]' in transcript, transcript
            assert str(ffmpeg.parent).replace('\\', '/').casefold() in transcript.replace('\\', '/').casefold(), transcript
            assert {'/video.mp4', '/audio.m4a'}.issubset(requests), requests
            probe = json.loads('\n'.join(run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                                             '-of', 'json', merged])))
            codecs = {stream['codec_name'] for stream in probe['streams']}
            assert codecs == {'h264', 'aac'}, probe
            assert .9 <= float(probe['format']['duration']) <= 1.2, probe
            run([ffmpeg, '-v', 'error', '-i', merged, '-f', 'null', '-'])
            print(json.dumps(dict(result='passed', scope='offline owned local streams; no platform access',
                ffmpeg=str(ffmpeg), ffprobe=str(ffprobe), backend=str(backend),
                ffmpeg_sha256=hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
                ffprobe_sha256=hashlib.sha256(ffprobe.read_bytes()).hexdigest(),
                codecs=sorted(codecs), duration=probe['format']['duration'],
                bytes=merged.stat().st_size, downloads=requests,
                working_directory_shadow='blocked', merged_and_decoded=True), indent=2), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    finally:
        os.chdir(original)
