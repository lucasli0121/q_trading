#!/bin/bash
# =============================================================================
# 冒烟：验证交付包里的常驻模式真的「不退出 + 每日定时执行」。
#
# 只跑 --self-check 证明不了这两点：自检是一瞬间的事，无法区分
# 「常驻成功」与「起来后立刻退出」。这里把调度时间设到 2 分钟后，
# 实地等到它自己触发一次，再验证 SIGTERM 能收干净。
#
# 全程在容器内 /tmp 解包运行，不触碰开发机的 dist/ 与真实 cfg。
#
# 用法（在开发机上）：bash scripts/smoke-daemon.sh <版本号>
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1

VERSION="${1:-1.0.0}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAR="${PROJECT_DIR}/q_trading_archive-${VERSION}-bin.tar.gz"
BASE_IMAGE="${BASE_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.12-slim}"

[ -f "${TAR}" ] || { echo "找不到交付包 ${TAR}，先跑 build.sh" >&2; exit 1; }
[ -f "${PROJECT_DIR}/cfg/stock.cfg" ] || { echo "缺 cfg/stock.cfg（真实凭据），无法连库" >&2; exit 1; }

docker run --rm \
    --platform linux/amd64 \
    -v "$(cygpath -m "${PROJECT_DIR}"):/src:ro" \
    -e TZ=Asia/Shanghai \
    -w /tmp \
    "${BASE_IMAGE}" \
    bash -c '
# 本块由外层单引号包裹，块内不得再出现单引号：会提前闭合外层引号，
# 把后面的内容甩到宿主 shell 上，症状是命令收到残缺参数（例如
# `sed: unterminated s command`），而脚本一路跑到下一步才停。
# 需要引号时一律用双引号，例如 sed "s/^/    /"。
set -euo pipefail

echo "=== 1. 解包（干净镜像，无任何第三方包）==="
mkdir -p app && tar -xzf /src/q_trading_archive-'"${VERSION}"'-bin.tar.gz -C app
cd app/q_trading_archive
ls -F | head
echo "入口权限: $(stat -c "%A" q_trading_archive)"
for s in run.sh stop.sh status.sh; do
    [ -x "${s}" ] || { echo "缺少可执行脚本 ${s}" >&2; exit 1; }
done
echo "运维脚本齐备"

echo
echo "=== 2. 准备配置（调度时间设为 2 分钟后，回溯窗口收窄）==="
mkdir -p cfg log
# 只改副本：源 cfg 是只读挂载，改不动也不该改
cp /src/cfg/stock.cfg cfg/stock.cfg

# 用 UTC+8 推算而不是依赖容器时区数据库：slim 镜像未必带 tzdata，
# `date -d "+2 minutes"` 会按 UTC 算，而调度器按 Asia/Shanghai 解释，
# 差 8 小时就永远等不到触发。
TARGET="$(date -u -d "+8 hours +2 minutes" +%H:%M)"
H="${TARGET%%:*}"; M="${TARGET##*:}"
H="${H#0}"; M="${M#0}"; H="${H:-0}"; M="${M:-0}"

# 真实 cfg 未必写了 [archive] 节（缺省即用默认值）。没有这节时，
# 下面的 sed 插入会无处落笔，调度时间就停在默认 15:30，测试白等到超时。
grep -q "^\[archive\]" cfg/stock.cfg || printf "\n[archive]\n" >> cfg/stock.cfg

for kv in "schedule_hour=${H}" "schedule_minute=${M}" "max_lookback_days=3"; do
    key="${kv%%=*}"
    if grep -q "^${key}[[:space:]]*=" cfg/stock.cfg; then
        sed -i "s|^${key}[[:space:]]*=.*|${kv}|" cfg/stock.cfg
    else
        sed -i "/^\[archive\]/a ${kv}" cfg/stock.cfg
    fi
done
echo "调度时间: ${TARGET}（容器 UTC 现在 $(date -u +%H:%M)）"
grep -A5 "^\[archive\]" cfg/stock.cfg | head -8

echo
echo "=== 3. bash run.sh ==="
bash run.sh
PID="$(cat q_trading_archive.pid)"
echo "PID 文件内容: ${PID}"

sleep 2
kill -0 "${PID}" 2>/dev/null || { echo "启动后进程即消失" >&2; cat log/daemon.out >&2; exit 1; }
echo "进程存活 ✓"

echo
echo "=== 4. 等首次补跑结束、进入常驻 ==="
for _ in $(seq 1 60); do
    grep -q "已进入常驻模式" log/daemon.out 2>/dev/null && break
    kill -0 "${PID}" 2>/dev/null || { echo "进程中途退出" >&2; cat log/daemon.out >&2; exit 1; }
    sleep 2
done
grep -q "已进入常驻模式" log/daemon.out || { echo "60 秒内未进入常驻模式" >&2; tail -20 log/daemon.out >&2; exit 1; }
grep "已进入常驻模式" log/daemon.out | tail -1

echo
echo "=== 5. 等到调度时间，确认自动触发 ==="
# 启动补跑也会归档，所以按日志来源区分：只有「定时触发」才证明调度生效。
# 数「计划归档」会误判，因为启动补跑那一次也会打印它。
count_trigger() {
    local n
    n="$(grep -cF "开始归档（定时触发）" log/daemon.out 2>/dev/null || true)"
    echo "${n:-0}"
}
BEFORE="$(count_trigger)"
echo "触发前「定时触发」次数: ${BEFORE}（应为 0）"
for _ in $(seq 1 100); do
    [ "$(count_trigger)" -gt "${BEFORE}" ] && break
    kill -0 "${PID}" 2>/dev/null || { echo "等待期间进程退出" >&2; exit 1; }
    sleep 3
done
AFTER="$(count_trigger)"
if [ "${AFTER}" -gt "${BEFORE}" ]; then
    echo "定时触发 ✓（次数 ${BEFORE} -> ${AFTER}）"
else
    echo "到点未触发 ✗" >&2
    grep "开始归档\|已进入常驻模式" log/daemon.out >&2
    exit 1
fi

echo
echo "=== 6. bash status.sh ==="
bash status.sh

echo
echo "=== 6b. 文件日志必须真的落盘 ==="
# 冻结态下 log.yaml 随包落在 _internal/cfg/。若程序只按包根查找，就会静默
# 降级成「仅控制台输出」：进程照常跑、日志照常打，但 log/archive.log 永不生成。
# 这条断言就是那个失效模式的守卫。
if [ ! -f log/archive.log ]; then
    echo "log/archive.log 未生成，日志配置未生效 ✗" >&2
    ls -l _internal/cfg/log.yaml >&2
    exit 1
fi
echo "log/archive.log 已生成 ✓"
head -2 log/archive.log | sed "s/^/    /"

echo
echo "=== 7. bash stop.sh（SIGTERM 收干净）==="
bash stop.sh
sleep 2
# 用 kill -0 判断原 PID 是否还在，不用 pgrep：镜像里可能没装 procps，
# 命令不存在会被误读成「没有残留」。
if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    echo "残留进程 ${PID} ✗" >&2
    exit 1
fi
if [ -f q_trading_archive.pid ]; then
    echo "PID 文件未清理 ✗" >&2
    exit 1
fi
echo "无残留进程 ✓"

echo "__SMOKE_OK__"
'
