"""盘前市场状态数据采集：指数日K + 每日涨跌停家数。

数据源：akshare（免费公开接口）。
用途：验证「市场状态对策略表现的影响是否显著」，以及后续盘前市场状态判断功能。

产出（与本脚本同目录）：
- index_daily.csv     多指数日K（上证/深证成指/创业板指/沪深300）
- limit_counts.csv    每个交易日的涨停家数、跌停家数
"""

from __future__ import annotations

import os
import time

import akshare as ak
import pandas as pd

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
START = "2026-06-01"
END = "2026-10-13"

INDEXES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
}


def fetch_indexes() -> pd.DataFrame:
    """拉取多指数日K，一次调用即可拿到全历史。"""
    frames = []
    for symbol, name in INDEXES.items():
        df = ak.stock_zh_index_daily(symbol=symbol)
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= START) & (df["date"] <= END)].copy()
        df["index_code"] = symbol
        df["index_name"] = name
        frames.append(df)
        print(f"  {name}({symbol}) {len(df)} 行")
    out = pd.concat(frames, ignore_index=True)
    path = os.path.join(OUT_DIR, "index_daily.csv")
    out.to_csv(path, index=False)
    print("已保存", path)
    return out


def fetch_limit_counts() -> pd.DataFrame:
    """逐日拉涨停池与跌停池，统计家数。

    东财接口只提供单日数据，需按交易日逐日调用。
    交易日历取自上证指数日K，避免在非交易日发起无意义请求。
    """
    idx = ak.stock_zh_index_daily(symbol="sh000001")
    idx["date"] = pd.to_datetime(idx["date"])
    days = (
        idx[(idx["date"] >= START) & (idx["date"] <= END)]["date"]
        .dt.strftime("%Y%m%d")
        .tolist()
    )
    rows: list[dict[str, object]] = []
    for i, d in enumerate(days, 1):
        up: int | None = None
        down: int | None = None
        try:
            up = len(ak.stock_zt_pool_em(date=d))
        except Exception as exc:
            print(f"  {d} 涨停池失败: {type(exc).__name__} {exc}")
        try:
            down = len(ak.stock_zt_pool_dtgc_em(date=d))
        except Exception as exc:
            print(f"  {d} 跌停池失败: {type(exc).__name__} {exc}")
        rows.append({"date": d, "limit_up": up, "limit_down": down})
        print(f"[{i}/{len(days)}] {d} 涨停 {up} 跌停 {down}")
        time.sleep(0.3)
    out = pd.DataFrame(rows)
    path = os.path.join(OUT_DIR, "limit_counts.csv")
    out.to_csv(path, index=False)
    print("已保存", path)
    return out


if __name__ == "__main__":
    print("== 指数日K ==")
    fetch_indexes()
    print("== 涨跌停家数 ==")
    fetch_limit_counts()
    print("完成")
