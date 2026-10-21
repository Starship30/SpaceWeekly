"""Run after packaging: python tests/check_packaged.py."""
import os
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from config import app_version
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root/'dist'/'SpaceWeekly'/'SpaceWeekly.exe'
ssl_policy = Path(sys.base_prefix) / 'Lib' / 'codex-rejected-cas.pem'
if ssl_policy.is_file():
    bundled_policy = binary.parent / '_internal' / ssl_policy.name
    assert bundled_policy.is_file(), 'TLS policy sidecar missing'
    assert hashlib.sha256(bundled_policy.read_bytes()).digest() == hashlib.sha256(ssl_policy.read_bytes()).digest(), 'TLS policy changed'
(root / 'build').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='spaceweekly-packaged-', dir=root / 'build') as directory:
    marker = Path(directory) / 'started.txt'
    env = dict(os.environ, QT_QPA_PLATFORM=os.environ.get('QT_QPA_PLATFORM', 'windows'), APPDATA=directory)
    system_root = os.environ['SystemRoot']
    env['PATH'] = os.pathsep.join([str(Path(system_root) / 'System32'), system_root])
    env['WINDIR'] = str(Path(directory) / 'no-system-fonts')
    env = {key: value for key, value in env.items() if key.casefold() != 'nodefaultcurrentdirectoryinexepath'}
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        (Path(directory) / name).write_bytes(b'invalid working-directory executable')
    result = subprocess.run([str(binary), '--smoke-test', str(marker)], env=env, cwd=directory, timeout=120)
    trace = marker.with_suffix(".trace")
    phases = trace.read_text(encoding='utf-8') if trace.exists() else 'No Python smoke phase reached'
    error = marker.with_suffix('.error')
    details = error.read_text(encoding='utf-8') if error.exists() else ''
    assert result.returncode == 0, f'Packaged startup failed: {result.returncode}; phases:\n{phases}\n{details}'
    assert 'bundled-fonts-selected' in phases and 'opener-encoded-420frames' in phases, 'Strict font/encoding smoke phases missing'
    assert 'launch-opacity-encoded' in phases and 'simplified-video-controls-and-headline-rule' in phases, 'Video UX checks missing'
    assert 'opener-playback-and-seek' in phases, 'Opener preview playback check missing'
    assert 'll2-status-json-and-reports' in phases and 'launch-status-badges-encoded' in phases, 'LL2 status checks missing'
    assert marker.exists(), 'Packaged Qt startup did not complete'
    assert marker.read_text(encoding='utf-8').strip() == app_version(), 'Packaged app version mismatch'
print(f'Packaged V{app_version()} Qt, video dialogs, opener font fallback/PNG logo, downloads, QR, yt-dlp, bundled FFmpeg/ffprobe, MP4 encoding/merge, alpha MOV, ProRes, Word, TLS, timezones and SVG passed without external tools on PATH')
