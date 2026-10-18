#!/usr/bin/env bash
# 临时脚本：账户级回撤的对照回测。用完即删。
# 区间 2026-07-21 09:30:00 ~ 2026-10-13 15:00:00，分钟频。
#
# 口径与 _bt_v6 的 on_* 完全一致，只差一个 --no-account-drawdown：
#
#   _bt_v7/nodraw_*  vs  _bt_v6/on_*   ← 账户级回撤的净效果，唯一真·单变量对照
#
# 两条路径的说明：分钟频只有**策略侧**一条（逐 bar 在卖出分上计入
# risk.drawdown_score）；引擎侧那条「达阈值当日收盘卖出全部在持仓位」
# （`_apply_drawdown_exit`）挂在**日频**路径上，因此 _bt_v6 的
# `drawdown_exit_trades` 九组全为 0，与本轮无关。
#
# strong 策略未设 RISK_MAX_DRAWDOWN_PCT（取基类默认 0.0 = 关闭），
# 因此它的三组应与 _bt_v6/on_* 逐笔一致，可用来验证开关没有误伤。
#
# 每组开跑前清一次 Redis 业务缓存（上一轮残留的 bar:/sr_points: 会被下一轮读到）。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v7

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

for strat in strong swing volume; do
    for key in holdings watch tech; do
        pool=$(pool_name "$key")
        out="_bt_v7/nodraw_${strat}_${key}.json"
        log="/tmp/btv7_nodraw_${strat}_${key}.log"
        if [ -s "${out}" ]; then
            echo "########## ${strat} / ${pool} -> ${out}  skip(已存在) @ $(date '+%H:%M:%S')"
            continue
        fi
        echo "########## ${strat} / ${pool} -> ${out}  @ $(date '+%H:%M:%S')"
        python _bt_clear_redis.py
        python -m backtest.main \
            --strategy "${strat}" \
            --pool-name "${pool}" \
            --start "${START}" \
            --end "${END}" \
            --frequency minute \
            --enable-daily-gates \
            --market-breadth-csv "${BREADTH}" \
            --min-trade-interval-minutes 30 \
            --no-account-drawdown \
            --verbose \
            --output "${out}" > "${log}" 2>&1
        echo "  exit=$? -> ${out}  @ $(date '+%H:%M:%S')"
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
