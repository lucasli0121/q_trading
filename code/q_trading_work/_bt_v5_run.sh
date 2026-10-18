#!/usr/bin/env bash
# 临时脚本：新止损（买入信号价连续 3 次）+ 量比 NaN 修复 的对比回测
# 区间 2026-07-21 09:30:00 ~ 2026-10-13 15:00:00，分钟频。用完即删。
#
# 两组（3 策略 × 3 池 = 9 组/组，共 18 组）：
#   newstop —— 改动后的代码：量比 NaN 拒绝 + 买入信号价止损（门槛全开 + 间隔 30 + 滑点 0.02）
#   nostop  —— 同上再加 --no-buy-signal-stop：新止损关掉
#
# 三组口径的对照关系（2026-10-14 修正）：
#   _bt_v4/base02_*  ← 旧代码：旧「自持仓最高点回撤」止损**在位**，NaN 未拦
#   _bt_v5/nostop_*  ← 旧止损已退役（代码路径被删，不是被开关关掉）+ NaN 拦 + 新止损关
#   _bt_v5/newstop_* ← 旧止损已退役 + NaN 拦 + 新止损开
#
# ⚠️ 因此**只有 nostop → newstop 是单变量**（新止损的净效果）。
#    base02 → nostop 混了「旧止损退役」与「NaN 修复」两个变量，不能当「只修 NaN」读。
#    另外强趋势策略既不用成交量因子、也没接新止损 ⇒ 它的三组结果必然逐笔全同。
#
# 每组开跑前清一次 Redis 业务缓存（上一轮残留的 bar:/sr_points: 会被下一轮读到）。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v5

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
    out="_bt_v5/${tag}_${strat}_${key}.json"
    log="/tmp/btv5_${tag}_${strat}_${key}.log"
    if [ -s "${out}" ]; then
        echo "########## [${tag}] ${strat} / ${pool} -> ${out}  skip(已存在) @ $(date '+%H:%M:%S')"
        return 0
    fi
    echo "########## [${tag}] ${strat} / ${pool} -> ${out}  @ $(date '+%H:%M:%S')"
    python _bt_clear_redis.py
    if [ "$tag" = "nostop" ]; then
        extra="--no-buy-signal-stop"
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

for tag in newstop nostop; do
    for strat in strong swing volume; do
        for key in holdings watch tech; do
            run_one "${tag}" "${strat}" "${key}"
        done
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
