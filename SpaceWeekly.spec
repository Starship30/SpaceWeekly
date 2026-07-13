# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path.cwd()

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "language"), "language"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ROOT / "ui" / "style.qss"), "ui"),
    (str(ROOT / "config.json"), "."),
    (str(ROOT / "feeds.json"), "."),
]

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=[],
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
