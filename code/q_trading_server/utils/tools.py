import re
from datetime import datetime
from typing import Any

# 库中各类时间字段（create_time / update_time / buy_time 等）统一用这两种写法，
# 且都存成字符串：字典序与时间序一致，所以范围查询直接做字符串比较即可。
TIME_FORMAT_DATE = "%Y-%m-%d"
TIME_FORMAT_DATETIME = "%Y-%m-%d %H:%M:%S"


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

def pure_stock_code(code: str) -> str:
    """从任意写法的股票代码中提取纯数字代码。

    库中统一存储 `xxxx.SH` / `xxxx.SZ` / `xxxx.BJ`，而 akshare 等数据源
    的接口只接受 6 位纯数字代码，所以调用外部接口前必须先剥掉后缀。

    - 600519.SH → 600519
    - sh600519  → 600519
    - 600519    → 600519

    这是纯字符串变换，不依赖任何数据库连接，因此可以安全地在
    AkStockProxy 这类不持有 Mongo 实现的模块里直接调用。
    """
    match = re.search(r"\d+", code)
    return match.group() if match else code

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

def normalize_query_time(value: str, is_end: bool = False) -> str:
    """把查询用的时间边界归一化成库中存储的 "%Y-%m-%d %H:%M:%S" 格式。

    库中时间字段存的是字符串，字典序与时间序一致，因此范围查询直接用
    字符串比较即可，无需转成 datetime 或构造 $expr。

    只传日期（"%Y-%m-%d"）时按边界语义补全时刻，上界的补全不能省：
    库中值是 "2026-09-23 10:00:00"，而 "2026-09-23" 按字典序小于它，
    若直接 `$lte "2026-09-23"`，当天的记录会被全部漏掉。

    :param value: 待归一化的时间字符串，None 或空串表示该边界不限制
    :param is_end: 是否为区间上界（上界补 23:59:59，下界补 00:00:00）
    :return: 归一化后的时间字符串；value 为空时返回空串
    :raises ValueError: 格式无法识别
    """
    if not value or not value.strip():
        return ""
    text = value.strip()
    is_date_only = False
    try:
        parsed = datetime.strptime(text, TIME_FORMAT_DATETIME)
    except ValueError:
        try:
            parsed = datetime.strptime(text, TIME_FORMAT_DATE)
        except ValueError as exc:
            raise ValueError(
                f"无法识别的时间格式: {value}"
                f"（支持 {TIME_FORMAT_DATE} 或 {TIME_FORMAT_DATETIME}）"
            ) from exc
        is_date_only = True
    if is_date_only and is_end:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed.strftime(TIME_FORMAT_DATETIME)