"""汇总三策略 × 三池回测对比结果，打印 Markdown 表格。用完可删。"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent / "_bt_cmp"
STRATS = {"strong": "强趋势(反弹)", "swing": "波段", "volume": "成交量"}
POOLS = {"holdings": "实际持仓(7)", "watch": "自选股(19)", "tech": "科技池(22)"}

rows: list[tuple[str, str, dict]] = []
for key_s, name_s in STRATS.items():
    for key_p, name_p in POOLS.items():
        path = BASE / f"{key_s}_{key_p}.json"
        if not path.exists():
            rows.append((name_s, name_p, {}))
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append((name_s, name_p, data.get("summary", {})))

print(f"| {'策略':<12} | {'股票池':<12} | 笔数 | 胜率 | 总收益 | 最大回撤 | 平均单笔 |")
print("|---|---|---|---|---|---|---|")
for name_s, name_p, s in rows:
    if not s:
        print(f"| {name_s:<12} | {name_p:<12} | - | - | - | - | - |")
        continue
    print(
        f"| {name_s:<12} | {name_p:<12} | {s.get('total_trades', 0)} | "
        f"{s.get('win_rate', 0):.2f}% | {s.get('total_return_pct', 0):.2f}% | "
        f"{s.get('max_drawdown_pct', 0):.2f}% | {s.get('avg_profit', 0):.2f}% |"
    )
