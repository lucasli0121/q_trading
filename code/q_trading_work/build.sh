#!/bin/bash
# =============================================================================
# Q_trading_work 项目打包构建脚本
#
# 用法：
#   ./build.sh                    # 默认构建 Docker 镜像（latest）
#   ./build.sh -v 1.2.3           # 指定版本号构建
#   ./build.sh -v 1.2.3 -p        # 构建并推送到镜像仓库
#   ./build.sh -t dev             # 构建开发版本
#   ./build.sh -P                 # PyInstaller 打包为独立可执行文件
#   ./build.sh -l                 # 仅运行 lint 检查
#   ./build.sh -c                 # 仅清理构建产物
#   ./build.sh -h                 # 显示帮助
#
# Author: liguoqiang
# Date: 2026-07-19
# =============================================================================

set -euo pipefail

# ---- 配置（可按需修改） ----
IMAGE_NAME="${IMAGE_NAME:-q_trading_work}"
REGISTRY="${REGISTRY:-}"                        # 镜像仓库地址，留空则仅本地构建
DOCKERFILE="${DOCKERFILE:-Dockerfile}"
PLATFORM="${PLATFORM:-linux/amd64}"             # 构建目标平台
BUILD_TAG="${BUILD_TAG:-latest}"                # 默认 tag

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $*"; }

# ---- 使用说明 ----
usage() {
    sed -n '2,13p' "$0"
    exit 0
}

# ---- 清理构建产物 ----
clean() {
    log_step "清理构建产物..."
    rm -rf build/ dist/ *.egg-info/
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    log_info "清理完成"
}

# ---- Lint 检查 ----
lint() {
    log_step "运行代码检查..."
    if command -v ruff &>/dev/null; then
        if ruff check . 2>&1; then
            log_info "ruff check 通过"
        else
            log_warn "ruff check 存在警告（非阻断）"
        fi
    else
        log_warn "ruff 未安装，跳过 lint"
    fi
}

# ---- 单元测试 ----
run_tests() {
    log_step "运行单元测试..."
    if python -m pytest tests/ -v --tb=short 2>&1; then
        log_info "全部测试通过"
    else
        log_error "测试失败，终止构建"
        exit 1
    fi
}

# ---- 检查依赖 ----
check_deps() {
    local missing=()
    for cmd in docker; do
        if ! command -v "${cmd}" &>/dev/null; then
            missing+=("${cmd}")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "缺少依赖命令: ${missing[*]}"
        log_error "请安装 Docker 后重试，或使用 -S 选项打包源码 tar.gz"
        return 1
    fi
    return 0
}

# ---- Docker 镜像构建 ----
build_image() {
    local version="$1"
    local push="${2:-false}"

    # 计算完整镜像名
    local full_image="${IMAGE_NAME}:${version}"
    if [[ -n "${REGISTRY}" ]]; then
        full_image="${REGISTRY}/${full_image}"
    fi

    log_step "构建 Docker 镜像: ${full_image}"
    log_info "  Dockerfile: ${DOCKERFILE}"
    log_info "  Platform:   ${PLATFORM}"
    log_info "  Context:    $(pwd)"

    docker build \
        --platform "${PLATFORM}" \
        --file "${DOCKERFILE}" \
        --tag "${full_image}" \
        --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --build-arg VERSION="${version}" \
        .

    log_info "镜像构建成功: ${full_image}"

    # 同时打 latest 标签
    if [[ "${version}" != "latest" ]]; then
        local latest_image="${IMAGE_NAME}:latest"
        if [[ -n "${REGISTRY}" ]]; then
            latest_image="${REGISTRY}/${latest_image}"
        fi
        docker tag "${full_image}" "${latest_image}"
        log_info "已打 latest 标签: ${latest_image}"
    fi

    # 推送
    if [[ "${push}" == "true" ]]; then
        push_image "${version}"
    fi
}

