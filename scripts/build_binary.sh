#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUILD_VENV="${BUILD_VENV:-$ROOT/.venv-build}"
if [[ ! -d "$BUILD_VENV" ]]; then
  python3 -m venv "$BUILD_VENV"
fi
# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"

pip install -q -U pip
pip install -q -e .
pip install -q "pyinstaller>=6"

rm -rf build dist
pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name wslmetrics \
  --paths src \
  --collect-all textual \
  --collect-all plotext \
  --hidden-import wslmetrics \
  --hidden-import wslmetrics.__main__ \
  src/wslmetrics/__main__.py

echo
echo "Binary: $ROOT/dist/wslmetrics"
echo "Try:    $ROOT/dist/wslmetrics --help"
