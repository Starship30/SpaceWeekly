import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


HOOK = Path(__file__).resolve().parents[1] / "tools/packaging/runtime_ffmpeg.py"
TEMP_ROOT = HOOK.parents[2] / "build"
TEMP_ROOT.mkdir(exist_ok=True)


class BundledFFmpegTests(unittest.TestCase):
    def test_bundled_tools_win_over_external_path(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
            root = Path(directory)
            bundled, external = root / "ffmpeg", root / "external"
            bundled.mkdir()
            external.mkdir()
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (bundled / name).touch()
                (external / name).touch()
            with patch.object(sys, "_MEIPASS", str(root), create=True), \
                    patch.dict(os.environ, {"PATH": str(external), "PATHEXT": ".EXE"}):
                runpy.run_path(str(HOOK))
                self.assertEqual(os.environ["PATH"], str(bundled) + os.pathsep + str(external))
                for name in ("ffmpeg.exe", "ffprobe.exe"):
                    self.assertEqual(Path(shutil.which(name)), bundled / name)

    def test_incomplete_bundle_preserves_external_fallback(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
            root = Path(directory)
            (root / "ffmpeg").mkdir()
            (root / "ffmpeg/ffmpeg.exe").touch()
            external = root / 'external'
            external.mkdir()
            (external / 'ffprobe.exe').touch()
            with patch.object(sys, "_MEIPASS", str(root), create=True), \
                    patch.dict(os.environ, {"PATH": str(external), 'PATHEXT': '.EXE'}):
                runpy.run_path(str(HOOK))
                self.assertEqual(Path(shutil.which('ffmpeg')), root / 'ffmpeg/ffmpeg.exe')
                self.assertEqual(Path(shutil.which('ffprobe')), external / 'ffprobe.exe')

    def test_missing_path_is_supported(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
            root = Path(directory)
            bundled = root / "ffmpeg"
            bundled.mkdir()
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (bundled / name).touch()
            with patch.object(sys, "_MEIPASS", str(root), create=True), \
                    patch.dict(os.environ, {}, clear=True):
                runpy.run_path(str(HOOK))
                self.assertEqual(os.environ["PATH"], str(bundled))


    def test_current_directory_cannot_shadow_bundled_tools(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
            root = Path(directory)
            bundled, working = root / 'ffmpeg', root / 'working'
            bundled.mkdir()
            working.mkdir()
            for name in ('ffmpeg.exe', 'ffprobe.exe'):
                (bundled / name).touch()
                (working / name).touch()
            previous = Path.cwd()
            try:
                os.chdir(working)
                with patch.object(sys, '_MEIPASS', str(root), create=True), \
                        patch.dict(os.environ, {'PATH': '', 'PATHEXT': '.EXE'}):
                    os.environ.pop('NoDefaultCurrentDirectoryInExePath', None)
                    runpy.run_path(str(HOOK))
                    for name in ('ffmpeg', 'ffprobe'):
                        self.assertEqual(Path(shutil.which(name)), bundled / (name+'.exe'))
            finally:
                os.chdir(previous)

    def test_empty_bundle_preserves_external_path(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
            root = Path(directory)
            (root / 'ffmpeg').mkdir()
            with patch.object(sys, '_MEIPASS', str(root), create=True), \
                    patch.dict(os.environ, {'PATH': 'external-tools'}):
                runpy.run_path(str(HOOK))
                self.assertEqual(os.environ['PATH'], 'external-tools')
                self.assertEqual(os.environ['NoDefaultCurrentDirectoryInExePath'], '1')
