#!/bin/bash
# =============================================================================
# 容器内打包步骤。由 build.sh 调用，不单独在开发机上跑。
#
# 设计取向：每一步都回读校验。`pyinstaller` 退出码为 0 不代表产物可用，
# 「打包成功」与「能在目标机跑起来」是两件事，中间隔着依赖收集是否完整。
#
# 用法（在容器内 /src）：bash scripts/pack-in-container.sh <version>
# =============================================================================

set -euo pipefail

VERSION="${1:-latest}"
SPEC="q_trading_archive.spec"
DIST_DIR="dist/q_trading_archive-${VERSION}"
APP="${DIST_DIR}/q_trading_archive"

log()  { echo "[$(date +%H:%M:%S)] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

# ---- 1. 运行依赖自检 ----
# 打包环境缺任何一个，产物都会在运行期才炸，而且往往炸在很深的调用栈里。
log "检查打包环境依赖..."
python - <<'PY'
import importlib
import sys

required = ["pandas", "pyarrow", "pymongo", "apscheduler", "yaml", "pytz"]
missing = []
for name in required:
    try:
        mod = importlib.import_module(name)
    except Exception as exc:                      # noqa: BLE001
        missing.append("%s (%s)" % (name, exc))
        continue
    print("  %-12s %s" % (name, getattr(mod, "__version__", "?")))
if missing:
    print("缺失依赖: %s" % ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("  依赖齐全")
PY

# ---- 2. 源码语法检查 ----
log "语法检查..."
python -m compileall -q main.py archive/ || fail "语法检查未通过"

# ---- 3. 打包 ----
log "PyInstaller 打包（onedir）..."
rm -rf "${DIST_DIR}" build
pyinstaller \
    --distpath "${DIST_DIR}" \
    --workpath build/pyinstaller \
    --noconfirm --clean \
    "${SPEC}" 2>&1 | tail -12

# ---- 4. 产物结构回读 ----
[ -f "${APP}/q_trading_archive" ] || fail "入口可执行文件不存在: ${APP}/q_trading_archive"
[ -d "${APP}/_internal" ] || fail "_internal 目录不存在，onedir 结构不完整"

# 魔数确认是 Linux ELF。拷错平台的产物在 Windows 上也能生成，
# 只有在目标机执行时才会报 "cannot execute binary file"。
MAGIC="$(head -c 4 "${APP}/q_trading_archive" | od -An -tx1 | tr -d ' \n')"
[ "${MAGIC}" = "7f454c46" ] || fail "入口不是 ELF（魔数 ${MAGIC}），平台不对"
log "入口为 Linux ELF，架构: $(file -b "${APP}/q_trading_archive" | cut -d, -f1-2)"

# 配置模板必须随包投放，否则部署时无从下手复制
for rel in "cfg/log.yaml" "cfg/stock.cfg.example"; do
    [ -f "${APP}/_internal/${rel}" ] || fail "模板缺失: _internal/${rel}"
done
log "cfg 模板已随包投放"

# ---- 4b. 运维脚本放包根 ----
# PyInstaller 的 datas 一律落到 _internal/，而 run.sh / stop.sh 要跟可执行文件
# 同级才能 `cd 包根 && bash run.sh`，所以只能在打包后复制。
for script in run.sh stop.sh status.sh; do
    [ -f "deploy/${script}" ] || fail "缺少 deploy/${script}"
    cp "deploy/${script}" "${APP}/${script}"
    chmod 755 "${APP}/${script}"
done
log "运维脚本已放入包根: run.sh stop.sh status.sh"

# ---- 5. 冒烟测试 ----
# 只启动不够：--self-check 会真实导入重依赖并落一个 parquet 再读回，
# 能覆盖「模块进了 PYZ 但原生库没被收集」这类只在调用时暴露的问题。
log "冒烟测试..."
SMOKE_OUT="${APP}/q_trading_archive --self-check"
if ! SMOKE_LOG="$(${SMOKE_OUT} 2>&1)"; then
    echo "${SMOKE_LOG}" >&2
    fail "自检退出码非 0"
fi
echo "${SMOKE_LOG}" | sed 's/^/    /'
# 断言输出内容。只看退出码会漏掉「启动失败但被 except 吞掉」的情况。
echo "${SMOKE_LOG}" | grep -q "自检通过" || fail "自检未打印通过标记，输出不可信"

# ---- 6. 记录 glibc 版本需求 ----
# 目标机 glibc 低于此版本时产物起不来。结尾 || true 不能省：
# readelf 对非 ELF 文件返回非 0，配合 set -e + pipefail 会中断整个构建。
log "扫描产物的 glibc 版本需求..."
REQUIRED="$(find "${APP}" -type f \( -name '*.so' -o -name '*.so.*' \) -print0 2>/dev/null \
    | xargs -0 -r readelf --version-info 2>/dev/null \
    | grep -oE 'GLIBC_[0-9]+(\.[0-9]+)*' \
    | sort -uV | tail -1 || true)"
echo "  最高需求: ${REQUIRED:-未检出}（目标机 glibc 需 >= 此版本）"

# ---- 7. 归档 ----
TAR_NAME="q_trading_archive-${VERSION}-bin.tar.gz"
log "压缩: ${TAR_NAME}"
rm -f "${TAR_NAME}"
# 不加 --hard-dereference：PyInstaller 复制 .so 时会复用 inode，
# dereference 会让每个硬链接各存一份，包体积接近翻倍。
tar -czf "${TAR_NAME}" -C "${DIST_DIR}" q_trading_archive
[ -s "${TAR_NAME}" ] || fail "tar 包为空"

SIZE_HUMAN="$(du -h "${TAR_NAME}" | cut -f1)"
DIR_HUMAN="$(du -sh "${APP}" | cut -f1)"
FILE_COUNT="$(find "${APP}" -type f | wc -l | tr -d ' ')"
log "完成: ${TAR_NAME}（${SIZE_HUMAN}），解压后 ${DIR_HUMAN}，${FILE_COUNT} 个文件"

echo "__BUILD_OK__"
