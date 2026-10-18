#!/bin/bash
# =============================================================================
# 查看常驻状态：进程、进度位、产物计数、最近日志。
#
# 用法： bash status.sh
# =============================================================================

cd "$(dirname "$0")"

PIDFILE="./q_trading_archive.pid"

echo "=== 进程 ==="
# 以 PID 文件为准，用 kill -0 判断存活。不用 pgrep：极简镜像里可能没装
# procps，命令不存在会被读成「没查到进程」，报出假的「无运行实例」。
PID="$(cat "${PIDFILE}" 2>/dev/null || true)"
if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    echo "运行中，PID ${PID}"
    ps -o pid=,etime=,args= -p "${PID}" 2>/dev/null || true
elif command -v pgrep >/dev/null 2>&1 \
    && pgrep -f "q_trading_archive --daemon" >/dev/null 2>&1; then
    echo "PID 文件与进程不一致，按命令行匹配到："
    pgrep -af "q_trading_archive --daemon"
else
    echo "无运行实例"
fi

echo "=== PID 文件 ==="
if [ -f "${PIDFILE}" ]; then
    printf "%s\n" "$(cat "${PIDFILE}")"
else
    echo "无"
fi

echo "=== 进度位 (.state.json) ==="
if [ -f "./.state.json" ]; then
    cat "./.state.json"
    echo ""
else
    echo "无（下次运行会从 max_lookback_days 向前回溯）"
fi

echo "=== 归档产物 ==="
MIN="$(find ./data/minute -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')"
DAY="$(find ./data/day -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')"
SIZE="$(du -sh ./data 2>/dev/null | cut -f1 || echo '?')"
echo "分钟 ${MIN} 个文件 / 日K ${DAY} 个文件 / 合计 ${SIZE}"

echo "=== 残留临时文件（应为空）==="
find ./data -name '*.tmp' 2>/dev/null | head -5
echo "（以上无输出即正常）"

echo "=== 最近归档日志 ==="
tail -n 6 ./log/archive.log 2>/dev/null || echo "无 log/archive.log"
