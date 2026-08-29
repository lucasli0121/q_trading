#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-07-19
Description: 通用工具函数
"""
from __future__ import annotations

import os
import re
import sys
from configparser import ConfigParser
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

def load_admin_token() -> str:
    """从 cfg/stock.cfg 读取 admin_token。

    :return: admin_token 字符串，未配置时返回空字符串
    """
    try:
        from utils.tools import resource_path
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        return cp.get("server", "admin_token", fallback="").strip()
    except Exception:  # noqa: BLE001
        return ""

def load_strategy_type() -> list:
    """从 cfg/stock.cfg 读取 策略类型。"""
    try:
        from utils.tools import resource_path
        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        types_str = cp.get("strategy", "type", fallback="").strip()
        type_list = [s.strip() for s in types_str.split(",")]
        return type_list
    except Exception:  # noqa: BLE001
        return []