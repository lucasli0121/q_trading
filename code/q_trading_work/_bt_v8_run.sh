#!/usr/bin/env bash
# 临时脚本：账户级回撤对照的「同时段」对照组。用完即删。
#
# 为什么需要它
# ------------
# `_bt_v6/on_*` 是 2026-10-14 12:12（**盘中**）跑的，`_bt_v7/nodraw_*` 是 20:41
# （**盘后**）跑的。两轮的选股结果就不一致（如 strong/holdings 的 08-11：
# 盘中选 3 只、盘后选 4 只，阀门日志完全相同），根因是 strong 选股用到了
# PE / 利润增长率这类**随行情更新的快照数据**，10-14 收盘后服务端更新了它们。
# 因此「v6 on vs v7 nodraw」混了「账户回撤开关」与「盘前盘后环境」两个变量，
# 不能作为单变量对照。
#
# 本脚本在同一时段（盘后，紧接 v7）重跑一次**不带** `--no-account-drawdown`
# 的口径，得到 `_bt_v8/on_*`，与 `_bt_v7/nodraw_*` 构成干净的单变量对照：
#
#     _bt_v8/on_*  vs  _bt_v7/nodraw_*   ← 账户级回撤的净效果（同环境）
#     _bt_v8/on_*  vs  _bt_v6/on_*       ← 跨时段环境差异的量级（应是同一组代码）
#
# 区间与其余口径与 v6 / v7 完全一致（门槛全开 + 间隔 30 + 滑点 0.02）。
set -u
cd /q_trading_work || exit 1
mkdir -p _bt_v8

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
        out="_bt_v8/on_${strat}_${key}.json"
        log="/tmp/btv8_on_${strat}_${key}.log"
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
            --verbose \
            --output "${out}" > "${log}" 2>&1
        echo "  exit=$? -> ${out}  @ $(date '+%H:%M:%S')"
    done
done
echo "########## ALL DONE @ $(date '+%H:%M:%S')"
