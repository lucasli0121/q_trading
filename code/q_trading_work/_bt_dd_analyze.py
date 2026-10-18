# -*- coding: utf-8 -*-
"""复现 backtest_engine._drawdown_at 的口径，标注每个交易日的账户回撤态，
统计「账户回撤触发状态下买入的成交」的笔数与持仓时长。

口径来源（read 自 backtest/backtest_engine.py）：
  _compute_equity_curve(split=False)：初始资金单池，position_size_pct 划出资金，
  卖出按 profit_pct 结算；持仓按成本计价（浮亏不计入权益）。
  _drawdown_at(trades, day)：只用 sell_date <= day 的已结算交易重算曲线，
  dd = (curve[-1] - max(curve)) / max(curve) * 100。
  注入发生在每日盘前，故复现时用 sell_date < day（严格小于）。
"""
import json
import collections
from datetime import datetime

INITIAL = 100000.0
POS_PCT = 1.0
THRESHOLD = -10.0


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def equity_curve(trades):
    """split_capital_by_code=False 时的权益曲线（与引擎逐行等价）。"""
    ordered = sorted(trades, key=lambda t: (t["buy_date"], t["sell_date"]))
    free = INITIAL
    open_inv = {}
    events = []
    for i, t in enumerate(ordered):
        events.append((t["buy_date"], 0, i))
        events.append((t["sell_date"], 1, i))
    events.sort(key=lambda e: (e[0], e[1]))
    eq = [free]
    for _date, et, i in events:
        t = ordered[i]
        if et == 1:
            entry = open_inv.pop(i, None)
            if entry is not None:
                _code, inv = entry
                free += inv * (1 + float(t["profit_pct"]) / 100)
        else:
            if i in open_inv:
                continue
            if free <= 1e-9:
                continue
            inv = min(free * POS_PCT, free)
            free -= inv
            open_inv[i] = (t["code"], inv)
        eq.append(free + sum(a for _c, a in open_inv.values()))
    return eq


def drawdown_at(trades, day):
    key = str(day)[:10]
    closed = [t for t in trades if str(t["sell_date"])[:10] < key]
    if not closed:
        return None
    curve = equity_curve(closed)
    peak = max(curve)
    if peak <= 0:
        return None
    return (curve[-1] - peak) / peak * 100


def hold_minutes(t):
    try:
        a = datetime.strptime(t["buy_date"], "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(t["sell_date"], "%Y-%m-%d %H:%M:%S")
        return (b - a).total_seconds() / 60.0
    except Exception:
        return None


KEYS = [
    "strong_holdings", "strong_watch", "strong_tech",
    "swing_holdings", "swing_watch", "swing_tech",
    "volume_holdings", "volume_watch", "volume_tech",
]


def analyze(prefix, label):
    print("=" * 104)
    print("[%s]  阈值 %.1f%%（盘前注入，用 sell_date < day 的已结算交易重算）" % (label, THRESHOLD))
    print("=" * 104)
    print("%-16s %6s %7s %9s %11s %10s %10s" % (
        "组", "总笔", "交易日", "触发日数", "触发日买入", "触发日往返", "非触发日往返"))
    print("-" * 104)
    for k in KEYS:
        d = load("%s/%s_%s.json" % (prefix, "on" if prefix == "_bt_v6" else "nodraw", k))
        if not d:
            continue
        tr = d["trades"]
        days = sorted({str(t["buy_date"])[:10] for t in tr})
        dd = {day: drawdown_at(tr, day) for day in days}
        trig = {day for day, v in dd.items() if v is not None and v <= THRESHOLD}
        hit = [t for t in tr if str(t["buy_date"])[:10] in trig]
        hit_rt = [t for t in hit if str(t["buy_date"])[:10] == str(t["sell_date"])[:10]]
        rest = [t for t in tr if str(t["buy_date"])[:10] not in trig]
        rest_rt = [t for t in rest if str(t["buy_date"])[:10] == str(t["sell_date"])[:10]]
        print("%-16s %6d %7d %9d %11s %10s %10s" % (
            k, len(tr), len(days), len(trig),
            "%d/%d" % (len(hit), len(tr)),
            "%d (%.0f%%)" % (len(hit_rt), 100.0 * len(hit_rt) / len(hit) if hit else 0),
            "%d (%.0f%%)" % (len(rest_rt), 100.0 * len(rest_rt) / len(rest) if rest else 0),
        ))
    print()


def detail(prefix, tag, k):
    d = load("%s/%s_%s.json" % (prefix, tag, k))
    if not d:
        print("(缺 %s/%s_%s.json)" % (prefix, tag, k))
        return
    tr = d["trades"]
    days = sorted({str(t["buy_date"])[:10] for t in tr})
    dd = {day: drawdown_at(tr, day) for day in days}
    print("--- %s/%s_%s 逐日回撤 ---" % (prefix, tag, k))
    for day in days:
        v = dd[day]
        mark = "  <== 触发" if (v is not None and v <= THRESHOLD) else ""
        print("  %s  dd=%s%s" % (day, "None" if v is None else "%+7.2f%%" % v, mark))


if __name__ == "__main__":
    analyze("_bt_v6", "账户回撤 开（v6 on，10-14 盘中）")
    analyze("_bt_v7", "账户回撤 关（v7 nodraw，10-14 盘后）")
