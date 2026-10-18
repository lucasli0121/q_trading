# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

选 onedir 而不是 onefile：

- onefile 每次启动都把内容解压到临时目录。这个进程带着 pandas / pyarrow
  （解压后几百 MB），启动要等数秒；onedir 直接加载，无解压开销。
- onedir 下 cfg/ 与可执行文件同级，运维改配置不需要重新打包。

产出的目录结构：

    q_trading_archive/
    ├── q_trading_archive          # 入口可执行文件
    ├── cfg/ -> 由部署时放在包根，见下方 datas 说明
    └── _internal/                 # 运行库、cfg 模板副本、log 模板
"""

block_cipher = None

# 只投放 log.yaml 与配置模板。
#
# 真实 cfg/stock.cfg 含 mongo 凭据，不进产物也不进版本库（见 .gitignore）。
# 部署时在包根放一份、由运维填写；archive/config.py 的 candidate_cfg_paths()
# 会把包根排在 _internal 之前，所以运维改的一定是生效的那份。
datas = [
    ('cfg/log.yaml', 'cfg'),
    ('cfg/stock.cfg.example', 'cfg'),
]

import importlib.util

from PyInstaller.utils.hooks import collect_submodules


def _pure_python_submodules(package: str) -> list:
    """列出包内所有在磁盘上以 .py 形式存在的子模块。

    numpy 自带的 hook（`numpy/_pyinstaller/hook-numpy.py`）只声明了两个
    它已知静态分析不到的子模块，假设其余都能被自动发现。这个假设在
    numpy 2.5 上不成立：实测产物缺 `numpy._core._exceptions`（纯 Python 模块），
    后果是进程启动时 `import numpy` 直接失败，而 PyInstaller 构建阶段不报错。

    这里按实际安装内容补齐，而不是硬编码名单，升版后不需要维护。
    只收纯 Python 模块：C 扩展由各包 hook 的 collect_dynamic_libs 负责落盘，
    把扩展名混进 hiddenimports 只会换来一堆「找不到模块」的噪声。
    """

    def _keep(name: str) -> bool:
        if ".tests" in name or name.endswith("_tests") or name.endswith(".conftest"):
            return False
        # numpy 自带的 hook 脚本与构建期代码，不是运行时依赖
        if name.startswith("numpy.f2py") or name.startswith("numpy._pyinstaller"):
            return False
        try:
            spec = importlib.util.find_spec(name)
        except Exception:  # noqa: BLE001 - 探测不到就当作不需要
            return False
        return bool(spec and spec.origin and spec.origin.endswith(".py"))

    return collect_submodules(package, filter=_keep)


hiddenimports = [
    # mongo：写入侧只用 MongoClient，不用 srv / 异步驱动
    'pymongo',
    'bson',
    'gridfs',
    # pandas：parquet 引擎是运行时按名字取的，静态分析容易漏
    'pandas',
    'pandas.io.parquet',
    # pyarrow：to_parquet / read_parquet 的实际执行者，带原生扩展
    'pyarrow',
    'pyarrow.parquet',
    'pyarrow._parquet',
    'pyarrow._compute',
    'pyarrow.lib',
    # 常驻模式的调度器
    'apscheduler',
    'apscheduler.schedulers.blocking',
    'apscheduler.triggers.cron',
    'apscheduler.executors.pool',
    'apscheduler.jobstores.memory',
    # 日志配置与时区
    'yaml',
    'pytz',
    'tzdata',
    'zoneinfo',
    'dateutil',
    # 按实际安装内容补齐的纯 Python 子模块，见 _pure_python_submodules 说明
    *_pure_python_submodules('numpy'),
    *_pure_python_submodules('pandas'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    zipfiles=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 归档进程不碰可视化与交互式分析栈，排除掉能明显减小体积。
        # 这些是 pandas 的软依赖，排掉不影响 parquet 读写。
        'matplotlib',
        'IPython',
        'jupyter',
        'notebook',
        'tkinter',
        'PyQt5',
        'PySide2',
        'PySide6',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='q_trading_archive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # 容器内无 upx；开启只会打印警告并跳过
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
    upx=False,
    upx_exclude=[],
    name='q_trading_archive',
)
