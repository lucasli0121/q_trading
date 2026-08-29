#!/bin/bash
# ============================================================
# Author: liguoqiang
# Description: PyInstaller 打包脚本 - 将项目打包为可执行文件
#
# 用法:
#   chmod +x build.sh
#   ./build.sh              # 完整构建
#   ./build.sh --clean      # 清理后重新构建
#   ./build.sh --install    # 安装构建依赖
# ============================================================

set -euo pipefail
cd "$(dirname "$0")"

OUTPUT_NAME="q_trading_server"
SPEC_FILE="main.spec"
DIST_DIR="dist/${OUTPUT_NAME}"

# -------- 参数解析 --------
DO_CLEAN=false
DO_INSTALL=false

for arg in "$@"; do
    case "$arg" in
        --clean) DO_CLEAN=true ;;
        --install) DO_INSTALL=true ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

# -------- 安装依赖 --------
if $DO_INSTALL; then
    echo "==> 安装 PyInstaller ..."
    pip install pyinstaller>=6.0.0
    echo "==> 安装完成"
fi

# 检查 PyInstaller 是否可用
if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "!! PyInstaller 未安装，请运行: ./build.sh --install"
    exit 1
fi

# -------- 清理 --------
if $DO_CLEAN; then
    echo "==> 清理旧的构建产物 ..."
    rm -rf build dist "${OUTPUT_NAME}.spec.bak" 2>/dev/null || true
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
fi

# -------- 构建 --------
echo "==> 开始 PyInstaller 打包 ..."
pyinstaller "${SPEC_FILE}" --clean --noconfirm

    # -------- 打包为 tar.gz --------
    PACKAGE="${OUTPUT_NAME}.tar.gz"
    echo "==> 打包为 ${PACKAGE} ..."
    tar -czf "${PACKAGE}" -C dist "${OUTPUT_NAME}"
    echo "==> 打包完成: ${PACKAGE} ($(du -sh ${PACKAGE} | cut -f1))"

# -------- 检查结果 --------
if [ -f "${DIST_DIR}/${OUTPUT_NAME}" ]; then
    echo ""
    echo "============================================"
    echo "  构建成功!"
    echo "  可执行文件: ${DIST_DIR}/${OUTPUT_NAME}"
    echo "  输出目录:   ${DIST_DIR}/
echo "  压缩包:     ${PACKAGE}""
    echo "============================================"
    echo ""
    echo "运行方式:"
    echo "  cd ${DIST_DIR}"
    echo "  编辑 cfg/stock.cfg 配置数据库等信息"
    echo "部署方式:"
echo "  tar -xzf ${PACKAGE}"
echo "  cd ${OUTPUT_NAME}"
echo "  编辑 cfg/stock.cfg 配置数据库等信息"
echo "  ./${OUTPUT_NAME}"
    echo ""
    # 显示文件大小
    du -sh "${DIST_DIR}/${OUTPUT_NAME}" 2>/dev/null || true
else
    echo "!! 构建失败，请检查上方日志"
    exit 1
fi
