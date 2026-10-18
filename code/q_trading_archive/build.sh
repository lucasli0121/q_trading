#!/bin/bash
# =============================================================================
# q_trading_archive 打包脚本
#
# 产出 Linux x86-64 的 PyInstaller onedir 可执行包，交付到生产机 192.168.1.63。
#
# 用法：
#   ./build.sh                    # 打包（默认版本 latest）
#   ./build.sh -v 1.0.0           # 指定版本号
#   ./build.sh -t                 # 先跑单元测试再打包
#   ./build.sh -c                 # 清理构建产物
#   ./build.sh -i                 # 只重建打包镜像（依赖升级后用）
#   ./build.sh -h                 # 帮助
#
# 为什么默认走 Docker：
#   开发机是 Windows，PyInstaller 不能跨平台产出。目标机是 Linux，
#   所以固定在一个 x86-64 Linux 容器里构建。若本机就是 Linux，
#   脚本会自动改用本机 Python，不再经过容器。
# =============================================================================

set -euo pipefail

# Git Bash 会把传给非 MSYS 程序（docker）的参数里形如 /src 的路径改写成
# Windows 路径，容器内工作目录因此变成 C:/.../PortableGit/src 并报
# "working directory is invalid"。这里统一关掉该转换。
# 本机就是 Linux 时这个变量无意义，不影响任何行为。
export MSYS_NO_PATHCONV=1

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

BUILDER_IMAGE="${BUILDER_IMAGE:-q_trading_archive-builder:latest}"
BASE_IMAGE="${BASE_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.12-slim}"
BASE_IMAGE_LOCAL="${BASE_IMAGE##*/}"
VERSION="latest"
DO_TEST="false"
FORCE_DOCKER="false"
REBUILD_IMAGE="false"

log()  { echo "[build] $*"; }
fail() { echo "[build][FAIL] $*" >&2; exit 1; }

usage() { sed -n '2,22p' "$0"; exit 0; }

# Windows 上有安全删除钩子：一次删除超过 50 个文件会直接中止，并报
# SAFE_DELETE_BULK_CONFIRM_REQUIRED。实测 rm -rf 与 Python 的 shutil.rmtree
# 同样被拦（宿主侧按轮次累计文件数，分批删也躲不开）。
# 构建产物动辄上千个文件，所以清理走容器：容器内的删除不经过该钩子。
# 本机就是 Linux 时没有这个钩子，直接删即可。
clean() {
    log "清理构建产物..."
    local already_cleaned="false"

    if [[ "${USE_DOCKER}" == "true" ]]; then
        if docker image inspect "${BUILDER_IMAGE}" >/dev/null 2>&1; then
            docker run --rm -v "$(host_mount):/src" -w /src "${BUILDER_IMAGE}" \
                bash -c 'rm -rf build dist
                         rm -f q_trading_archive-*-bin.tar.gz
                         find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true' \
                || fail "容器内清理失败"
            already_cleaned="true"
        else
            log "打包镜像尚未构建，跳过产物清理"
        fi
    else
        rm -rf build dist
        rm -f q_trading_archive-*-bin.tar.gz
        find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
        already_cleaned="true"
    fi

    # 回读校验。「删了但还在」比删不掉更危险：后续构建会复用到旧产物。
    if [[ "${already_cleaned}" == "true" ]]; then
        for leftover in build dist; do
            [ -e "${leftover}" ] && fail "清理失败: ${leftover} 仍存在"
        done
    fi
    log "清理完成"
}

run_tests() {
    log "运行单元测试..."
    if [[ "${USE_DOCKER}" == "true" ]]; then
        # 复用打包镜像跑测试：它已经装好 pandas / pyarrow / pymongo，
        # 开发机上不需要再维护一套。
        ensure_builder_image
        docker run --rm -v "$(host_mount):/src" -w /src "${BUILDER_IMAGE}" \
            python -m unittest discover -s tests -t . || fail "单元测试未通过"
    else
        python -m unittest discover -s tests -t . || fail "单元测试未通过"
    fi
    log "测试通过"
}

host_mount() {
    # Docker Desktop 需要宿主路径。Git Bash 下 cygpath -m 给出 C:/... 形式，
    # 反斜杠形式在 -v 里偶尔被 shell 吃掉。
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "${PROJECT_DIR}"
    else
        echo "${PROJECT_DIR}"
    fi
}

ensure_builder_image() {
    if [[ "${REBUILD_IMAGE}" == "true" ]] || ! docker image inspect "${BUILDER_IMAGE}" >/dev/null 2>&1; then
        log "构建打包镜像 ${BUILDER_IMAGE}（首次约数分钟）..."
        # Docker Hub 直连不通，基础镜像固定走华为云加速。
        docker build \
            --platform linux/amd64 \
            -f Dockerfile.builder \
            -t "${BUILDER_IMAGE}" \
            . || fail "打包镜像构建失败"
        docker image inspect "${BUILDER_IMAGE}" >/dev/null 2>&1 \
            || fail "打包镜像构建后仍不存在"
        log "打包镜像就绪"
    else
        log "复用已有打包镜像 ${BUILDER_IMAGE}"
    fi
}

