"""汇总对比回测结果（三策略 × 三池 × 门槛开关 × 成交间隔），打印 Markdown 表格。用完可删。

三套口径：
  v2   _bt_v2/gates_{strat}_{pool}.json     门槛全开
  v2   _bt_v2/nogate_{strat}_{pool}.json    门槛全关（对照）
  v3   _bt_v3/gates30_{strat}_{pool}.json   门槛全开 + 成交间隔 30 分钟
  v4   _bt_v4/base02_{strat}_{pool}.json    滑点 0.02 + 门槛全开 + 间隔 30 分钟
  v4   _bt_v4/dip02_{strat}_{pool}.json     再叠加盘中买入门槛

注意 v2/v3 的滑点是 0.2 元，v4 是 0.02 元，两组不可直接比收益绝对值。

用法：python _bt_v2_summary.py        （容器内 cd /q_trading_work 后执行）
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STRATS: list[tuple[str, str]] = [
    ("strong", "强趋势(反弹)"),
    ("swing", "波段"),
    ("volume", "成交量"),
]
POOLS: list[tuple[str, str]] = [
    ("holdings", "实际持仓"),
    ("watch", "自选股"),
    ("tech", "科技池"),
]


def load(base: Path, tag: str, strat: str, pool: str) -> dict:
    path = base / f"{tag}_{strat}_{pool}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("summary", {}) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"<!-- 读取失败 {path.name}: {exc} -->")
        return {}


def fmt_row(label_a: str, label_b: str, s: dict) -> str:
    if not s:
        return f"| {label_a} | {label_b} | - | - | - | - | - | - |"
    return (
        f"| {label_a} | {label_b} | {s.get('total_trades', 0)} | "
        f"{s.get('win_rate', 0):.2f}% | {s.get('total_return_pct', 0):+.2f}% | "
        f"{s.get('max_drawdown_pct', 0):.2f}% | {s.get('avg_profit', 0):+.2f}% | "
        f"{s.get('sharpe_ratio', 0):+.2f} |"
    )


HEADER = (
    "| 策略 | 股票池 | 笔数 | 胜率 | 总收益率 | 最大回撤 | 平均单笔 | 夏普 |\n"
    "|---|---|---|---|---|---|---|---|"
)
DIFF_HEADER = (
    "| 策略 | 股票池 | 笔数差 | 胜率差 | 总收益率差 | 回撤差 | 平均单笔差 |\n"
    "|---|---|---|---|---|---|---|"
)


def section(title: str, base: Path, tag: str, note: str = "") -> None:
    print(f"## {title}")
    print()
    if note:
        print(note)
        print()
    print(HEADER)
    for sk, sn in STRATS:
        for pk, pn in POOLS:
            print(fmt_row(sn, pn, load(base, tag, sk, pk)))
    print()


def diff_section(title: str, base_a: Path, tag_a: str, base_b: Path, tag_b: str, note: str = "") -> None:
    print(f"## {title}")
    print()
    if note:
        print(note)
        print()
    print(DIFF_HEADER)
    for sk, sn in STRATS:
        for pk, pn in POOLS:
            a = load(base_a, tag_a, sk, pk)
            b = load(base_b, tag_b, sk, pk)
            if not a or not b:
                print(f"| {sn} | {pn} | - | - | - | - | - |")
                continue
            print(
                f"| {sn} | {pn} | "
                f"{a.get('total_trades', 0) - b.get('total_trades', 0):+d} | "
                f"{a.get('win_rate', 0) - b.get('win_rate', 0):+.2f}pp | "
                f"{a.get('total_return_pct', 0) - b.get('total_return_pct', 0):+.2f}pp | "
                f"{a.get('max_drawdown_pct', 0) - b.get('max_drawdown_pct', 0):+.2f}pp | "
                f"{a.get('avg_profit', 0) - b.get('avg_profit', 0):+.2f}pp |"
            )
    print()


B_V2 = ROOT / "_bt_v2"
B_V3 = ROOT / "_bt_v3"
B_V4 = ROOT / "_bt_v4"

section("一、门槛全开（新版本 v2 gates）", B_V2, "gates")
section("二、门槛全关（对照 v2 nogate）", B_V2, "nogate")
section("三、门槛全开 + 成交间隔 30 分钟（v3 gates30）", B_V3, "gates30")
diff_section("四、门槛开关净效果（全开 − 全关）", B_V2, "gates", B_V2, "nogate")
diff_section("五、成交间隔项净效果（门槛+30分钟 − 仅门槛）", B_V3, "gates30", B_V2, "gates")
section("六、滑点0.02 + 门槛全开 + 间隔30分钟（v4 base02）", B_V4, "base02")
section("七、再叠加盘中买入门槛（v4 dip02）", B_V4, "dip02")
diff_section(
    "八、盘中买入门槛净效果（dip02 − base02）",
    B_V4,
    "dip02",
    B_V4,
    "base02",
    note="两组滑点与其它开关完全相同，只差 `--buy-dip-gate`。",
)
