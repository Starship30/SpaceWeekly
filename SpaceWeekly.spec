# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
import os
from importlib.util import find_spec

ROOT = Path.cwd()
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

datas = [
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
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="SpaceWeekly",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ROOT / "assets" / "sspo.ico"),
)
