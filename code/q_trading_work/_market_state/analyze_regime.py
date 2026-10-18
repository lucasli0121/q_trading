"""市场状态对策略表现的影响验证。

思路：用指数日K 定义每个交易日的市场状态（当日涨跌幅、近 5 日累计涨跌幅、
是否站上 MA20），再把回测成交按买入日聚合，看不同市场状态下的胜率与平均盈亏
是否存在显著差异。

若差异显著，说明「加一个市场状态分值」有价值；若不显著，则没必要加。

数据来源：
- _market_state/index_daily.csv（akshare，完整）
- _market_state/limit_counts.csv（akshare，仅最近 30 个交易日有效）
- 仓库根 bt_vol_*.json（历史回测快照）
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BACKTESTS = [
    "bt_vol_holdings.json",
    "bt_vol_holdings_gap30.json",
    "bt_vol_holdings_gap30_split.json",
]

STATES = [
    ("强势", lambda r: r >= 2.0),
    ("偏强", lambda r: (r >= 0.5) & (r < 2.0)),
    ("偏弱", lambda r: (r > -2.0) & (r < 0.5)),
    ("弱势", lambda r: r <= -2.0),
]


def build_index_state() -> pd.DataFrame:
    """按指数日K 计算每日市场状态。"""
    idx = pd.read_csv(os.path.join(HERE, "index_daily.csv"))
    idx["date"] = pd.to_datetime(idx["date"]).dt.normalize()

    frames = []
    for symbol in ("sh000001", "sz399006"):
        d = idx[idx["index_code"] == symbol].sort_values("date").copy()
        d["ret1"] = d["close"].pct_change() * 100
        d["ret5"] = d["close"].pct_change(5) * 100
        d["ma20"] = d["close"].rolling(20).mean()
        d["above_ma20"] = d["close"] > d["ma20"]
        frames.append(
            d[["date", "ret1", "ret5", "above_ma20"]].rename(
                columns={
                    "ret1": f"{symbol}_ret1",
                    "ret5": f"{symbol}_ret5",
                    "above_ma20": f"{symbol}_above_ma20",
                }
            )
        )
    state = frames[0].merge(frames[1], on="date", how="outer").sort_values("date")
    return state


def load_trades(path: str) -> pd.DataFrame:
    """读回测 JSON，返回按买入日聚合的逐日表现。"""
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        data = json.load(fh)
    trades = pd.DataFrame(data["trades"])
    if trades.empty:
        return pd.DataFrame()
    trades["date"] = pd.to_datetime(trades["buy_date"]).dt.normalize()
    daily = (
        trades.groupby("date")
        .agg(
            笔数=("profit_pct", "size"),
            平均盈亏=("profit_pct", "mean"),
            中位盈亏=("profit_pct", "median"),
            胜率=("profit_pct", lambda s: (s > 0).mean() * 100),
            最大亏损=("profit_pct", "min"),
        )
        .reset_index()
    )
    return daily


def main() -> None:
    state = build_index_state()
    state.to_csv(os.path.join(HERE, "index_state.csv"), index=False)
    print(f"指数状态: {len(state)} 个交易日，"
          f"{state['date'].min().date()} ~ {state['date'].max().date()}")

    limits = pd.read_csv(os.path.join(HERE, "limit_counts.csv"))
    limits["date"] = pd.to_datetime(limits["date"], format="%Y%m%d")
    limits = limits[(limits["limit_up"] > 0) & limits["limit_down"].notna()].copy()
    limits["涨跌停比"] = limits["limit_up"] / (limits["limit_up"] + limits["limit_down"])
    limits = limits[["date", "limit_up", "limit_down", "涨跌停比"]]
    print(f"涨跌停家数: {len(limits)} 个交易日有效，"
          f"{limits['date'].min().date()} ~ {limits['date'].max().date()}")

    for path in BACKTESTS:
        daily = load_trades(path)
        if daily.empty:
            continue
        merged = daily.merge(state, on="date", how="left").merge(limits, on="date", how="left")
        merged = merged.sort_values("date")
        print("\n" + "=" * 100)
        print(f"### {path}   成交分布 {len(merged)} 个交易日，"
              f"{int(merged['笔数'].sum())} 笔，逐日平均 {merged['平均盈亏'].mean():.3f}%/笔")
        print("=" * 100)
        show = merged.copy()
        show["date"] = show["date"].dt.strftime("%m-%d")
        cols = ["date", "笔数", "胜率", "平均盈亏", "中位盈亏",
                "sh000001_ret1", "sz399006_ret1", "sz399006_ret5",
                "limit_up", "limit_down", "涨跌停比"]
        cols = [c for c in cols if c in show.columns]
        print(show[cols].to_string(index=False, float_format=lambda v: f"{v:7.2f}"))

        # 按市场状态分组（用创业板指近 5 日涨跌幅，策略标的以创业板/科技股为主）
        col = "sz399006_ret5"
        sub = merged.dropna(subset=[col]).copy()
        if len(sub) >= 4:
            sub["状态"] = "未知"
            for name, cond in STATES:
                sub.loc[cond(sub[col]), "状态"] = name
            grp = (
                sub.groupby("状态")
                .agg(交易日=("date", "size"), 笔数=("笔数", "sum"),
                     胜率=("胜率", "mean"), 平均盈亏=("平均盈亏", "mean"))
                .sort_values("平均盈亏")
            )
            print(f"\n-- 按创业板指近 5 日涨跌幅分组（{len(sub)} 个交易日）--")
            print(grp.to_string(float_format=lambda v: f"{v:8.2f}"))

            if len(sub) >= 6:
                r = np.corrcoef(sub[col], sub["平均盈亏"])[0, 1]
                print(f"\n相关系数 ({col} vs 当日平均盈亏): {r:+.3f}")

            # 波动幅度维度：验证「极端状态（无论涨跌）表现更差」的假设。
            # 方向维度若无区分力，幅度维度仍可能有，两者要分开看。
            sub["波动"] = sub[col].abs()
            med = sub["波动"].median()
            sub["波动态"] = np.where(sub["波动"] >= med, "高波动", "低波动")
            g2 = sub.groupby("波动态").agg(
                交易日=("date", "size"), 笔数=("笔数", "sum"),
                胜率=("胜率", "mean"), 平均盈亏=("平均盈亏", "mean"),
            )
            print(f"\n-- 按 |近5日涨跌幅| 分组（中位阈值 {med:.2f}%）--")
            print(g2.to_string(float_format=lambda v: f"{v:8.2f}"))

        # 涨跌停家数部分（仅有数据的日期）
        sub2 = merged.dropna(subset=["涨跌停比"])
        if len(sub2) >= 4:
            print(f"\n-- 按涨跌停家数比分组（{len(sub2)} 个交易日）--")
            print(sub2[["date", "笔数", "胜率", "平均盈亏", "limit_up",
                        "limit_down", "涨跌停比"]]
                  .assign(date=lambda d: d["date"].dt.strftime("%m-%d"))
                  .to_string(index=False, float_format=lambda v: f"{v:7.2f}"))


if __name__ == "__main__":
    main()
