#!/usr/bin/env bash
# 临时脚本：日内熔断的对照回测。用完即删。
# 区间 2026-07-21 09:30:00 ~ 2026-10-13 15:00:00，分钟频。
#
# 两组（3 策略 × 3 池 = 9 组/组，共 18 组）：
#   off —— 改动后的代码 + --no-intraday-breaker：熔断关闭
#   on  —— 改动后的代码：熔断开启（默认）
#
# 口径与 _bt_v5 的 newstop 完全一致（门槛全开 + 成交间隔 30 + 滑点 0.02），
# 因此有三个可用的对照，按可信度从高到低：
#
#   1) _bt_v6/off_*   vs _bt_v6/on_*      ← 熔断净效果。同一份代码、只差一个开关，
#                                            唯一真·单变量对照。
#   2) _bt_v6/off_*   vs _bt_v5/newstop_* ← **重构等价性检查**。两者语义上都是
#                                            「有止损、无熔断」，但中间做过了
#                                            `_sync_buy_signal_price` 改名与路由
#                                            调整 ⇒ 逐笔应完全一致，不一致说明重构
#                                            改变了行为。
#   3) _bt_v5/newstop_* vs _bt_v6/on_*    ← 熔断 + 重构的合计效果，仅供参考。
#
# 每组开跑前清一次 Redis 业务缓存（上一轮残留的 bar:/sr_points: 会被下一轮读到）。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v6

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
    out="_bt_v6/${tag}_${strat}_${key}.json"
    log="/tmp/btv6_${tag}_${strat}_${key}.log"
    if [ -s "${out}" ]; then
        echo "########## [${tag}] ${strat} / ${pool} -> ${out}  skip(已存在) @ $(date '+%H:%M:%S')"
        return 0
    fi
    echo "########## [${tag}] ${strat} / ${pool} -> ${out}  @ $(date '+%H:%M:%S')"
    python _bt_clear_redis.py
    if [ "$tag" = "off" ]; then
        extra="--no-intraday-breaker"
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

for tag in off on; do
    for strat in strong swing volume; do
        for key in holdings watch tech; do
            run_one "${tag}" "${strat}" "${key}"
        done
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
