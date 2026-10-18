#!/usr/bin/env bash
# 临时脚本：门槛全开 + 成交间隔 30 分钟（2026-07-21 ~ 2026-10-13 分钟频）。用完即删。
#
# 与 _bt_v2_run.sh 的 gates 组唯一差别：多传 --min-trade-interval-minutes 30。
# 用途：与「门槛全开但无间隔限制」直接对照，单独量化成交间隔这一项的净效果。
#
# 注意：容器 /bin/sh 是 dash，本脚本按 POSIX 写，用 bash 执行亦可。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v3

START="2026-07-21 09:30:00"
END="2026-10-13 15:00:00"
BREADTH="/q_trading_work/_market_state/limit_counts.csv"

pool_name() {
    case "$1" in
        holdings) printf '%s' "我实际持仓股票" ;;
        watch) printf '%s' "我的自选股" ;;
        tech) printf '%s' "我的科技股票池" ;;
    esac
}

run_one() {
    strat="$1"
    key="$2"
    pool=$(pool_name "$key")
    out="_bt_v3/gates30_${strat}_${key}.json"
    log="_bt_v3/gates30_${strat}_${key}.log"
    echo "########## [gates30] ${strat} / ${pool} -> ${out}  @ $(date '+%H:%M:%S')"
    python -m backtest.main \
        --strategy "${strat}" \
        --pool-name "${pool}" \
        --start "${START}" \
        --end "${END}" \
        --frequency minute \
        --enable-daily-gates \
        --market-breadth-csv "${BREADTH}" \
        --min-trade-interval-minutes 30 \
        --verbose \
        --output "${out}" > "${log}" 2>&1
    echo "  exit=$? -> ${out}  @ $(date '+%H:%M:%S')"
}

for strat in strong swing volume; do
    for key in holdings watch tech; do
        run_one "${strat}" "${key}"
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
