"""账户级回撤（`risk.max_drawdown_pct`）的对照汇总。

对照关系：

    _bt_v7/nodraw_*  vs  _bt_v6/on_*   ← 账户级回撤的净效果，唯一真·单变量对照

两组代码完全相同、只差 `--no-account-drawdown`。分钟频只有**策略侧**一条路径
（逐 bar 在卖出分上计入 `risk.drawdown_score`）；引擎侧那条「达阈值当日收盘卖出
全部在持仓位」（`_apply_drawdown_exit`）挂在**日频**上，因此 `_bt_v6` 的
`drawdown_exit_trades` 九组全为 0，与本轮无关。

`strong` 策略未设 `RISK_MAX_DRAWDOWN_PCT`（取基类默认 0.0 = 关闭），
所以它的三组应逐笔一致 —— 既是对照组的「零效应基线」，也用来验证开关没误伤。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT: Path = Path(__file__).resolve().parent
KEYS: list[str] = [
    f"{strat}_{pool}"
    for strat in ("strong", "swing", "volume")
    for pool in ("holdings", "watch", "tech")
]
NAMES: dict[str, str] = {
    "strong": "强趋势",
    "swing": "波段",
    "volume": "成交量",
}
POOLS: dict[str, str] = {
    "holdings": "实际持仓",
    "watch": "自选股",
    "tech": "科技池",
}
# 硬止损的等效卖出分（固定值），用于把止损单单独拎出来看
STOP_SCORE: float = -6.0


def load(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def is_stop(trade: dict[str, Any]) -> bool:
    """硬止损成交：卖出分恰为 SELL_BUY_PRICE_BREAK_SCORE。

    注意它**不唯一**：账户回撤扣分（-6.0）叠加技术分恰为 0 时也是 -6.0。
    因此本函数只能给出「止损 + 部分回撤卖出」的上界。
    """
    return abs(float(trade.get("sell_score", 0.0)) - STOP_SCORE) < 1e-9


def main() -> None:
    print("=" * 108)
    print("账户级回撤对照：_bt_v6/on_*（开启） vs _bt_v7/nodraw_*（关闭）")
    print("=" * 108)
    header = (
        f"{'组':<16}{'开启 笔数':>10}{'关闭 笔数':>10}{'笔数差':>8}"
        f"{'开启 收益':>12}{'关闭 收益':>12}{'收益差':>10}{'开启 回撤':>11}{'关闭 回撤':>11}"
    )
    print(header)
    print("-" * 108)

    diffs: list[float] = []
    trade_diffs: list[int] = []
    identical: list[str] = []
    for key in KEYS:
        on: dict[str, Any] | None = load(ROOT / "_bt_v6" / f"on_{key}.json")
        off: dict[str, Any] | None = load(ROOT / "_bt_v7" / f"nodraw_{key}.json")
        if on is None or off is None:
            print(f"{key:<16}{'缺产物':>10}")
            continue
        so, sf = on["summary"], off["summary"]
        strat, pool = key.split("_", 1)
        label = f"{NAMES.get(strat, strat)}/{POOLS.get(pool, pool)}"
        d_ret = sf["total_return_pct"] - so["total_return_pct"]
        d_cnt = sf["total_trades"] - so["total_trades"]
        if on["trades"] == off["trades"]:
            identical.append(label)
        if strat != "strong":
            diffs.append(d_ret)
            trade_diffs.append(d_cnt)
        print(
            f"{label:<16}{so['total_trades']:>10}{sf['total_trades']:>10}{d_cnt:>+8}"
            f"{so['total_return_pct']:>+11.2f}%{sf['total_return_pct']:>+11.2f}%"
            f"{d_ret:>+9.2f}pp{so['max_drawdown_pct']:>10.2f}%"
            f"{sf['max_drawdown_pct']:>10.2f}%"
        )

    print("-" * 108)
    if diffs:
        print(
            f"剔除 strong（本就关闭）后的六组：收益差均值 {sum(diffs) / len(diffs):+.2f}pp"
            f"（{min(diffs):+.2f} ~ {max(diffs):+.2f}），笔数差均值 "
            f"{sum(trade_diffs) / len(trade_diffs):+.1f}"
            f"（{min(trade_diffs):+d} ~ {max(trade_diffs):+d}）"
        )
    print(f"逐笔完全一致的组：{identical if identical else '无'}")

    print()
    print("=" * 108)
    print("止损单占比（卖出分 == -6.0 的成交；含「账户回撤扣分 + 技术分恰为 0」，故为上界）")
    print("=" * 108)
    print(f"{'组':<16}{'开启 笔数':>10}{'其中 -6.0':>11}{'占比':>9}      {'关闭 笔数':>10}{'其中 -6.0':>11}{'占比':>9}")
    print("-" * 108)
    for key in KEYS:
        on: dict[str, Any] | None = load(ROOT / "_bt_v6" / f"on_{key}.json")
        off: dict[str, Any] | None = load(ROOT / "_bt_v7" / f"nodraw_{key}.json")
        if on is None or off is None:
            continue
        strat, pool = key.split("_", 1)
        label = f"{NAMES.get(strat, strat)}/{POOLS.get(pool, pool)}"
        row = f"{label:<16}"
        for data in (on, off):
            trades = data["trades"]
            hits = [t for t in trades if is_stop(t)]
            pct = 100 * len(hits) / len(trades) if trades else 0.0
            row += f"{len(trades):>10}{len(hits):>11}{pct:>8.1f}%"
        print(row)


if __name__ == "__main__":
    main()
