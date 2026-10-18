#!/usr/bin/env bash
# 临时脚本：对照组 —— 科技池(22) × 三策略，开启资金分片 + 30 分钟成交间隔。用完即删。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_cmp

START="2026-07-21 09:30:00"
END="2026-10-13 15:00:00"
POOL="我的科技股票池"

for strat in strong swing volume; do
  out="_bt_cmp/${strat}_tech_split30.json"
  echo "########## ${strat} / ${POOL} / split+gap30 -> ${out}"
  python -m backtest.main \
    --strategy "${strat}" \
    --pool-name "${POOL}" \
    --start "${START}" \
    --end "${END}" \
    --frequency minute \
    --split-capital-by-code \
    --min-trade-interval-minutes 30 \
    --output "${out}" 2>&1 | grep -E "策略:|总交易次数|胜率|总收益率|最大回撤|执行失败|结果已导出"
done
echo "########## ALL DONE"