# ---- 源码打包（无需 Docker） ----
package_source() {
    local version="$1"
    local tar_file="q_trading_work-${version}-src.tar.gz"
    # 输出到上级目录避免 tar 自身被包含
    local output_path="/tmp/${tar_file}"

    log_step "打包源码: ${tar_file}"
    tar -czf "${output_path}" \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.mypy_cache' \
        --exclude='.pytest_cache' \
        --exclude='.ruff_cache' \
        --exclude='.claude' \
        --exclude='.nicegui' \
        --exclude='log/*.log' \
        --exclude='.vscode' \
        --exclude='*.tar.gz' \
        --exclude='build' \
        --exclude='dist' \
        .

    mv "${output_path}" "${tar_file}"
    log_info "源码打包完成: ${tar_file} ($(du -h "${tar_file}" | cut -f1))"
}

# ---- PyInstaller 打包（独立可执行文件） ----
build_pyinstaller() {
    local version="$1"
    local dist_dir="dist/q_trading_work-${version}"
    local tar_file="q_trading_work-${version}-bin.tar.gz"

    if ! command -v pyinstaller &>/dev/null; then
        log_error "pyinstaller 未安装，请执行: pip install pyinstaller"
        exit 1
    fi

    log_step "PyInstaller 打包..."

    # 创建空 hook 覆盖，解决项目 workflow/ 目录与 pip workflow 包的 hook 冲突
    local hook_dir="/tmp/pyinstaller-hooks"
    mkdir -p "${hook_dir}"
    cat > "${hook_dir}/hook-workflow.py" << 'HOOKEOF'
# Override: disable pyinstaller-hooks-contrib hook-workflow.py
HOOKEOF

    pyinstaller \
        --distpath "${dist_dir}" \
        --workpath /tmp/pyinstaller-work \
        --noconfirm --clean \
        q_trading_work.spec 2>&1 | tail -20

    if [[ ! -d "${dist_dir}/q_trading_work" ]]; then
        log_error "PyInstaller 打包失败"
        exit 1
    fi

    log_info "可执行文件: ${dist_dir}/q_trading_work/q_trading_work"

    # 打包为 tar.gz
    log_step "压缩二进制包: ${tar_file}"
    (
        cd "${dist_dir}"
        tar -czf "/tmp/${tar_file}" q_trading_work/
    )
    mv "/tmp/${tar_file}" "${tar_file}"
    log_info "二进制打包完成: ${tar_file} ($(du -h "${tar_file}" | cut -f1))"
}

# ---- 推送镜像 ----
push_image() {
    local version="$1"
    local full_image="${IMAGE_NAME}:${version}"
    if [[ -n "${REGISTRY}" ]]; then
        full_image="${REGISTRY}/${full_image}"
    fi

    log_step "推送镜像: ${full_image}"
    docker push "${full_image}"
    log_info "推送完成: ${full_image}"

    if [[ "${version}" != "latest" ]]; then
        local latest_image="${REGISTRY:-}${REGISTRY:+/}${IMAGE_NAME}:latest"
        docker push "${latest_image}"
        log_info "推送完成: ${latest_image}"
    fi
}

# ---- 生成 tar 包（可选，用于离线部署） ----
package_tar() {
    local version="$1"
    local tar_file="q_trading_work-${version}.tar"
    local full_image="${IMAGE_NAME}:${version}"

    log_step "导出镜像为 tar 包..."
    docker save -o "${tar_file}" "${full_image}"
    log_info "导出完成: ${tar_file} ($(du -h "${tar_file}" | cut -f1))"

    # 压缩
    gzip -f "${tar_file}"
    log_info "压缩完成: ${tar_file}.gz ($(du -h "${tar_file}.gz" | cut -f1))"
}

# ---- 显示镜像信息 ----
show_info() {
    local version="$1"
    local full_image="${IMAGE_NAME}:${version}"

    echo ""
    log_info "===== 构建信息 ====="
    echo "  镜像:     ${full_image}"
    echo "  版本:     ${version}"
    echo "  时间:     $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  平台:     ${PLATFORM}"

    # 显示镜像大小
    local size
    size=$(docker images "${full_image}" --format "{{.Size}}" 2>/dev/null || echo "N/A")
    echo "  大小:     ${size}"
    echo ""
    echo "运行容器:"
    echo "  docker run -d -p 8085:8085 --name q_trading_work ${full_image}"
    echo ""
    echo "或使用 docker compose:"
    echo "  docker compose up -d"
}

# =============================================================================
# 主流程
# =============================================================================

main() {
    local version="${BUILD_TAG}"
    local push="false"
    local do_clean="false"
    local do_lint="false"
    local do_test="false"
    local do_tar="false"
    local do_source="false"
    local do_pyinstaller="false"
    local build_type="prod"

    # 解析参数
    while getopts "v:pt:lchTSP" opt; do
        case "${opt}" in
            v) version="${OPTARG}" ;;
            p) push="true" ;;
            t) build_type="${OPTARG}" ;;
            l) do_lint="true" ;;
            c) do_clean="true" ;;
            h) usage ;;
            T) do_tar="true" ;;
            S) do_source="true" ;;
            P) do_pyinstaller="true" ;;
            *) usage ;;
        esac
    done

    # 仅清理模式
    if [[ "${do_clean}" == "true" ]]; then
        clean
        exit 0
    fi

    # 仅 lint 模式
    if [[ "${do_lint}" == "true" ]]; then
        lint
        exit 0
    fi

    # 判断构建模式
    local mode="docker"
    if [[ "${do_pyinstaller}" == "true" ]]; then
        mode="pyinstaller"
    elif [[ "${do_source}" == "true" ]]; then
        mode="source"
    elif ! command -v docker &>/dev/null; then
        log_warn "未检测到 Docker，尝试 PyInstaller 打包"
        mode="pyinstaller"
    fi

    if [[ "${build_type}" == "dev" ]]; then
        version="${version}-dev"
    fi

    echo ""
    log_info "============================================"
    log_info "  Q_trading_work 项目构建"
    log_info "  版本:  ${version}"
    log_info "  模式:  ${mode}"
    log_info "============================================"
    echo ""

    local t0; t0=$(date +%s)
    clean
    lint

    if [[ "${do_test}" == "true" ]]; then
        run_tests
    fi

    case "${mode}" in
        docker)
            build_image "${version}" "${push}"
            [[ "${do_tar}" == "true" ]] && package_tar "${version}"
            show_info "${version}"
            ;;
        pyinstaller)
            build_pyinstaller "${version}"
            ;;
        source)
            package_source "${version}"
            ;;
    esac

    log_info "构建总耗时: $(($(date +%s) - t0))s"
    log_info "构建完成!"
}

main "$@"
