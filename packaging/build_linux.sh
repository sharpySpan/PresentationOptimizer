#!/usr/bin/env bash
# Builds dist/Showrunner-linux-x86_64.tar.gz (app + install.sh). Run on Linux.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv .venv-build
source .venv-build/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

rm -rf build dist
pyinstaller --noconfirm --clean packaging/showrunner.spec

cp packaging/linux/install.sh packaging/linux/showrunner.desktop dist/Showrunner/
chmod +x dist/Showrunner/install.sh
tar -C dist -czf "dist/Showrunner-linux-$(uname -m).tar.gz" Showrunner
echo
echo "Built: dist/Showrunner-linux-$(uname -m).tar.gz"
