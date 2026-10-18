"""v6 对照汇总：日内熔断的净效果 + 重构等价性检查。

三套口径（同一区间 2026-07-21 ~ 2026-10-13，分钟频，门槛全开 + 间隔 30 + 滑点 0.02）：

  v5new  _bt_v5/newstop_{s}_{p}.json   旧代码（有止损，无熔断）
  v6off  _bt_v6/off_{s}_{p}.json       新代码 + --no-intraday-breaker
  v6on   _bt_v6/on_{s}_{p}.json        新代码（熔断默认开启）

对照关系：
  v6off vs v5new  = 重构等价性（改名 + 路由调整后再关掉熔断，逐笔应完全一致）
  v6on  vs v6off  = 熔断净效果（唯一真·单变量）

用法（容器内）：python _bt_v6_summary.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path("/q_trading_work")
B_V5 = ROOT / "_bt_v5"
B_V6 = ROOT / "_bt_v6"

STRATS: list[tuple[str, str]] = [("strong", "强趋势"), ("swing", "波段"), ("volume", "成交量")]
POOLS: list[tuple[str, str]] = [("holdings", "实际持仓"), ("watch", "自选股"), ("tech", "科技池")]

CASES: list[tuple[str, Path, str]] = [
    ("旧码无熔断", B_V5, "newstop_{s}_{p}.json"),
    ("新码熔断关", B_V6, "off_{s}_{p}.json"),
    ("新码熔断开", B_V6, "on_{s}_{p}.json"),
]

STOP_SCORE: float = -6.0


def load(directory: Path, pattern: str, strat: str, pool: str) -> dict | None:
    path = directory / pattern.format(s=strat, p=pool)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def is_stop(trade: dict) -> bool:
    """硬止损成交：卖出分恰为 SELL_BUY_PRICE_BREAK_SCORE（−6.0）。"""
    return abs(float(trade.get("sell_score", 0.0)) - STOP_SCORE) < 1e-9


def fmt(summary: dict | None) -> str:
    if not summary:
        return "        -"
    return "%4d笔 %+7.2f%%" % (summary["total_trades"], summary["total_return_pct"])


def section_matrix() -> None:
    print("=" * 108)
    print("一、三套口径明细（笔数 / 总收益率）")
    print("=" * 108)
    header = "%-8s %-8s" % ("策略", "池")
    for name, _, _ in CASES:
        header += " | %-18s" % name
    print(header)
    print("-" * 108)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            line = "%-8s %-8s" % (s_name, p_name)
            for _, directory, pattern in CASES:
                data = load(directory, pattern, strat, pool)
                line += " | %-18s" % fmt(data.get("summary") if data else None)
            print(line)


def section_refactor_equivalence() -> None:
    """v6off 与 v5new 语义相同，逐笔应完全一致。"""
    print()
    print("=" * 108)
    print("二、重构等价性检查（新码关熔断 vs 旧码，逐笔比对）")
    print("=" * 108)
    print("%-8s %-8s | %-16s | %s" % ("策略", "池", "结论", "差异"))
    print("-" * 108)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            old = load(B_V5, "newstop_{s}_{p}.json", strat, pool)
            new = load(B_V6, "off_{s}_{p}.json", strat, pool)
            if not old or not new:
                print("%-8s %-8s | %-16s | 缺数据" % (s_name, p_name, "-"))
                continue
            same: bool = old.get("trades") == new.get("trades") and (
                old["summary"]["total_return_pct"] == new["summary"]["total_return_pct"]
            )
            if same:
                print("%-8s %-8s | %-16s | -" % (s_name, p_name, "逐笔一致"))
                continue
            so, sn = old["summary"], new["summary"]
            print(
                "%-8s %-8s | %-16s | 笔数%+d 收益%+.2fpp"
                % (
                    s_name,
                    p_name,
                    "有差异",
                    sn["total_trades"] - so["total_trades"],
                    sn["total_return_pct"] - so["total_return_pct"],
                )
            )


def section_breaker_effect() -> None:
    print()
    print("=" * 108)
    print("三、熔断净效果（新码熔断开 − 新码熔断关）")
    print("=" * 108)
    print(
        "%-8s %-8s | %-22s | %6s %6s | %s"
        % ("策略", "池", "笔数差 / 收益差", "止损笔", "占比", "止损单均收益")
    )
    print("-" * 108)
    deltas: list[float] = []
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            off = load(B_V6, "off_{s}_{p}.json", strat, pool)
            on = load(B_V6, "on_{s}_{p}.json", strat, pool)
            if not off or not on:
                continue
            so, sn = off["summary"], on["summary"]
            trades = on.get("trades") or []
            hits = [t for t in trades if is_stop(t)]
            avg = sum(t["profit_pct"] for t in hits) / len(hits) if hits else 0.0
            share = len(hits) / len(trades) * 100 if trades else 0.0
            delta: float = sn["total_return_pct"] - so["total_return_pct"]
            deltas.append(delta)
            print(
                "%-8s %-8s | %+4d笔 %+7.2fpp       | %6d %5.1f%% | %+.2f%%"
                % (
                    s_name,
                    p_name,
                    sn["total_trades"] - so["total_trades"],
                    delta,
                    len(hits),
                    share,
                    avg,
                )
            )
    if deltas:
        print()
        print(
            "九组均值 %+.2fpp，区间 %+.2f ~ %+.2fpp"
            % (sum(deltas) / len(deltas), min(deltas), max(deltas))
        )


def section_churn() -> None:
    """止损后 2 小时内买回同一只票的比例：熔断要打的就是这个循环。"""
    print()
    print("=" * 108)
    print("四、止损空转指标（止损后 2h 内再入场同一只票，及其是否被二次止损）")
    print("=" * 108)
    print(
        "%-8s %-8s | %-32s | %s"
        % ("策略", "池", "熔断关（再入场 / 二次止损）", "熔断开（再入场 / 二次止损）")
    )
    print("-" * 108)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            cells: list[str] = []
            for directory, pattern in ((B_V6, "off_{s}_{p}.json"), (B_V6, "on_{s}_{p}.json")):
                data = load(directory, pattern, strat, pool)
                trades = (data or {}).get("trades") or []
                re_entry = 0
                re_stop = 0
                for i, trade in enumerate(trades):
                    if not is_stop(trade):
                        continue
                    sell_at = _ts(trade.get("sell_date", ""))
                    if not sell_at:
                        continue
                    for later in trades[i + 1:]:
                        if later["code"] != trade["code"]:
                            continue
                        buy_at = _ts(later.get("buy_date", ""))
                        if buy_at and 0 <= (buy_at - sell_at).total_seconds() <= 7200:
                            re_entry += 1
                            if is_stop(later):
                                re_stop += 1
                        break
                rate = (re_stop / re_entry * 100) if re_entry else 0.0
                cells.append("%3d 笔 / %3d 笔(%.0f%%)" % (re_entry, re_stop, rate))
            print("%-8s %-8s | %-32s | %s" % (s_name, p_name, cells[0], cells[1]))


def section_triggers() -> None:
    """从回测日志统计熔断触发次数（策略在封锁生效那一刻各记一条 INFO）。"""
    print()
    print("=" * 108)
    print("五、熔断触发次数（日志里「日内熔断」行数）")
    print("=" * 108)
    print("%-8s %-8s | %10s %10s" % ("策略", "池", "停买次数", "停卖次数"))
    print("-" * 108)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            path = Path("/tmp/btv6_on_%s_%s.log" % (strat, pool))
            if not path.exists():
                print("%-8s %-8s | %10s %10s" % (s_name, p_name, "-", "-"))
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            print(
                "%-8s %-8s | %10d %10d"
                % (
                    s_name,
                    p_name,
                    text.count("亏损卖出 ⇒ 当日不再买入该股"),
                    text.count("卖飞 ⇒ 当日不再卖出该股"),
                )
            )


def _ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def main() -> int:
    section_matrix()
    section_refactor_equivalence()
    section_breaker_effect()
    section_churn()
    section_triggers()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
