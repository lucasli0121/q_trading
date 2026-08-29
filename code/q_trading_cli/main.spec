# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


a = Analysis(
    ['main.py',
    'hqbase.py',
    './db/mydb.py',
    './db/base.py',
    './db/mongo/mongo_impl.py',
    './db/mysql/mysql_impl.py',
    './db/redis/redis_impl.py',
    './mq/mq_impl.py',
    './index_fetch/__init__.py',
    './index_fetch/index_hq_wangyi.py',
    './index_fetch/insert_index.py',
    './index_fetch/ak_index_proxy.py',
    './stock_fetch/__init__.py',
    './stock_fetch/ak_stock_proxy.py',
    './utils/uniqueue.py',
    './utils/timeutils.py',
    ],
    pathex=['~/vateran_player/code/fetch_all'],
    binaries=[],
    datas=[('./cfg/*','./cfg'),
    ('./log', './log'),
    ('./dll/*', './'),
    ('./cert/*', './cert'),
    ('./akshare/file_fold/*', './akshare/file_fold')],
    zipfiles=[],
    hiddenimports=['pymongo', 'pytz', 'tzdata', 'dateutil', 'zoneinfo', 'cattr', 'cattr.preconf.bson', 'cattr.preconf.json', 'cattr.preconf.msgpack', 'cattr.preconf.ujson', 'cattr.preconf.orjson', 'cattr.preconf.tomlkit', 'cattr.preconf.pyyaml', 'talib', 'talib.stream', 'talib.abstract', 'talib._ta_lib'],
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
    [],
    exclude_binaries=True,
    name='fetch_stock',
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
    name='fetch_stock',
)
