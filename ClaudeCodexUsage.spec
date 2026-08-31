# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — single-file, windowed (no console) tray widget."""
import os

block_cipher = None

datas = [
    ('app-claude.png', '.'),
    ('app-codex.png', '.'),
    ('app-icon.png', '.'),
    ('app-icon.ico', '.'),
]
# Personal icon overrides (git-ignored) ride along when they exist locally.
for extra in ('app-claude-custom.png', 'app-codex-custom.png'):
    if os.path.exists(extra):
        datas.append((extra, '.'))

a = Analysis(
    ['tray.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
        'diagnostics',
        'autostart',
        'paths',
        'tkinter.messagebox',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'scipy', 'pandas', 'matplotlib',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'pytest', 'setuptools', 'pip',
    ],
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
    name='ClaudeCodexUsage',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app-icon.ico',
)
