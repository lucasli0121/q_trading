#!/bin/bash
# =============================================================================
# 停止常驻进程。
#
# 先 SIGTERM 而不是直接 SIGKILL：归档途中的文件靠原子写保护（写 .tmp 再
# os.replace），有序退出不会留下半截 parquet；强杀则可能留下一个 .tmp，
# 虽然也不会被下游当成完整数据，但下次运行要人工清理。
#
# 用法： bash stop.sh
# =============================================================================

cd "$(dirname "$0")"

PIDFILE="./q_trading_archive.pid"
PATTERN="q_trading_archive --daemon"

alive() { [ -n "${1}" ] && kill -0 "${1}" 2>/dev/null; }

if [ ! -f "${PIDFILE}" ]; then
    # 没有 PID 文件（上次是手工启动、或文件被误删）时按命令行兜底，
    # 与 q_trading_work 的 kill_pid.sh 行为一致。
    # pkill 不一定存在（极简镜像没装 procps），这种情况要给出可执行的下一步，
    # 不能因为「命令不存在」就报成「没有实例」。
    if command -v pkill >/dev/null 2>&1; then
        if pkill -f "${PATTERN}" 2>/dev/null; then
            echo "无 PID 文件，已按命令行匹配停止"
        else
            echo "没有在运行的实例"
        fi
    else
        echo "无 PID 文件，且系统没有 pkill。请手工确认后停止：" >&2
        echo "  ps -ef | grep q_trading_archive" >&2
        exit 1
    fi
    exit 0
fi

PID="$(cat "${PIDFILE}" 2>/dev/null || true)"

if ! alive "${PID}"; then
    echo "进程 ${PID} 已不在运行"
    rm -f "${PIDFILE}"
    exit 0
fi

kill "${PID}" 2>/dev/null || true

for _ in $(seq 1 20); do
    alive "${PID}" || break
    sleep 1
done

if alive "${PID}"; then
    echo "SIGTERM 20 秒未退出，改用 SIGKILL（PID ${PID}）"
    kill -9 "${PID}" 2>/dev/null || true
fi

rm -f "${PIDFILE}"
echo "已停止 PID ${PID}"
