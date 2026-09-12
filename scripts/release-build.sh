#!/usr/bin/env bash
# Build local artifacts without publishing or deploying. Dependencies must be installed.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:?Usage: release-build.sh X.Y.Z [nightly|beta|rc]}"
CHANNEL="${2:-rc}"
python3 scripts/check-release-version.py "$VERSION" "$CHANNEL"
mkdir -p release-output
uv sync --python 3.13 --locked --group packaging --no-dev --no-install-project --no-build
uv run --no-sync --no-build python -m PyInstaller --clean --noconfirm inverter-dashboard.spec
EXECUTABLE=dist/inverter-dashboard
if [[ -f dist/inverter-dashboard.exe ]]; then EXECUTABLE=dist/inverter-dashboard.exe; fi
uv run --no-sync --no-build python scripts/check_frozen_binary.py "$EXECUTABLE"
uv run --no-sync --no-build python scripts/package_binary.py "$EXECUTABLE" "$(uname -s)-$(uname -m)"
cp release/*.zip release/*.sha256 release-output/
