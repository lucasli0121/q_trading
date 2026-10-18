#!/usr/bin/env bash
# 临时脚本：滑点 0.02 + 门槛全开 + 成交间隔 30 分钟 ± 盘中买入门槛（2026-07-21 ~ 2026-10-13）。用完即删。
#
# 两组（3 策略 × 3 池 = 9 组/组，共 18 组）：
#   base02 —— 门槛全开 + 成交间隔 30 分钟（滑点沿用 CLI 默认 0.02）
#   dip02  —— 在 base02 基础上加 --buy-dip-gate（盘中买入门槛）
# 两组之差即「盘中买入门槛」的净效果；两组都用 0.02 滑点，与更早 0.2 滑点的 v2/v3 不可直接比。
#
# 每组开跑前清一次 Redis 业务缓存（上一轮残留的 bar:/sr_points: 会被下一轮读到）。
#
# 注意：容器 /bin/sh 是 dash，本脚本按 POSIX 写，用 bash 执行亦可。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v4

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
    out="_bt_v4/${tag}_${strat}_${key}.json"
    log="/tmp/btv4_${tag}_${strat}_${key}.log"
    echo "########## [${tag}] ${strat} / ${pool} -> ${out}  @ $(date '+%H:%M:%S')"
    python _bt_clear_redis.py
    if [ "$tag" = "dip02" ]; then
        extra="--buy-dip-gate"
    else
        extra=""
    fi
    python -m backtest.main \
        --strategy "${strat}" \
        --pool-name "${pool}" \
        --start "${START}" \
        --end "${END}" \
        --frequency minute \
        --enable-daily-gates \
        --market-breadth-csv "${BREADTH}" \
        --min-trade-interval-minutes 30 \
        ${extra} \
        --verbose \
        --output "${out}" > "${log}" 2>&1
    echo "  exit=$? -> ${out}  @ $(date '+%H:%M:%S')"
}

for tag in base02 dip02; do
    for strat in strong swing volume; do
        for key in holdings watch tech; do
            run_one "${tag}" "${strat}" "${key}"
        done
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
