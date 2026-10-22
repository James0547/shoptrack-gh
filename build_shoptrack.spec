# -*- mode: python ; coding: utf-8 -*-
"""
ShopTrack GH — PyInstaller spec.
Outputs dist/ShopTrackGH.exe + dist/data/ (never bundles data/).
Data and logs live NEXT TO the exe so updates never touch them.
"""
import os
from PyInstaller.utils.hooks import collect_submodules

APP_NAME = "ShopTrackGH"
ICON     = "static/img/track.png"

# Bundle read-only assets
datas = [
    ("static", "static"),
    ("templates", "templates"),
]

hidden = (
    collect_submodules("apscheduler")
    + collect_submodules("flask")
    + collect_submodules("werkzeug")
    + ["reportlab", "openpyxl", "sqlite3", "requests"]
)

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,             # no console window on shop PC
    icon=ICON if os.path.exists(ICON) else None,
)