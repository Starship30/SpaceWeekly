"""Run after packaging: python tests/check_packaged.py."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from config import app_version
with tempfile.TemporaryDirectory(prefix='spaceweekly-packaged-') as directory:
    marker = Path(directory) / 'started.txt'
    env = dict(os.environ, QT_QPA_PLATFORM=os.environ.get('QT_QPA_PLATFORM', 'windows'), APPDATA=directory)
    result = subprocess.run([str(root/'dist'/'SpaceWeekly.exe'), '--smoke-test', str(marker)], env=env, cwd=directory, timeout=60)
    assert result.returncode == 0, f'Packaged startup failed: {result.returncode}'
    assert marker.exists(), 'Packaged Qt startup did not complete'
    assert marker.read_text(encoding='utf-8').strip() == app_version(), 'Packaged app version mismatch'
print(f'Packaged V{app_version()} Qt startup, dashboard, video dialogs and opener render passed')
