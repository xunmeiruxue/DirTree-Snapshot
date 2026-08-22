# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the DirTree GUI single-file executable.

Build:
    py -3 -m PyInstaller --clean dirtree_gui.spec

Output:
    dist/dirtree-gui.exe
"""

import os

block_cipher = None

a = Analysis(
    ['dirtree_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('dirtree_assets/*.css', 'dirtree_assets'),
        ('dirtree_assets/*.js', 'dirtree_assets'),
        ('dirtree_assets/*.svg', 'dirtree_assets'),
        ('dirtree_assets/*.html', 'dirtree_assets'),
        ('dirtree_assets/__init__.py', 'dirtree_assets'),
    ],
    hiddenimports=['tkinter'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='dirtree-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
