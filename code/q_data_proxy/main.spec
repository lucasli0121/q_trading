# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件 — q_data_proxy 数据代理服务

构建命令:
    pyinstaller main.spec --clean

输出目录: dist/q_data_proxy/
可执行文件: dist/q_data_proxy/q_data_proxy
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

block_cipher = None

# Anaconda Python (Windows) 将 ffi.dll / libbz2.dll / sqlite3.dll 等支撑 DLL
# 放在 Library/bin 目录下，PyInstaller 无法自动解析（表现为构建时
# "Library not found: could not resolve 'ffi.dll'" 警告，运行时
# "ImportError: DLL load failed while importing _ctypes"）。
# 此处手动从 sys.base_prefix/Library/bin 收集并放到 _internal 根目录。
_conda_dll_binaries = []
if sys.platform == "win32":
    _conda_bin = os.path.join(sys.base_prefix, "Library", "bin")
    if os.path.isdir(_conda_bin):
        for _dll in ("ffi.dll", "libbz2.dll", "sqlite3.dll"):
            _dll_path = os.path.join(_conda_bin, _dll)
            if os.path.isfile(_dll_path):
                _conda_dll_binaries.append((_dll_path, "."))

_PROJ_ROOT = str(SPECPATH)
_akshare_hidden = collect_submodules("akshare")
_akshare_datas = collect_data_files("akshare")
_xcals_datas = collect_data_files("exchange_calendars")
_mini_racer_hidden = collect_submodules("py_mini_racer")
_mini_racer_datas = collect_data_files("py_mini_racer")
# 过滤非 Linux 二进制文件（macOS .dylib / Windows .dll），减小打包体积
_mini_racer_datas = [(src, dest) for src, dest in _mini_racer_datas
                     if not src.endswith((".dylib", ".dll"))]
# py_mini_racer 的 _get_lib_path() 在 _MEIPASS 根目录查找 .so 文件，
# collect_dynamic_libs 默认放到 py_mini_racer/ 子目录会导致查找失败。
# 此处直接将 Linux .so 放到 _internal 根目录，不再保留子目录副本以节省空间。
_mini_racer_binaries = [
    (src, ".") for src, dest in collect_dynamic_libs("py_mini_racer")
    if src.endswith(".so")
]

a = Analysis(
    ["main.py"],
    pathex=[str(_PROJ_ROOT)],
    binaries=_mini_racer_binaries + _conda_dll_binaries,
    datas=[
        ("cfg/stock.cfg", "cfg"),
        ("cfg/log.yaml", "cfg"),
        # MQTT TLS 证书（cfg/stock.cfg → [mqtt] cert_file/key_file/ca_file 引用 ./cert/）
        ("cert", "cert"),
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
        # 配置
        "yaml",
        # 调度器
        "apscheduler", "apscheduler.schedulers.background",
        # HTTP 客户端
        "urllib3",
        # 日期工具
        "dateutil",
    ] + _akshare_hidden + _mini_racer_hidden,
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
    name="q_data_proxy",
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
    name="q_data_proxy",
)
