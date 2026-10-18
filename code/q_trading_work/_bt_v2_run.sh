#!/usr/bin/env bash
# 临时脚本：三策略 × 三股票池 × 门槛开关 分钟频回测（2026-07-21 ~ 2026-10-13）。用完即删。
#
# 两组：
#   gates   —— 门槛全开：盘后阀门复算 + 市场偏移 + 极端利空禁买 + 日线趋势门槛
#   nogate  —— 门槛全关：关日线趋势门槛，阀门/市场/禁买都不传（历史口径）
# 两组之差即「四项门槛机制」的净效果。
#
# 注意：容器 /bin/sh 是 dash，本脚本按 POSIX 写，用 bash 执行亦可。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v2

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
    tag="$1"
    strat="$2"
    key="$3"
    pool=$(pool_name "$key")
    out="_bt_v2/${tag}_${strat}_${key}.json"
    log="_bt_v2/${tag}_${strat}_${key}.log"
    echo "########## [${tag}] ${strat} / ${pool} -> ${out}  @ $(date '+%H:%M:%S')"
    if [ "$tag" = "gates" ]; then
        python -m backtest.main \
            --strategy "${strat}" \
            --pool-name "${pool}" \
            --start "${START}" \
            --end "${END}" \
            --frequency minute \
            --enable-daily-gates \
            --market-breadth-csv "${BREADTH}" \
            --verbose \
            --output "${out}" > "${log}" 2>&1
    else
        python -m backtest.main \
            --strategy "${strat}" \
            --pool-name "${pool}" \
            --start "${START}" \
            --end "${END}" \
            --frequency minute \
            --no-trend-gate \
            --verbose \
            --output "${out}" > "${log}" 2>&1
    fi
    echo "  exit=$? -> ${out}  @ $(date '+%H:%M:%S')"
}

for tag in gates nogate; do
    for strat in strong swing volume; do
        for key in holdings watch tech; do
            run_one "${tag}" "${strat}" "${key}"
        done
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
