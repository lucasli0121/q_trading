"""
Author: liguoqiang
Date: 2026-07-19
Description: 通用工具函数
"""
from __future__ import annotations

from configparser import ConfigParser
import os
import re
import sys
from typing import Any


def resource_path(relative_path: str) -> str:
    """获取资源文件的绝对路径，兼容开发环境和 PyInstaller 打包环境。

    PyInstaller 打包后资源文件存放在 _internal/ 目录（sys._MEIPASS），
    开发环境下资源文件相对于项目根目录。

    :param relative_path: 相对于项目根目录的资源路径，如 cfg/stock.cfg
    :return: 资源的绝对路径
    """
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 环境
        return os.path.join(sys._MEIPASS, relative_path)
    # 开发环境：相对于此文件所在目录的上级（即项目根目录）
    base_dir: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)


_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_UNIT_SCALE: dict[str, float] = {"亿": 1e8, "万": 1e4}


def to_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, handling commas and common Chinese units.

    支持千分位逗号以及常见后缀（元/%/¥/万/亿等），例如：
    "1,234.56" -> 1234.56, "10.5元" -> 10.5, "12.3%" -> 12.3,
    "2.5万" -> 25000.0。解析失败返回 `default`。
    """
    if value is None or value == "":
        return default
    try:
        normalized: str = str(value).replace(",", "").replace("，", "").strip()
        m = _NUMBER_RE.search(normalized)
        if m is None:
            return default
        number: float = float(m.group())
        tail: str = normalized[m.end():]
        for unit, scale in _UNIT_SCALE.items():
            if tail.startswith(unit):
                number *= scale
                break
        return number
    except (TypeError, ValueError):
        return default

def load_service_name() -> str:
    """从 cfg/stock.cfg [workflow] 节读取 service_name。

    :return: service_name 字符串，未配置时返回空字符串
    """
    try:
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        return cp.get("workflow", "service_name", fallback="").strip()
    except Exception:
        return ""


def load_enable_handle_tick() -> bool:
    """从 cfg/stock.cfg [workflow] 节读取 enable_handle_tick。

    实时 tick 通道默认 **关闭**：两个内置策略的 `check_tick_buy` / `check_tick_sell`
    都未实现（恒返回 False），无差别派发只会用空转任务占满线程池并延迟分钟信号。
    将来策略实现 tick 逻辑后，把该项改为 true 即可恢复派发。

    :return: 是否启用 tick 派发；未配置时返回 False
    """
    try:
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        return cp.getboolean("workflow", "enable_handle_tick", fallback=False)
    except Exception:
        return False


def load_order_queue_size() -> int:
    """从 cfg/stock.cfg [workflow] 节读取 order_queue_size（线程池队列上限）。

    :return: 队列最大长度；未配置或非法时返回 512
    """
    try:
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        size: int = cp.getint("workflow", "order_queue_size", fallback=512)
        return size if size > 0 else 512
    except Exception:
        return 512


def load_admin_token() -> str:
    """从 cfg/stock.cfg 读取 admin_token。

    :return: admin_token 字符串，未配置时返回空字符串
    """
    try:
        from utils.tools import resource_path
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        return cp.get("server", "admin_token", fallback="").strip()
    except Exception:
        return ""
