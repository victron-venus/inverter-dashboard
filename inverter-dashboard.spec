# -*- mode: python ; coding: utf-8 -*-
# noqa: F401,F821  # Analysis/PYZ/EXE are injected by PyInstaller's hook machinery

block_cipher = None

local_modules = [
    'inverter_dashboard',
    'inverter_dashboard.config',
    'inverter_dashboard.version',
    'inverter_dashboard.mqtt_handler',
    'inverter_dashboard.websocket_handler',
    'inverter_dashboard.html_template',
    'inverter_dashboard.ha_client',
    'inverter_dashboard.scripts.docker_healthcheck',
]

a = Analysis(  # noqa: F821
    ['src/inverter_dashboard/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('VERSION', '.', 'DATA'),
    ],
    hiddenimports=local_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='inverter-dashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
