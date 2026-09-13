import re
from typing import Any


def to_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, handling commas and common Chinese units.

    Returns `default` if the value is empty, None, or cannot be converted.
    """
    if value is None or value == "":
        return default
    try:
        normalized = str(value).replace(",", "").replace("元", "").strip()
        return float(normalized)
    except (TypeError, ValueError):
        return default


def guess_stock_code_suffix(code: str) -> str:
    """根据纯数字股票代码推断交易所后缀。

    规则：
    - 60xxxx / 68xxxx → 上证 → .SH
    - 00xxxx / 30xxxx → 深证 → .SZ
    - 4xxxxx / 8xxxxx → 北交所 → .BJ
    - 其他 → 原样返回
    """
    if not re.match(r"^\d{6}$", code):
        return code
    prefix2 = code[:2]
    prefix1 = code[:1]
    if prefix2 in ("60", "68"):
        return code + ".SH"
    elif prefix2 in ("00", "30"):
        return code + ".SZ"
    elif prefix1 in ("4", "8"):
        return code + ".BJ"
    return code