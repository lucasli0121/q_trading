# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for q_trading_cli
Usage: pyinstaller q_trading_cli.spec
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[
        # PyInstaller Linux NSS DNS 解析修复：
        # 显式打包 NSS 库，避免 bootloader 的 RPATH 导致 glibc
        # 无法加载系统路径下的 libnss_dns / libresolv
        ("/usr/lib/x86_64-linux-gnu/libnss_dns.so.2", "."),
        ("/usr/lib/x86_64-linux-gnu/libresolv.so.2", "."),
    ],
    datas=[
        ("cfg", "cfg"),
        ("cert", "cert"),
        ("static", "static"),
        ("resources", "resources"),
    ],
    hiddenimports=[
        # 项目子模块（避免命名冲突，不直接 import workflow 顶层包）
        "api", "api.client", "api.config", "api.error_handler",
        "dao", "dao.order_dao", "dao.strategy_execution_dao", "dao.rt_stocks_dao",
        "factor", "factor.base_factor", "factor.factor_manager", "factor.factor_utils",
        "factor.kline_support_resistance", "factor.in_day_support_resistance",
        "strategy", "strategy.base_strategy", "strategy.swing_trading_strategy",
        "workflow.strategy_workflow",
        "trade", "trade.manager",
        "pages", "pages.login_page", "pages.main_page",
        "components", "menu", "colors", "mq",
        "stock_fetch.akshare_fetch", "stock_fetch.tickflow_fetch",
        "utils", "resources", "backtest", "core",
        # 三方依赖
        "nicegui", "fastapi", "uvicorn", "starlette",
        "paho.mqtt.client", "talib", "talib.stream", "talib.abstract", "talib._ta_lib", "pandas", "numpy",
        "scipy", "scipy.signal", "scipy.signal._peak_finding", "scipy.signal._sigtools",
        "httpx", "pydantic", "yaml",
        # httpx 依赖链（PyInstaller 无法自动追踪动态导入）
        "httpcore", "httpcore._backends", "httpcore._backends.sync",
        "httpcore._backends.auto", "httpcore._sync", "httpcore._sync.connection",
        "httpcore._sync.connection_pool", "httpcore._sync.http11", "httpcore._sync.http_proxy",
        "httpcore._async", "h11", "certifi", "anyio", "idna",
        "apscheduler", "exchange_calendars",
        "pymongo", "redis", "dbutils",
        "multiprocessing", "PySide6",
    ],
    hookspath=["/tmp/pyinstaller-hooks"],
    hooksconfig={},
    runtime_hooks=["runtime_hook.py"],
    excludes=[
        # 不需要的大型三方包
        "matplotlib", "IPython", "jedi", "parso",
        "lxml", "pyarrow", "psutil",
        "jsonschema", "jsonschema_specifications",
        "nbformat", "docutils",
        "setuptools", "pip",
        "debugpy", "pydevd",
        "test", "tests",
        "urllib3", "rich",
        "prompt_toolkit", "traitlets",
        "zmq", "tornado", "uvloop",
        "watchfiles",
        "email_validator",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="q_trading_cli",
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="q_trading_cli",
)