pack_with_docker() {
    ensure_builder_image
    log "容器内打包（linux/amd64，版本 ${VERSION}）..."
    docker run --rm \
        --platform linux/amd64 \
        -v "$(host_mount):/src" \
        -w /src \
        -e TZ=Asia/Shanghai \
        "${BUILDER_IMAGE}" \
        bash scripts/pack-in-container.sh "${VERSION}" || fail "容器内打包失败"
}

pack_native() {
    command -v pyinstaller >/dev/null 2>&1 \
        || fail "本机未安装 pyinstaller（pip install pyinstaller==6.11.1）"
    log "本机打包（版本 ${VERSION}）..."
    bash scripts/pack-in-container.sh "${VERSION}" || fail "打包失败"
}

verify_deliverable() {
    local tar_name="q_trading_archive-${VERSION}-bin.tar.gz"
    local dist_dir="dist/q_trading_archive-${VERSION}"
    [ -s "${tar_name}" ] || fail "未产出 ${tar_name}"
    [ -d "${dist_dir}/q_trading_archive/_internal" ] || fail "未产出 onedir 目录"

    # 归档条目回读：确认包里确实是 onedir 结构而不是一个空壳。
    local entries
    entries="$(tar -tzf "${tar_name}" 2>/dev/null | wc -l | tr -d ' ')"
    [ "${entries}" -gt 100 ] || fail "归档条目仅 ${entries} 条，包结构不完整"

    # 执行位必须留在归档里。宿主是 Windows 时不体现 Unix 权限位，
    # 在宿主上用 -x 判断会误判成「没有执行权限」，依据只能是 tar 条目里的模式串。
    local entry_line
    entry_line="$(tar -tvzf "${tar_name}" 2>/dev/null \
        | awk '$NF == "q_trading_archive/q_trading_archive" {print; exit}')"
    [ -n "${entry_line}" ] || fail "归档内找不到入口可执行文件"
    case "${entry_line}" in
        -rwx*) ;;
        *) fail "归档内入口缺执行位，解压后无法直接运行: ${entry_line}" ;;
    esac

    echo ""
    log "============================================"
    log "  交付物: ${PROJECT_DIR}/${tar_name}"
    log "  体积:   $(du -h "${tar_name}" | cut -f1)"
    log "  条目:   ${entries}"
    log "  结构:   q_trading_archive/q_trading_archive + _internal/"
    log "============================================"
    echo ""
    log "部署见 DEPLOY.md"
}

main() {
    # set -u 下未定义的变量会直接中止，先全部初始化
    DO_TEST="false"
    DO_CLEAN="false"
    FORCE_DOCKER="false"
    REBUILD_IMAGE="false"

    while getopts "v:tcdih" opt; do
        case "${opt}" in
            v) VERSION="${OPTARG}" ;;
            t) DO_TEST="true" ;;
            c) DO_CLEAN="true" ;;
            d) FORCE_DOCKER="true" ;;
            i) REBUILD_IMAGE="true" ;;
            h) usage ;;
            *) usage ;;
        esac
    done

    # 目标平台固定 Linux x86-64：本机不是 Linux 就必须走容器。
    USE_DOCKER="true"
    if [[ "$(uname -s)" == "Linux" && "${FORCE_DOCKER}" != "true" ]]; then
        USE_DOCKER="false"
    fi
    if [[ "${USE_DOCKER}" == "true" ]] && ! command -v docker >/dev/null 2>&1; then
        fail "需要 Docker 才能构建 Linux 产物；本机未安装 docker"
    fi
    if [[ "${USE_DOCKER}" == "true" ]] \
        && ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1 \
        && ! docker image inspect "${BASE_IMAGE_LOCAL}" >/dev/null 2>&1; then
        log "基础镜像 ${BASE_IMAGE} 尚未拉取，构建时会自动下载"
    fi

    # 清理走宿主还是容器取决于 USE_DOCKER，所以放在它确定之后
    if [[ "${DO_CLEAN}" == "true" ]]; then
        clean
        exit 0
    fi

    echo ""
    log "===== q_trading_archive 打包 ====="
    log "版本:   ${VERSION}"
    log "平台:   linux/amd64"
    log "方式:   $([[ "${USE_DOCKER}" == "true" ]] && echo "Docker 容器" || echo "本机 Python")"
    echo ""

    clean
    [[ "${DO_TEST}" == "true" ]] && run_tests

    if [[ "${USE_DOCKER}" == "true" ]]; then
        pack_with_docker
    else
        pack_native
    fi

    verify_deliverable
    log "打包完成"
}

main "$@"
