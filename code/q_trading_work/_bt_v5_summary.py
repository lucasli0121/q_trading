"""v5 对比汇总：量比 NaN 修复 + 买入信号价止损的净效果。

三套口径（同一区间 2026-07-21 ~ 2026-10-13，分钟频，门槛全开 + 成交间隔 30 分钟 + 滑点 0.02）：

  base02  _bt_v4/base02_{strat}_{pool}.json   旧代码（自持仓最高点回撤止损，NaN 未拦）
  nostop  _bt_v5/nostop_{strat}_{pool}.json   只修 NaN（--no-buy-signal-stop）
  newstop _bt_v5/newstop_{strat}_{pool}.json  NaN + 买入信号价止损

  base02 → nostop = NaN 修复的净效果
  nostop → newstop = 买入信号价止损的净效果
  base02 → newstop = 两项合计

用法（容器内）：python _bt_v5_summary.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/q_trading_work")
B_V4 = ROOT / "_bt_v4"
B_V5 = ROOT / "_bt_v5"

STRATS: list[tuple[str, str]] = [("strong", "强趋势"), ("swing", "波段"), ("volume", "成交量")]
POOLS: list[tuple[str, str]] = [("holdings", "实际持仓"), ("watch", "自选股"), ("tech", "科技池")]

# (显示名, 目录, 文件名模板)
CASES: list[tuple[str, Path, str]] = [
    ("旧代码", B_V4, "base02_{s}_{p}.json"),
    ("只修NaN", B_V5, "nostop_{s}_{p}.json"),
    ("NaN+止损", B_V5, "newstop_{s}_{p}.json"),
]


def load(directory: Path, pattern: str, strat: str, pool: str) -> dict | None:
    path = directory / pattern.format(s=strat, p=pool)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data


def summary_of(data: dict | None) -> dict | None:
    return data.get("summary") if data else None


def fmt(s: dict | None) -> str:
    if not s:
        return "        -"
    return "%4d笔 %+7.2f%%" % (s["total_trades"], s["total_return_pct"])


def diff_line(a: dict | None, b: dict | None) -> str:
    if not a or not b:
        return "      -"
    return "%+4d笔 %+6.2fpp  回撤%+5.2fpp" % (
        b["total_trades"] - a["total_trades"],
        b["total_return_pct"] - a["total_return_pct"],
        b["max_drawdown_pct"] - a["max_drawdown_pct"],
    )


def section_matrix() -> None:
    print("=" * 116)
    print("一、三套口径明细（笔数 / 总收益率）")
    print("=" * 116)
    header = "%-8s %-8s" % ("策略", "池")
    for name, _, _ in CASES:
        header += " | %-18s" % name
    print(header)
    print("-" * 116)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            line = "%-8s %-8s" % (s_name, p_name)
            for _, directory, pattern in CASES:
                line += " | %-18s" % fmt(summary_of(load(directory, pattern, strat, pool)))
            print(line)


def section_diff(title: str, left: tuple, right: tuple) -> None:
    lname, ldir, lpat = left
    rname, rdir, rpat = right
    print()
    print("=" * 116)
    print(f"{title}（{rname} − {lname}）")
    print("=" * 116)
    print("%-8s %-8s | %-24s | %-9s %-9s" % ("策略", "池", "笔数差 / 收益差 / 回撤差", "", ""))
    print("-" * 116)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            a = summary_of(load(ldir, lpat, strat, pool))
            b = summary_of(load(rdir, rpat, strat, pool))
            print("%-8s %-8s | %-24s | %s" % (s_name, p_name, "", diff_line(a, b)))


def section_stop_share() -> None:
    """止损成交占比：硬止损的固定卖出分是 -6.0（SELL_BUY_PRICE_BREAK_SCORE）。"""
    print()
    print("=" * 116)
    print("二、硬止损了结的成交（卖出分恰为 -6.0 = SELL_BUY_PRICE_BREAK_SCORE）")
    print("=" * 116)
    print("%-8s %-8s | %6s %6s %6s | %s" % ("策略", "池", "总笔数", "止损笔", "占比", "止损单平均收益"))
    print("-" * 116)
    for strat, s_name in STRATS:
        for pool, p_name in POOLS:
            data = load(B_V5, "newstop_{s}_{p}.json", strat, pool)
            if not data:
                continue
            trades = data.get("trades") or []
            hits = [t for t in trades if abs(float(t.get("sell_score", 0.0)) + 6.0) < 1e-9]
            share = (len(hits) / len(trades) * 100) if trades else 0.0
            avg = (sum(t["profit_pct"] for t in hits) / len(hits)) if hits else 0.0
            print(
                "%-8s %-8s | %6d %6d %5.1f%% | %+.2f%%"
                % (s_name, p_name, len(trades), len(hits), share, avg)
            )


def main() -> int:
    section_matrix()
    section_stop_share()
    section_diff("NaN 修复净效果", CASES[0], CASES[1])
    section_diff("买入信号价止损净效果", CASES[1], CASES[2])
    section_diff("两项合计", CASES[0], CASES[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
