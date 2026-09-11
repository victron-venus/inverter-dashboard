# -*- mode: python ; coding: utf-8 -*-
# noqa: F401,F821  # Analysis/PYZ/EXE are injected by PyInstaller.

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['scripts/frozen_entrypoint.py'],
    pathex=['src'],
    binaries=[],
    datas=[('VERSION', '.')] + collect_data_files('inverter_dashboard'),
    hiddenimports=[],
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
    [],
    name='inverter-dashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
