#!/usr/bin/env bash
# 临时脚本：三策略 × 三股票池 分钟频回测对比（2026-07-21 ~ 2026-10-13）。用完即删。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_cmp

START="2026-07-21 09:30:00"
END="2026-10-13 15:00:00"

declare -A POOLS=(
  [holdings]="我实际持仓股票"
  [watch]="我的自选股"
  [tech]="我的科技股票池"
)

for strat in strong swing volume; do
  for key in holdings watch tech; do
    pool="${POOLS[$key]}"
    out="_bt_cmp/${strat}_${key}.json"
    echo "########## ${strat} / ${pool} -> ${out}"
    python -m backtest.main \
      --strategy "${strat}" \
      --pool-name "${pool}" \
      --start "${START}" \
      --end "${END}" \
      --frequency minute \
      --output "${out}" 2>&1 | grep -E "策略:|总交易次数|胜率|总收益率|最大回撤|执行失败|结果已导出"
  done
done
echo "########## ALL DONE"
