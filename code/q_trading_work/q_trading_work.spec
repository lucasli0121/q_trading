# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('./cfg/*', './cfg'),
        ('./log', './log'),
    ],
    zipfiles=[],
    hiddenimports=[
        'pymongo', 'pytz', 'tzdata', 'dateutil', 'zoneinfo',
        'talib', 'talib.stream', 'talib.abstract', 'talib._ta_lib',
        'apscheduler', 'httpx', 'paho.mqtt', 'itchat',
        'redis', 'scipy', 'yaml',
    ],
    hookspath=['/tmp/pyinstaller-hooks'],
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
    [],
    exclude_binaries=True,
    name='q_trading_work',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='q_trading_work',
)
