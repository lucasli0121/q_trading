#!/bin/bash
# ============================================================
# q_data_proxy - PyInstaller auto build script (Linux/macOS)
#
# Usage : ./build.sh
# Output: dist/q_data_proxy/q_data_proxy
#
# Steps :
#   1. cd to script dir (project root)
#   2. install dependencies from requirements.txt
#   3. ensure PyInstaller is installed
#   4. build with PyInstaller (main.spec)
#   5. compress dist/q_data_proxy/ -> dist/q_data_proxy.tar.gz
# ============================================================
set -e

# cd to script dir so relative paths in main.spec resolve
cd "$(dirname "$0")"

PYTHON="python3"
PIP="pip3"

# ---------- install dependencies ----------
echo "[INFO] installing dependencies from requirements.txt ..."
"$PIP" install -r requirements.txt --quiet
if [ $? -ne 0 ]; then
    echo "[ERROR] pip install failed"
    exit 1
fi

# ---------- ensure PyInstaller ----------
if ! "$PYTHON" -m PyInstaller --version > /dev/null 2>&1; then
    echo "[INFO] PyInstaller not installed, installing..."
    "$PIP" install pyinstaller --quiet
    if [ $? -ne 0 ]; then
        echo "[ERROR] PyInstaller install failed"
        exit 1
    fi
fi

# ---------- build ----------
echo "[INFO] building q_data_proxy ..."
"$PYTHON" -m PyInstaller main.spec --clean --noconfirm
if [ $? -ne 0 ]; then
    echo "[ERROR] build failed, see log above"
    exit 1
fi

# ---------- compress ----------
# -h: follow symlinks, store actual files instead (avoids symlink extraction errors)
TAR_FILE="dist/q_data_proxy.tar.gz"
echo ""
echo "[INFO] compressing to $TAR_FILE ..."
rm -f "$TAR_FILE"
tar -czhf "$TAR_FILE" -C dist q_data_proxy
if [ $? -ne 0 ]; then
    echo "[WARN] compression failed, but build artifacts are intact in dist/q_data_proxy/"
    echo "[OK] build done: dist/q_data_proxy/q_data_proxy"
    exit 1
fi

TAR_SIZE=$(stat -c%s "$TAR_FILE" 2>/dev/null || stat -f%z "$TAR_FILE" 2>/dev/null)
echo ""
echo "[OK] build done: dist/q_data_proxy/q_data_proxy"
echo "[OK]   tar done: $TAR_FILE ($TAR_SIZE bytes)"
exit 0
