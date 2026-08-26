# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

ROOT = Path.cwd()
PYTHON_DLLS = Path(sys.base_prefix) / "DLLs"
# Prevent PyInstaller from resolving Python's _ssl dependencies to an
# incompatible OpenSSL installation found earlier on PATH (for example Git).
SSL_BINARIES = [
    (str(PYTHON_DLLS / "libssl-3-x64.dll"), "."),
    (str(PYTHON_DLLS / "libcrypto-3-x64.dll"), "."),
]

datas = [
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
