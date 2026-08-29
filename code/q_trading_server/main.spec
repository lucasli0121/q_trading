# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件

构建命令:
    pyinstaller main.spec --clean

输出目录: dist/q_trading_server/
可执行文件: dist/q_trading_server/q_trading_server
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

block_cipher = None

_PROJ_ROOT = str(SPECPATH)
_akshare_hidden = collect_submodules("akshare")
_akshare_datas = collect_data_files("akshare")
_xcals_datas = collect_data_files("exchange_calendars")
_uvicorn_hidden = collect_submodules("uvicorn")
_mini_racer_hidden = collect_submodules("py_mini_racer")
_mini_racer_datas = collect_data_files("py_mini_racer")
_mini_racer_binaries = collect_dynamic_libs("py_mini_racer")
# py_mini_racer 的 _get_lib_path() 在 _MEIPASS 根目录查找 .so 文件，
# 但 collect_dynamic_libs 默认放到 py_mini_racer/ 子目录下，导致路径不匹配。
# 此处额外将 .so 文件复制到 _internal 根目录以兼容 _get_lib_path 的查找逻辑。
_mini_racer_binaries_root = [
    (src, ".") for src, _ in _mini_racer_binaries
]
_mini_racer_binaries += _mini_racer_binaries_root

a = Analysis(
    ["main.py"],
    pathex=[str(_PROJ_ROOT)],
    binaries=_mini_racer_binaries,
    datas=[
        ("cfg/stock.cfg", "cfg"),
        ("cfg/log.yaml", "cfg"),
    ] + _xcals_datas + _akshare_datas + _mini_racer_datas,
    hiddenimports=[
        # exchange_calendars
        "exchange_calendars",
        "exchange_calendars.exchange_calendar_xshg",
        # 数据库驱动
        "pymongo", "bson",
        "redis",
        # 时区
        "pytz", "tzdata",
        # MQTT
        "paho.mqtt.client",
        # Web 框架
        "fastapi", "starlette",
        "uvicorn", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        # 配置
        "yaml",
        # 调度器
        "apscheduler", "apscheduler.schedulers.background",
        # HTTP 客户端
        "urllib3",
        # 日期工具
        "dateutil",
    ] + _akshare_hidden + _uvicorn_hidden + _mini_racer_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "scipy", "PIL", "cv2",
        "numpy.tests", "pandas.tests", "cattr", "jinja2",
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
    [],
    exclude_binaries=True,
    name="q_trading_server",
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
    name="q_trading_server",
)
