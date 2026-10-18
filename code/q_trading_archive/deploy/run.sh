#!/bin/bash
# =============================================================================
# 常驻启动：进程不退出，每日按 cfg/stock.cfg 的 [archive] schedule_* 归档一次。
#
# 与 q_trading_work 的 run.sh 同一套做法：在子 shell 里后台启动。
#
# 括号不能省。`cmd &` 只是把进程挂到当前 shell 的作业表，它仍在同一个会话里，
# SSH 一断、shell 退出就会给作业发 SIGHUP，进程被中途杀掉；`(cmd &)` 把进程
# 放到子 shell 的作业表，父 shell 退出时它不在那份名单里，因此不受影响。
#
# 用法： bash run.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "$0")"

APP="./q_trading_archive"
PIDFILE="./q_trading_archive.pid"
OUT="./log/daemon.out"

[ -x "${APP}" ] || { echo "找不到可执行文件 ${APP}" >&2; exit 1; }
[ -f "./cfg/stock.cfg" ] || {
    echo "缺少 ./cfg/stock.cfg。先复制模板并填写：" >&2
    echo "  cp cfg/stock.cfg.example cfg/stock.cfg && vi cfg/stock.cfg" >&2
    exit 1
}

# 已在运行就不重复启动。两个实例会写同一天的文件，而原子写的临时名是固定的
# `<日期>.parquet.tmp`，互相踩会让 os.replace 报源文件不存在。
if [ -f "${PIDFILE}" ]; then
    OLD="$(cat "${PIDFILE}" 2>/dev/null || true)"
    if [ -n "${OLD}" ] && kill -0 "${OLD}" 2>/dev/null; then
        echo "已在运行，PID ${OLD}。如需重启先执行 bash stop.sh" >&2
        exit 1
    fi
fi

mkdir -p ./log

# 截断上一轮的启动输出，避免把历史报错当成这次的。
: >"${OUT}"
( "${APP}" --daemon >>"${OUT}" 2>&1 & echo $! >"${PIDFILE}" )

sleep 1
PID="$(cat "${PIDFILE}")"

# 启动即先补跑一次（见 main.run_daemon），首次部署会向前回溯，耗时以分钟计；
# 因此这里只确认进程活着、配置已被读到，不等待归档完成。
for _ in $(seq 1 15); do
    if ! kill -0 "${PID}" 2>/dev/null; then
        echo "启动失败：PID ${PID} 已退出。最近输出：" >&2
        tail -n 20 "${OUT}" >&2
        rm -f "${PIDFILE}"
        exit 1
    fi
    grep -q "归档目录" "${OUT}" 2>/dev/null && break
    sleep 1
done

if ! grep -q "归档目录" "${OUT}" 2>/dev/null; then
    echo "警告：15 秒内未见启动日志，进程仍活着（PID ${PID}），请查看 ${OUT}" >&2
    exit 1
fi

echo "已启动，PID ${PID}"
echo "  首次归档（启动补跑）进行中，完成后会打印「已进入常驻模式」"
echo "  日志：tail -f ./log/daemon.out  或  tail -f ./log/archive.log"
echo "  状态：bash status.sh    停止：bash stop.sh"
