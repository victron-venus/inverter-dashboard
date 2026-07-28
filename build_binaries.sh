#!/usr/bin/env bash
# Cross-platform PyInstaller build for inverter-dashboard
# Builds: macOS (x86_64, arm64), Linux (x86_64), Windows (x86_64)
# Usage: ./build_binaries.sh [--local]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_ROOT="${SCRIPT_DIR}/build"
PYINSTALLER_VERSION="6.11.1"
PYINSTALLER_URL="https://github.com/pyinstaller/pyinstaller/releases/download/v${PYINSTALLER_VERSION}/pyinstaller-${PYINSTALLER_VERSION}.tar.gz"

LOCAL="${1:-}"

cleanup() {
    rm -rf "${BUILD_ROOT}"/venv
    rm -rf "${BUILD_ROOT}"/pyinstaller
}
trap cleanup EXIT

mkdir -p "${BUILD_ROOT}"

echo "=== Building Python venv ==="
python3 -m venv "${BUILD_ROOT}/venv"
# shellcheck source=/dev/null
source "${BUILD_ROOT}/venv/bin/activate"

pip install --quiet --only-binary :all: pyinstaller==${PYINSTALLER_VERSION}
pip install --quiet --only-binary :all: -r "${SCRIPT_DIR}/requirements.txt"

cd "${SCRIPT_DIR}"

echo "=== Building binaries ==="
# shellcheck disable=SC2046
PYTHON=$(pwd)/"${BUILD_ROOT}/venv/bin/python"

# --- macOS x86_64 ---
if [[ "$(uname)" == "Darwin" ]]; then
    echo ">>> macOS x86_64"
    MACOSX_DEPLOYMENT_TARGET=11.0 \
        CFLAGS="-arch x86_64" \
        LDFLAGS="-arch x86_64" \
        ${PYTHON} -m PyInstaller --target-arch x86_64 inverter-dashboard.spec \
        --distpath "${BUILD_ROOT}/dist/macos-x86_64" \
        --workpath "${BUILD_ROOT}/build/macos-x86_64"

    echo ">>> macOS arm64 (Apple Silicon)"
    MACOSX_DEPLOYMENT_TARGET=11.0 \
        CFLAGS="-arch arm64" \
        LDFLAGS="-arch arm64" \
        ${PYTHON} -m PyInstaller --target-arch arm64 inverter-dashboard.spec \
        --distpath "${BUILD_ROOT}/dist/macos-arm64" \
        --workpath "${BUILD_ROOT}/build/macos-arm64"
fi

# --- Linux x86_64 ---
echo ">>> Linux x86_64"
${PYTHON} -m PyInstaller --target-arch x86_64 inverter-dashboard.spec \
    --distpath "${BUILD_ROOT}/dist/linux-x86_64" \
    --workpath "${BUILD_ROOT}/build/linux-x86_64"

# --- Windows x86_64 ---
echo ">>> Windows x86_64"
if command -v wine &>/dev/null && command -v python3 &>/dev/null; then
    # Run PyInstaller through wine for Windows cross-compile
    # (requires wine and a Windows Python installed via wine)
    # Most CI will use dedicated Windows runners instead.
    echo "Skipping Windows (wine build not fully implemented — use Windows runner)"
elif [[ "$(uname)" == "Linux" || "$(uname)" == "Darwin" ]]; then
    echo "Note: Windows build requires a Windows runner or cross-compile toolchain"
fi

echo ""
echo "=== Build artifacts ==="
find "${BUILD_ROOT}/dist" -type f -name "inverter-dashboard*" 2>/dev/null || echo "No artifacts found"

# --- Create Windows zip using Python (cross-platform) ---
python3 << 'PYEOF'
import sys
import zipfile
import os
from pathlib import Path

build_root = Path("build/dist")
output = Path("build/assets")
output.mkdir(parents=True, exist_ok=True)

# Collect all binary artifacts
artifacts = {
    "linux": list(build_root.glob("linux-x86_64/inverter-dashboard")),
    "macos_x86_64": list(build_root.glob("macos-x86_64/inverter-dashboard")),
    "macos_arm64": list(build_root.glob("macos-arm64/inverter-dashboard")),
}

# Create zip for each platform
for platform, files in artifacts.items():
    if files:
        exe = files[0]
        zip_path = output / f"inverter-dashboard-{platform}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, exe.name)
        print(f"Created: {zip_path}")

print("Done.")
PYEOF
