# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
import os
from importlib.util import find_spec
import zipfile

if sys.version_info < (3, 12):
    raise SystemExit("Windows packaging requires Python 3.12+ for bundled tool lookup")

ROOT = Path.cwd()
ONEFILE = os.environ.get("SPACEWEEKLY_ONEFILE") == "1"

VIDEO_BACKEND = Path(os.environ.get(
    "SPACEWEEKLY_VIDEO_BACKEND", str(ROOT / "build" / "video-backend" / "yt-dlp.exe")))
if not VIDEO_BACKEND.is_file():
    raise SystemExit("Missing yt-dlp.exe: see tools/PACKAGING.md")
FFMPEG_DIR = ROOT / "build" / "ffmpeg"
FFMPEG_FILES = ["ffmpeg.exe", "ffprobe.exe", "LICENSE", "README.txt"]
if any(not (FFMPEG_DIR / name).is_file() for name in FFMPEG_FILES):
    raise SystemExit("Missing FFmpeg bundle: see tools/PACKAGING.md")
if any(not (ROOT / 'assets' / 'fonts' / name).is_file()
       for name in ('NotoSansCJKsc-Bold.otf', 'OFL.txt')):
    raise SystemExit("Missing bundled font/license: see assets/fonts/README.md")
# Resolve native dependencies from Python/Qt and Windows, never unrelated PATH tools.
os.environ["PATH"] = os.pathsep.join([
    sys.base_prefix,
    str(Path(sys.base_prefix) / "DLLs"),
    str(Path(find_spec("PySide6").origin).parent),
    str(Path(find_spec("shiboken6").origin).parent),
    str(Path(os.environ["SystemRoot"]) / "System32"),
    os.environ["SystemRoot"],
])
PYTHON_DLLS = Path(sys.base_prefix) / "DLLs"
# Prevent PyInstaller from resolving Python's _ssl dependencies to an
# incompatible OpenSSL installation found earlier on PATH (for example Git).
SSL_BINARIES = [
    (str(dll), ".")
    for pattern in ("libssl*.dll", "libcrypto*.dll")
    for dll in PYTHON_DLLS.glob(pattern)
]

# zipimport/importlib.resources preserve every timezone without extracting 600 files.
TZDATA_ROOT = Path(find_spec("tzdata").origin).parent
TZDATA_ARCHIVE = ROOT / "build" / "tzdata.zip"
TZDATA_ARCHIVE.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(TZDATA_ARCHIVE, "w", zipfile.ZIP_DEFLATED) as archive:
    for source in sorted(TZDATA_ROOT.rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            archive.write(source, source.relative_to(TZDATA_ROOT.parent).as_posix())
with zipfile.ZipFile(TZDATA_ARCHIVE) as archive:
    assert archive.testzip() is None

datas = [
    *[(str(FFMPEG_DIR / name), "ffmpeg") for name in FFMPEG_FILES],
    (str(TZDATA_ARCHIVE), "."),
    (str(VIDEO_BACKEND), "video-backend"),
    (str(VIDEO_BACKEND.with_name("yt-dlp-LICENSE.txt")), "video-backend"),
    (str(ROOT / "tools" / "video_cards.py"), "tools"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "language"), "language"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ROOT / "translator" / "dictionary.json"), "translator"),
    (str(ROOT / "translator" / "pending_terms.json"), "translator"),
    (str(ROOT / "translator" / "translation_cache.json"), "translator"),
    (str(ROOT / "ui" / "style.qss"), "ui"),
    (str(ROOT / "config.json"), "."),
    (str(ROOT / "categories.json"), "."),
    (str(ROOT / "feeds.json"), "."),
]

# This runtime loads its TLS distrust policy beside frozen ssl.py.
ssl_policy = Path(sys.base_prefix) / 'Lib' / 'codex-rejected-cas.pem'
if ssl_policy.is_file():
    datas.append((str(ssl_policy), '.'))

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=SSL_BINARIES,
    datas=datas,
    hiddenimports=[
        "bs4",
        "docx",
        "feedparser",
        "requests",
        "PySide6",
        "qrcode.image.pil",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "tools" / "packaging" / name)
                   for name in ("runtime_tzdata.py", "runtime_ffmpeg.py")],
    # Pillow/requests probe these optional packages; SpaceWeekly does not use them.
    excludes=["numpy", "OpenSSL", "cryptography"],
    noarchive=False,
)
# The interface uses raster QWidget painting, not QML, PDF rendering or OpenGL.
UNUSED_QT_BINARIES = {
    "opengl32sw.dll", "qtvirtualkeyboardplugin.dll", "qpdf.dll",
    "Qt6VirtualKeyboard.dll", "Qt6Pdf.dll", "Qt6Quick.dll", "Qt6OpenGL.dll",
    "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll",
}
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name not in UNUSED_QT_BINARIES]
a.pure = [entry for entry in a.pure
          if entry[0] != "tzdata" and not entry[0].startswith("tzdata.")]
# The app installs its own JSON translations, without a Qt QTranslator.
a.datas = [entry for entry in a.datas
           if not entry[0].replace("\\", "/").startswith(
               ("tzdata/", "PySide6/translations/"))]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if ONEFILE else [],
    a.datas if ONEFILE else [],
    name="SpaceWeekly",
    exclude_binaries=not ONEFILE,
    contents_directory="_internal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ROOT / "assets" / "sspo.ico"),
)

if not ONEFILE:
    collect = COLLECT(exe, a.binaries, a.datas,
                      strip=False, upx=True, name="SpaceWeekly")
